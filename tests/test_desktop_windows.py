import os
import queue
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("QUOTA_DESK_DESKTOP_TESTS") != "1",
    reason="Requires an interactive Windows desktop; set QUOTA_DESK_DESKTOP_TESTS=1",
)


def test_native_tray_lifecycle_and_dispatch(tmp_path):
    from quota_desk.backend import DASHBOARD_ICON_PATH
    from quota_desk.tray import CALLBACK, TrayIcon, reveal_existing
    events = queue.Queue()
    tray = TrayIcon(DASHBOARD_ICON_PATH, events.put, home=tmp_path)
    try:
        assert tray.start(), tray.error
        assert tray.active and tray.hwnd
        tray.user.PostMessageW(tray.hwnd, CALLBACK, 0, 0x203)
        assert events.get(timeout=2) == "show"
        assert reveal_existing(tmp_path)
        assert events.get(timeout=2) == "show"
    finally:
        tray.close()
    assert not tray.active and not tray.thread.is_alive()
    assert not reveal_existing(tmp_path)


def test_gui_search_manage_and_close_to_tray(app, monkeypatch):
    import tkinter as tk
    from tkinter import ttk
    from unittest.mock import Mock
    from conftest import add
    from quota_desk import demo
    from quota_desk.ui import dashboard

    for n in (1, 2, 3):
        add(app, "codex", n)
        add(app, "agy", n)
    monkeypatch.setattr(app, "fetch_provider_quotas", demo.provider_data)
    tray = Mock(active=True)
    monkeypatch.setattr(dashboard, "TrayIcon", lambda *args, **kwargs: tray)
    root = tk.Tk()
    root.withdraw()
    failures = []
    root.report_callback_exception = lambda *error: failures.append(error)
    ui = dashboard.QuotaDashboard(root, app)

    def pump(until):
        deadline = time.monotonic() + 3
        while not until() and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        assert until()
        assert not failures

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    try:
        pump(lambda: all(p["data"] is not None for p in ui.pages.values()))
        state = ui.pages["codex"]
        ui.show_page("codex")
        state["query"].set("codex2@")
        assert state["counts"].get() == "1 / 3 个账号"
        state["query"].set("")
        selected = state["data"]["codex"][1]
        ui.open_manage("codex", selected)
        dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        widgets = list(descendants(dialog))
        name = next(w for w in widgets if type(w) is ttk.Entry)
        name.delete(0, "end")
        name.insert(0, "新名称")
        group = next(w for w in widgets if isinstance(w, ttk.Combobox))
        group.set("测试分组")
        next(w for w in widgets if isinstance(w, ttk.Button) and w.cget("text") == "保存").invoke()
        pump(lambda: not state["editing"])
        assert selected["alias"] == "新名称" and selected["group"] == "测试分组"
        assert app.codex_sw.load_account_slot("2")["group"] == "测试分组"
        state["group"].set("分组：测试分组")
        ui.render_page("codex")
        assert state["counts"].get() == "1 / 3 个账号"
        monkeypatch.setattr(dashboard.messagebox, "askyesno", lambda *args, **kwargs: True)
        ui.open_manage("codex", selected)
        dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        next(w for w in descendants(dialog) if isinstance(w, ttk.Button) and w.cget("text") == "删除账号").invoke()
        pump(lambda: not state["editing"])
        assert app.codex_sw.SLOT_ORDER == ("1", "3")
        assert len(state["data"]["codex"]) == 2
        ui.open_preferences()
        dialog = ui.settings_dialog
        spinbox = next(w for w in descendants(dialog) if isinstance(w, ttk.Spinbox))
        spinbox.delete(0, "end")
        spinbox.insert(0, "15")
        next(w for w in descendants(dialog) if isinstance(w, ttk.Button) and w.cget("text") == "保存").invoke()
        assert ui.preferences.values["threshold"] == 15
        assert ui.preferences.path.exists()
        ui.close()
        assert root.state() == "withdrawn" and not ui.closed
        ui.show()
        assert root.state() == "normal"
    finally:
        ui.quit()
    tray.close.assert_called_once()
