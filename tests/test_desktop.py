import json

import pytest

from conftest import add, auth
from quota_desk.desktop import AccountManager, DesktopPreferences, visible_accounts
from quota_desk.notifications import QuotaMonitor


def item(slot="1", pct=50, source="live", reset=1000, **values):
    return dict(slot=slot, alias="工作账号 " + slot, email=slot + "@example.test",
                group="工作", bound=True, error=None,
                quota_windows=[dict(id="weekly", label="周额度", pct=pct, source=source, reset_at=reset)],
                **values)


@pytest.mark.parametrize("provider", ["agy", "codex"])
def test_metadata_edit_keeps_encrypted_envelope_and_survives_sync(app, provider, monkeypatch):
    add(app, provider, 1)
    sw = app.provider_switcher(provider)
    path = app.STORE.directory(provider) / "acc1.json"
    encrypted = json.loads(path.read_text(encoding="utf-8"))["encrypted_auth"]
    AccountManager(app).update(provider, "1", "  我的工作号  ", "  工作  ")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["encrypted_auth"] == encrypted
    monkeypatch.setattr(sw, "read_current", lambda: auth(provider, 1))
    sw.auto_save_current_auth()
    assert sw.load_account_slot("1")["group"] == "工作"
    assert sw.load_account_slot("1")["alias"] == "我的工作号"
    assert sw.load_account_slot("1")[sw.key] == auth(provider, 1)


def test_delete_removes_only_selected_saved_account(app):
    add(app, "codex", 1)
    add(app, "codex", 2)
    add(app, "agy", 1)
    current = app.SETTINGS.codex_auth_path
    current.parent.mkdir()
    current.write_text(json.dumps(auth("codex", 1)), encoding="utf-8")
    original = current.read_bytes()
    other = (app.STORE.directory("codex") / "acc2.json").read_bytes()
    manager = AccountManager(app)
    manager.delete("codex", "1")
    assert app.codex_sw.SLOT_ORDER == ("2",)
    assert (app.STORE.directory("codex") / "acc2.json").read_bytes() == other
    assert current.read_bytes() == original
    assert app.agy_sw.load_account_slot("1")["cred"] == auth("agy", 1)
    with pytest.raises(ValueError):
        manager.delete("codex", "../../auth")


def test_search_group_and_live_quota_sort():
    items = [item("1", 40, reset=3000), item("2", 90, reset=2000),
             item("3", 1, source="cache", reset=1), item("4", None)]
    items[1]["group"] = "个人"
    assert [i["slot"] for i in visible_accounts(items, query="2@EXAMPLE", group="个人")] == ["2"]
    assert visible_accounts(items, query="不存在") == []
    assert [i["slot"] for i in visible_accounts(items, order="额度从多到少")] == ["2", "1", "3", "4"]
    assert [i["slot"] for i in visible_accounts(items, order="额度从少到多")] == ["1", "2", "3", "4"]
    assert [i["slot"] for i in visible_accounts(items, order="最早重置")] == ["2", "1", "3", "4"]


def test_preferences_persist_and_demo_does_not_read_or_write(tmp_path):
    prefs = DesktopPreferences(tmp_path)
    prefs.save(dict(prefs.values, threshold=15, login_expired=False))
    assert DesktopPreferences(tmp_path).values["threshold"] == 15
    before = prefs.path.read_bytes()
    demo = DesktopPreferences(tmp_path, demo=True)
    assert demo.values["threshold"] == 20
    demo.save(dict(demo.values, threshold=50))
    assert prefs.path.read_bytes() == before
    with pytest.raises(ValueError):
        prefs.save(dict(prefs.values, threshold=0))


def test_low_and_recovered_notifications_only_on_measured_transitions(tmp_path):
    prefs = DesktopPreferences(tmp_path).values
    monitor = QuotaMonitor()
    assert len(monitor.observe("codex", [item(pct=20)], prefs)) == 1
    assert monitor.observe("codex", [item(pct=10)], prefs) == []
    assert monitor.observe("codex", [item(pct=100, source="cache")], prefs) == []
    failed = item(pct=None)
    failed["error"] = "HTTP 503"
    assert monitor.observe("codex", [failed], prefs) == []
    assert "恢复" in monitor.observe("codex", [item(pct=21)], prefs)[0]
    assert monitor.observe("codex", [item(pct=50)], prefs) == []
    assert len(monitor.observe("codex", [item(pct=0)], prefs)) == 1
    monitor.forget("codex", "1")
    assert monitor.observe("codex", [item(pct=50)], prefs) == []


def test_login_failure_dedup_and_network_errors_are_not_expiry(tmp_path):
    prefs = DesktopPreferences(tmp_path).values
    monitor = QuotaMonitor()
    expired = item(pct=None)
    expired["error"] = "HTTP 401"
    assert "登录" in monitor.observe("agy", [expired], prefs)[0]
    assert monitor.observe("agy", [expired], prefs) == []
    for error in ("HTTP 403", "HTTP 429", "HTTP 503", "timeout"):
        failed = item(pct=None)
        failed["error"] = error
        assert monitor.observe("agy", [failed], prefs) == []
    monitor.observe("agy", [item()], prefs)
    assert len(monitor.observe("agy", [expired], prefs)) == 1


def test_notification_switches_and_provider_isolation(tmp_path):
    prefs = DesktopPreferences(tmp_path).values
    monitor = QuotaMonitor()
    assert monitor.observe("agy", [item(pct=5)], dict(prefs, notifications=False)) == []
    assert len(monitor.observe("codex", [item(pct=5)], prefs)) == 1
    assert monitor.observe("codex", [item(pct=50)], dict(prefs, quota_recovered=False)) == []
