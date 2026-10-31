from pathlib import Path
from unittest.mock import Mock

import pytest

from quota_desk.operations import AccountOperations
from conftest import add, auth


def setup_operation(app, monkeypatch):
    add(app, "codex", 1)
    add(app, "codex", 2)
    sw = app.codex_sw
    current = {"auth": auth("codex", 1)}
    monkeypatch.setattr(sw, "read_current", lambda: current["auth"])
    writes = []
    def write(obj):
        current["auth"] = obj
        writes.append(obj)
    monkeypatch.setattr(sw, "write_current", write)
    monkeypatch.setattr(sw, "clear_current", lambda: current.update(auth=None))
    controller = Mock()
    controller.stop.return_value = True
    return AccountOperations(app, lambda *a: controller), controller, sw, current, writes


def test_switch_waits_for_normal_stop_and_verifies_target(app, monkeypatch):
    operations, controller, sw, current, writes = setup_operation(app, monkeypatch)
    operations.run("codex", "2")
    controller.stop.assert_called_once()
    assert writes == [auth("codex", 2)]
    controller.start.assert_called_once()
    assert not (app.SETTINGS.home / "recovery/codex.json").exists()


def test_failed_stop_never_changes_active_auth(app, monkeypatch):
    operations, controller, sw, current, writes = setup_operation(app, monkeypatch)
    controller.stop.side_effect = RuntimeError("still running")
    with pytest.raises(RuntimeError, match="still running"):
        operations.run("codex", "2")
    assert not writes
    assert current["auth"] == auth("codex", 1)
    controller.start.assert_not_called()


def test_failed_restart_rolls_back_previous_account(app, monkeypatch):
    operations, controller, sw, current, writes = setup_operation(app, monkeypatch)
    controller.start.side_effect = [OSError("synthetic start error"), None]
    with pytest.raises(OSError, match="synthetic start error"):
        operations.run("codex", "2")
    assert current["auth"] == auth("codex", 1)
    assert writes[-1] == auth("codex", 1)
    assert not (app.SETTINGS.home / "recovery/codex.json").exists()


def test_login_timeout_restores_previous_account(app, monkeypatch):
    operations, controller, sw, current, writes = setup_operation(app, monkeypatch)
    slot = app.reserve_account("codex", "新账号")
    with pytest.raises(TimeoutError):
        operations.run("codex", slot, bind=True, login_timeout=0)
    assert current["auth"] == auth("codex", 1)
    assert sw.load_account_slot(slot).get("auth") is None


def test_successful_new_login_preserves_custom_alias(app, monkeypatch):
    operations, controller, sw, current, writes = setup_operation(app, monkeypatch)
    slot = app.reserve_account("codex", "我的新账号")
    controller.start.side_effect = lambda: current.update(auth=auth("codex", 3))
    operations.run("codex", slot, bind=True, login_timeout=1)
    assert sw.load_account_slot(slot)["alias"] == "我的新账号"
    assert sw.load_account_slot(slot)["auth"] == auth("codex", 3)


def test_existing_account_login_does_not_duplicate(app, monkeypatch):
    operations, controller, sw, current, writes = setup_operation(app, monkeypatch)
    slot = app.reserve_account("codex", "重复登录")
    def start():
        if current["auth"] is None:
            current["auth"] = auth("codex", 2)
    controller.start.side_effect = start
    with pytest.raises(ValueError, match="已添加"):
        operations.run("codex", slot, bind=True, login_timeout=1)
    assert sw.load_account_slot(slot).get("auth") is None
    assert current["auth"] == auth("codex", 1)


def test_process_matching_does_not_target_unrelated_codex_or_python(app):
    from quota_desk.windows import ClientController
    controller = ClientController(app.SETTINGS, "codex")
    assert not controller._matches({"Name": "codex.exe", "ExecutablePath": r"C:\Tools\codex.exe"})
    assert not controller._matches({"Name": "python.exe", "ExecutablePath": r"C:\Python\python.exe"})
    assert controller._matches({"Name": "ChatGPT.exe", "ExecutablePath": r"C:\Program Files\WindowsApps\OpenAI.Codex_1\app\ChatGPT.exe"})
