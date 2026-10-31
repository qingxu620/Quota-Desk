"""Windows notification-area icon. All Tk actions are queued to the GUI thread."""
import ctypes
from ctypes import wintypes as wt
import hashlib
import os
from pathlib import Path
import queue
import threading


CALLBACK = 0x8001
UPDATE = 0x8002
SHOW = 0x8003
WM_CLOSE, WM_DESTROY = 0x10, 0x02


def window_class(home):
    digest = hashlib.sha256(str(Path(home).resolve()).casefold().encode()).hexdigest()[:20]
    return "QuotaDeskTray." + digest


def reveal_existing(home):
    """A second desktop launch opens the running dashboard instead of duplicating it."""
    if os.name != "nt":
        return False
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
    user.FindWindowW.restype = wt.HWND
    user.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user.PostMessageW.restype = wt.BOOL
    hwnd = user.FindWindowW(window_class(home), None)
    return bool(hwnd and user.PostMessageW(hwnd, SHOW, 0, 0))


class NotifyData(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
                ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                ("szInfo", wt.WCHAR * 256), ("uTimeout", wt.UINT), ("szInfoTitle", wt.WCHAR * 64),
                ("dwInfoFlags", wt.DWORD), ("guidItem", ctypes.c_ubyte * 16), ("hBalloonIcon", wt.HICON)]


