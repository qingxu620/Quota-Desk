"""Plain native UI for the local account dashboard."""
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from ..commands import launch_operation

AUTO_REFRESH_MS = 60_000


class QuotaDashboard:
    def __init__(self, root, backend, provider='agy'):
        self.root, self.q = root, backend
        self.results = queue.Queue()
        self.pages = {}
        self.closed = False
        root.title('Quota Desk · 朴素版' + (' · 离线演示' if backend.DEMO else ''))
        scale = root.winfo_fpixels('1i') / 96
        root.minsize(round(780 * scale), round(580 * scale))
        root.configure(bg='#F4F5F6')
        icon = backend.SIMPLE_ICON_PATH
        if os.path.exists(icon):
            root.iconbitmap(icon)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#F4F5F6', foreground='#24292F')
        style.configure('TButton', padding=(12, 6))
        style.configure('TNotebook.Tab', padding=(22, 9))
        style.configure('Card.TFrame', background='white', borderwidth=1, relief='solid')
        style.configure('Card.TLabel', background='white')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 11, 'bold'), background='white')
        style.configure('Muted.TLabel', foreground='#626B75', background='white')
        style.configure('Quota.Horizontal.TProgressbar', troughcolor='#E9EDF1', background='#6585A3',
                        borderwidth=0, thickness=8)
        self.active_provider = provider
        style.configure('ActiveTab.TButton', background='#DCE7F0', foreground='#17466A')
        style.map('ActiveTab.TButton', background=[('active', '#DCE7F0')])
        navigation = ttk.Frame(root, padding=(16, 12, 16, 4))
        navigation.pack(fill='x')
        self.navigation_buttons = {}
        for key, label in [('agy', 'Antigravity'), ('codex', 'Codex')]:
            button = ttk.Button(navigation, text=label, command=lambda p=key: self.show_page(p))
            button.pack(side='left', padx=(0, 8))
            self.navigation_buttons[key] = button
        self.page_container = ttk.Frame(root)
        self.page_container.pack(fill='both', expand=True, padx=16, pady=(4, 8))
        self.page_container.rowconfigure(0, weight=1)
        self.page_container.columnconfigure(0, weight=1)
        for key, name in [('agy', 'Antigravity'), ('codex', 'Codex')]:
            page = ttk.Frame(self.page_container, padding=12)
            page.grid(row=0, column=0, sticky='nsew')
            ttk.Label(page, text=name + ' 额度', font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w', pady=(0, 10))
            toolbar = ttk.Frame(page)
            toolbar.pack(fill='x', pady=(0, 10))
            add = ttk.Button(toolbar, text='+ 新增账号', command=lambda p=key: self.open_add(p))
            add.pack(side='left')
            rotate = ttk.Button(toolbar, text='切换下一账号', command=lambda p=key: self.rotate(p))
            rotate.pack(side='left', padx=8)
            refresh = ttk.Button(toolbar, text='刷新额度  F5', command=lambda p=key: self.refresh(p))
            refresh.pack(side='right')
            status = tk.StringVar(value='尚未刷新')
            ttk.Label(page, textvariable=status, foreground='#626B75').pack(anchor='w', pady=(0, 10))
            area = ttk.Frame(page)
            area.pack(fill='both', expand=True)
            canvas = tk.Canvas(area, bg='#F4F5F6', highlightthickness=0)
            scrollbar = ttk.Scrollbar(area, orient='vertical', command=canvas.yview)
            canvas.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side='right', fill='y')
            canvas.pack(side='left', fill='both', expand=True)
            content = ttk.Frame(canvas)
            window = canvas.create_window((0, 0), window=content, anchor='nw')
            content.bind('<Configure>', lambda e, c=canvas: c.configure(scrollregion=c.bbox('all')))
            canvas.bind('<Configure>', lambda e, c=canvas, w=window: c.itemconfigure(w, width=e.width))
            self.pages[key] = dict(frame=page, content=content, canvas=canvas, data=None,
                                   refreshing=False, refresh_again=False, switching=False,
                                   status=status, buttons=[add, rotate, refresh])
            ttk.Label(content, text='打开此页后自动读取额度。', padding=20).pack(anchor='w')
        self.footer = ttk.Label(root, text=f'F5 刷新当前页 · 每 {backend.SETTINGS.refresh_seconds} 秒自动刷新两页', padding=(18, 0, 18, 10))
        self.footer.pack(fill='x')
        self.show_page(provider)
        root.bind('<F5>', lambda e: self.refresh(self.current_provider()))
        root.bind_all('<MouseWheel>', self.on_wheel)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(80, self.poll_results)
        root.after(200, self.warm_pages)
        root.after(backend.AUTO_REFRESH_MS, self.auto_refresh)

    def current_provider(self):
        return self.active_provider

    def show_page(self, provider):
        # Bind an explicit provider to each page; switching never fetches synchronously.
        self.active_provider = provider
        for key, state in self.pages.items():
            if key == provider:
                state['frame'].grid()
            else:
                state['frame'].grid_remove()
            self.navigation_buttons[key].configure(style='ActiveTab.TButton' if key == provider else 'TButton')
        if hasattr(self, 'footer'):
            self.footer.configure(text=('Argon：当前登录账号实时，其余账号为历史记录。过期记录显示未知。'
                                        if provider == 'agy' else 'Codex：分别显示 5 小时窗口和每周剩余额度。')
                                       + f'   ·   每 {self.q.SETTINGS.refresh_seconds} 秒自动刷新两页 · F5 刷新当前页')
        if self.pages[provider]['data'] is None and not self.pages[provider]['refreshing']:
            self.refresh(provider)

    def warm_pages(self):
        for provider, state in self.pages.items():
            if state['data'] is None and not state['refreshing']:
                self.refresh(provider)

    def on_wheel(self, event):
        if event.widget.winfo_toplevel() == self.root:
            self.pages[self.current_provider()]['canvas'].yview_scroll(-int(event.delta / 120), 'units')

    def set_busy(self, provider):
        state = self.pages[provider]
        busy = state['refreshing'] or state['switching']
        for button in state['buttons']:
            button.configure(state='disabled' if busy else 'normal')

    def refresh(self, provider):
        state = self.pages[provider]
        if state['refreshing'] or state['switching']:
            state['refresh_again'] = True
            return
        state['refresh_again'] = False
        state['refreshing'] = True
        state['status'].set('正在刷新额度…')
        self.set_busy(provider)

        def worker():
            try:
                result, error = self.q.fetch_provider_quotas(provider), None
            except Exception as ex:
                result, error = None, str(ex)[:160]
            self.results.put((provider, result, error))

        threading.Thread(target=worker, daemon=True).start()

    def poll_results(self):
        if self.closed:
            return
        while True:
            try:
                provider, data, error = self.results.get_nowait()
            except queue.Empty:
                break
            state = self.pages[provider]
            state['refreshing'] = False
            if data is not None:
                state['data'] = data
                self.render_page(provider)
                count = sum(i['bound'] for i in data[provider])
                state['status'].set(f"更新于 {data['timestamp']}  ·  {count} 个账号  ·  耗时 {data['elapsed']:.1f} 秒")
            else:
                state['status'].set(f'刷新失败：{error}；已有数据显示保持不变。')
            self.set_busy(provider)
            if state['refresh_again'] and not state['switching']:
                self.refresh(provider)
        self.root.after(80, self.poll_results)

    def render_page(self, provider):
        state = self.pages[provider]
        for widget in state['content'].winfo_children():
            widget.destroy()
        if not state['data'][provider]:
            ttk.Label(state['content'], text='还没有账号。点击“+ 新增账号”保存当前登录账号或导入账号文件。',
                      padding=20, wraplength=600).pack(anchor='w')
        for item in state['data'][provider]:
            card = ttk.Frame(state['content'], style='Card.TFrame', padding=16)
            card.pack(fill='x', pady=(0, 12), padx=(0, 8))
            head = ttk.Frame(card, style='Card.TFrame', borderwidth=0)
            head.pack(fill='x', pady=(0, 12))
            ttk.Label(head, text=item['alias'], style='Title.TLabel').pack(side='left')
            ttk.Label(head, text=f"   {item['plan']}   {item['email']}", style='Muted.TLabel').pack(side='left')
            action = '当前账号' if item['is_active'] else '切换' if item['bound'] else '登录绑定'
            ttk.Button(head, text=action, state='disabled' if item['is_active'] else 'normal',
                       command=lambda p=provider, i=item: self.handle_slot(p, i)).pack(side='right')
            table = ttk.Frame(card, style='Card.TFrame', borderwidth=0)
            table.pack(fill='x')
            table.columnconfigure(1, weight=1)
            row = 0
            for group in item['bars']:
                for bar in group.get('split', [group]):
                    label = bar['label'].replace('◆ ', '').replace('GPT-5 / Codex', 'Codex')
                    if provider == 'agy' and 'Argon' in label:
                        label = 'Gemini 4 Argon · 周'
                    elif provider == 'agy':
                        label = 'Gemini Pro/Flash · 5小时' if '5h' in label else 'Gemini Pro/Flash · 周'
                    ttk.Label(table, text=label, width=25, style='Card.TLabel').grid(row=row, column=0, sticky='w', pady=5)
                    pct = bar.get('pct')
                    ttk.Progressbar(table, style='Quota.Horizontal.TProgressbar', value=pct or 0,
                                    maximum=100).grid(row=row, column=1, sticky='ew', padx=(0, 10))
                    ttk.Label(table, text='未知' if pct is None else f'{pct:.1f}%', width=8,
                              anchor='e', style='Card.TLabel').grid(row=row, column=2, sticky='e')
                    ttk.Label(table, text=bar['reset'], width=28, wraplength=round(240 * self.root.winfo_fpixels('1i') / 96),
                              style='Muted.TLabel').grid(row=row, column=3, sticky='w', padx=(14, 0), pady=5)
                    row += 1
            notes = '   ·   '.join(text for _, text in item.get('sub_chips', []))
            ttk.Label(card, text=notes, style='Muted.TLabel', wraplength=round(950 * self.root.winfo_fpixels('1i') / 96)).pack(anchor='w', pady=(10, 0))

    def handle_slot(self, provider, item):
        if self.q.DEMO:
            messagebox.showinfo('离线演示', '演示模式展示界面和刷新，不操作真实客户端。', parent=self.root)
            return
        if item['is_active'] or self.pages[provider]['switching']:
            return
        if provider in ('agy', 'codex'):
            if not messagebox.askyesno('切换账号', '此操作会重启对应客户端以加载账号。是否继续？', parent=self.root):
                return
        self.launch_switch(provider, item['slot'], bind=not item['bound'])

    def rotate(self, provider):
        items = (self.pages[provider]['data'] or {}).get(provider, [])
        bound = [i for i in items if i['bound']]
        if len(bound) < 2:
            return
        active = next((n for n, i in enumerate(bound) if i['is_active']), -1)
        self.handle_slot(provider, bound[(active + 1) % len(bound)])

    def launch_switch(self, provider, slot, bind=False):
        if self.q.DEMO:
            return
        state = self.pages[provider]
        if state['switching']:
            return
        state['switching'] = True
        self.set_busy(provider)
        state['status'].set('正在等待登录完成…' if bind else '正在切换账号…')

        def start():
            if self.closed:
                return
            if state['refreshing']:
                self.root.after(100, start)
                return
            try:
                proc = launch_operation(self.q, provider, slot, bind=bind)
            except OSError as ex:
                finish(str(ex))
                return

            def poll():
                if self.closed:
                    return
                code = proc.poll()
                if code is None:
                    self.root.after(250, poll)
                else:
                    finish(None if code == 0 else self.q.operation_error(provider, proc.pid) or f'操作未完成（状态码 {code}）。请检查客户端是否已关闭。')
            poll()

        def finish(error):
            state['switching'] = False
            self.set_busy(provider)
            if error:
                messagebox.showerror('账号操作未完成', error, parent=self.root)
            self.refresh(provider)
            if provider == 'agy' and not error:
                self.root.after(8000, lambda: self.refresh(provider) if not self.closed else None)

        start()

    def open_add(self, provider):
        if self.q.DEMO:
            messagebox.showinfo('离线演示', '退出 --demo 后可以添加真实账号。', parent=self.root)
            return
        if self.pages[provider]['refreshing'] or self.pages[provider]['switching']:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('新增 Antigravity 账号' if provider == 'agy' else '新增 Codex 账号')
        dialog.transient(self.root)
        dialog.resizable(False, False)
        panel = ttk.Frame(dialog, padding=22)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='账号名称（可选）').pack(anchor='w')
        alias = tk.StringVar()
        entry = ttk.Entry(panel, textvariable=alias, width=55)
        entry.pack(fill='x', pady=(6, 16))
        mode = tk.StringVar(value='current')
        for value, label in [('current', '保存 / 更新当前登录账号'), ('import', '从账号 JSON 文件导入'),
                             ('login', '登录其他账号（会重启对应客户端）')]:
            ttk.Radiobutton(panel, text=label, value=value, variable=mode).pack(anchor='w', pady=5)
        ttk.Label(panel, text='导入支持原始登录凭证，或已有的 accN.json 账号文件。\n登录其他账号时，请在客户端或浏览器中自行完成登录。',
                  foreground='#626B75').pack(anchor='w', pady=(14, 18))

        def submit():
            try:
                if mode.get() == 'login':
                    slot = self.q.reserve_account(provider, alias.get())
                    dialog.destroy()
                    self.launch_switch(provider, slot, bind=True)
                    return
                if mode.get() == 'import':
                    filename = filedialog.askopenfilename(parent=dialog, title='选择账号文件',
                                                         filetypes=[('JSON 账号文件', '*.json')])
                    if not filename:
                        return
                    with open(filename, encoding='utf-8-sig') as f:
                        source = json.load(f)
                elif provider == 'agy':
                    source = self.q.agy_sw.read_wincred_auth()
                else:
                    with open(self.q.codex_sw.CODEX_AUTH_PATH, encoding='utf-8') as f:
                        source = json.load(f)
                if mode.get() == 'current':
                    self.q.save_current_account(provider, source, alias.get())
                else:
                    self.q.save_new_account(provider, source, alias.get())
            except (OSError, ValueError) as ex:
                messagebox.showerror('未添加账号', str(ex), parent=dialog)
                return
            dialog.destroy()
            self.refresh(provider)

        actions = ttk.Frame(panel)
        actions.pack(fill='x')
        ttk.Button(actions, text='添加', command=submit).pack(side='right')
        ttk.Button(actions, text='取消', command=dialog.destroy).pack(side='right', padx=8)
        entry.focus_set()
        dialog.grab_set()

    def auto_refresh(self):
        if not self.closed:
            for provider in self.pages:
                self.refresh(provider)
            self.root.after(self.q.AUTO_REFRESH_MS, self.auto_refresh)

    def close(self):
        self.closed = True
        self.root.destroy()