class TrayIcon:
    def __init__(self, icon_path, dispatch, home=None):
        self.icon_path, self.dispatch = str(icon_path), dispatch
        self.active = False
        self.error = None
        self.hwnd = None
        self.pending = queue.Queue()
        self.ready = threading.Event()
        self.thread = None
        self.class_name = window_class(home) if home is not None else f"QuotaDeskTray.{os.getpid()}.{id(self)}"

    def start(self):
        if os.name != "nt":
            return False
        self.thread = threading.Thread(target=self._run, name="QuotaDeskTray", daemon=True)
        self.thread.start()
        self.ready.wait(2)
        return self.active

    def notify(self, title, body):
        if self.active:
            self.pending.put((title, body))
            self.user.PostMessageW(self.hwnd, UPDATE, 0, 0)

    def close(self):
        if self.hwnd:
            self.user.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self.thread:
            self.thread.join(timeout=2)

    def _apis(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [("style", wt.UINT), ("lpfnWndProc", self.proc_type), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

        self.WindowClass = WindowClass
        signatures = {
            "RegisterClassW": ([ctypes.POINTER(WindowClass)], wt.ATOM),
            "UnregisterClassW": ([wt.LPCWSTR, wt.HINSTANCE], wt.BOOL),
            "CreateWindowExW": ([wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID], wt.HWND),
            "DefWindowProcW": ([wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], ctypes.c_ssize_t),
            "PostMessageW": ([wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL),
            "DestroyWindow": ([wt.HWND], wt.BOOL),
            "GetMessageW": ([ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT], wt.BOOL),
            "TranslateMessage": ([ctypes.POINTER(wt.MSG)], wt.BOOL),
            "DispatchMessageW": ([ctypes.POINTER(wt.MSG)], ctypes.c_ssize_t),
            "PostQuitMessage": ([ctypes.c_int], None),
            "RegisterWindowMessageW": ([wt.LPCWSTR], wt.UINT),
            "LoadImageW": ([wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT], wt.HANDLE),
            "DestroyIcon": ([wt.HICON], wt.BOOL),
            "CreatePopupMenu": ([], wt.HMENU),
            "AppendMenuW": ([wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR], wt.BOOL),
            "DestroyMenu": ([wt.HMENU], wt.BOOL),
            "GetCursorPos": ([ctypes.POINTER(wt.POINT)], wt.BOOL),
            "SetForegroundWindow": ([wt.HWND], wt.BOOL),
            "TrackPopupMenu": ([wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               wt.HWND, ctypes.c_void_p], wt.UINT),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        self.kernel.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        self.kernel.GetModuleHandleW.restype = wt.HMODULE
        self.shell.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NotifyData)]
        self.shell.Shell_NotifyIconW.restype = wt.BOOL

    def _data(self, flags):
        data = NotifyData()
        data.cbSize, data.hWnd, data.uID, data.uFlags = ctypes.sizeof(data), self.hwnd, 1, flags
        return data

    def _add(self):
        data = self._data(1 | 2 | 4)
        data.uCallbackMessage, data.hIcon, data.szTip = CALLBACK, self.icon, "Quota Desk · 双击打开，右键菜单"
        self.active = bool(self.shell.Shell_NotifyIconW(0, ctypes.byref(data)))
        if not self.active:
            self.dispatch("tray_failed")

    def _menu(self):
        options = [("show", "打开看板"), ("agy", "Antigravity"), ("codex", "Codex"),
                   ("refresh", "刷新全部额度"), ("settings", "提醒与托盘设置"), ("quit", "退出 Quota Desk")]
        menu = self.user.CreatePopupMenu()
        try:
            for number, (_, label) in enumerate(options, 1):
                self.user.AppendMenuW(menu, 0, number, label)
            point = wt.POINT()
            self.user.GetCursorPos(ctypes.byref(point))
            self.user.SetForegroundWindow(self.hwnd)
            choice = self.user.TrackPopupMenu(menu, 0x100 | 0x02, point.x, point.y, 0, self.hwnd, None)
            self.user.PostMessageW(self.hwnd, 0, 0, 0)
            if choice:
                self.dispatch(options[choice - 1][0])
        finally:
            self.user.DestroyMenu(menu)

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == self.taskbar_created:
            self._add()
            return 0
        if message == CALLBACK:
            if lparam in (0x203, 0x405):  # Double-click or notification click.
                self.dispatch("show")
            elif lparam == 0x205:
                self._menu()
            return 0
        if message == SHOW:
            self.dispatch("show")
            return 0
        if message == UPDATE:
            try:
                title, body = self.pending.get_nowait()
            except queue.Empty:
                return 0
            data = self._data(0x10)
            data.szInfoTitle, data.szInfo = title[:63], body[:255]
            data.dwInfoFlags = 1 | 0x10 | 0x80  # Info, silent, respect Windows quiet time.
            self.shell.Shell_NotifyIconW(1, ctypes.byref(data))
            return 0
        if message == WM_CLOSE:
            self.user.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            self.shell.Shell_NotifyIconW(2, ctypes.byref(self._data(0)))
            self.active, self.hwnd = False, None
            self.user.PostQuitMessage(0)
            return 0
        return self.user.DefWindowProcW(hwnd, message, wparam, lparam)

    def _run(self):
        icon, instance, name = None, None, self.class_name
        try:
            self._apis()
            instance = self.kernel.GetModuleHandleW(None)
            self.taskbar_created = self.user.RegisterWindowMessageW("TaskbarCreated")
            self.callback = self.proc_type(self._window_proc)
            window_class = self.WindowClass()
            window_class.lpfnWndProc, window_class.hInstance = self.callback, instance
            window_class.lpszClassName = name
            if not self.user.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.hwnd = self.user.CreateWindowExW(0, name, name, 0, 0, 0, 0, 0, None, None, instance, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            icon = self.user.LoadImageW(None, self.icon_path, 1, 16, 16, 0x10)
            if not icon:
                raise ctypes.WinError(ctypes.get_last_error())
            self.icon = icon
            self._add()
            self.ready.set()
            message = wt.MSG()
            while self.user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                self.user.TranslateMessage(ctypes.byref(message))
                self.user.DispatchMessageW(ctypes.byref(message))
        except (OSError, AttributeError) as error:
            self.error = str(error)
            self.dispatch("tray_failed")
        finally:
            if self.hwnd:
                self.user.DestroyWindow(self.hwnd)
            if icon:
                self.user.DestroyIcon(icon)
            if instance:
                self.user.UnregisterClassW(name, instance)
            self.ready.set()
