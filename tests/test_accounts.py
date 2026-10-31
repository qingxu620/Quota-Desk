import json
import os
from pathlib import Path
import uuid

import pytest

from quota_desk.storage import AccountStore, DpapiProtector, CredentialError
from quota_desk.windows import WindowsCredentials
from conftest import add, auth


@pytest.mark.parametrize("provider", ["agy", "codex"])
def test_many_accounts_are_encrypted_and_rediscovered(app, provider):
    sw = app.provider_switcher(provider)
    for n in range(1, 9):
        assert add(app, provider, n) == str(n)
    sw.refresh_slots()
    assert len(sw.SLOT_ORDER) == 8
    path = Path(sw.ACCOUNTS_DIR) / "acc8.json"
    raw = path.read_text(encoding="utf-8")
    assert "test-refresh-8" not in raw
    assert "encrypted_auth" in json.loads(raw)
    key = "cred" if provider == "agy" else "auth"
    assert sw.load_account_slot("8")[key] == auth(provider, 8)


@pytest.mark.parametrize("provider", ["agy", "codex"])
def test_duplicate_and_invalid_auth_do_not_overwrite(app, provider):
    add(app, provider, 1)
    sw = app.provider_switcher(provider)
    path = Path(sw.ACCOUNTS_DIR) / "acc1.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="已在列表"):
        add(app, provider, 1)
    with pytest.raises(ValueError):
        app.save_new_account(provider, {}, "invalid")
    assert path.read_bytes() == before
    assert sw.SLOT_ORDER == ("1",)


@pytest.mark.parametrize("provider", ["agy", "codex"])
def test_save_current_updates_existing_identity(app, provider):
    add(app, provider, 1)
    updated = auth(provider, 1)
    key = "token" if provider == "agy" else "tokens"
    updated[key]["refresh_token"] = "test-rotated"
    assert app.save_current_account(provider, updated, "更新名称") == "1"
    sw = app.provider_switcher(provider)
    assert sw.load_account_slot("1")[sw.key] == updated
    assert sw.SHORTCUT_META["1"]["badge_title"] == "更新名称"
    assert sw.SLOT_ORDER == ("1",)


def test_unknown_login_is_not_automatically_assigned(app, monkeypatch):
    add(app, "codex", 1)
    sw = app.codex_sw
    monkeypatch.setattr(sw, "read_current", lambda: auth("codex", 2))
    assert sw.get_current_active_idx() is None
    assert sw.auto_save_current_auth() is None
    assert sw.SLOT_ORDER == ("1",)


def test_pending_slot_and_other_provider_are_isolated(app):
    assert app.reserve_account("agy", "等待登录") == "1"
    assert app.agy_sw.load_account_slot("1")["alias"] == "等待登录"
    assert app.codex_sw.SLOT_ORDER == ()
    assert app.agy_sw.get_current_active_idx is not None
    with pytest.raises(ValueError):
        app.provider_switcher("unknown")


def test_account_path_traversal_is_rejected(app):
    with pytest.raises(ValueError):
        app.STORE.load("codex", "../../auth")


def test_google_switch_syncs_client_email_without_losing_other_settings(app, monkeypatch, tmp_path):
    from unittest.mock import Mock
    monkeypatch.setenv("APPDATA", str(tmp_path))
    storage = tmp_path / "Antigravity/app_storage.json"
    storage.parent.mkdir()
    storage.write_text(json.dumps({"jetski.onboarding.lastLoginUsername": "old@example.test", "keep": 1}), encoding="utf-8")
    app.agy_sw.credentials = Mock()
    app.agy_sw.write_current(auth("agy", 1))
    result = json.loads(storage.read_text(encoding="utf-8"))
    assert result["jetski.onboarding.lastLoginUsername"] == "test1@example.test"
    assert result["keep"] == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI integration")
def test_real_dpapi_roundtrip(tmp_path):
    store = AccountStore(tmp_path, DpapiProtector())
    path = store.directory("codex") / "acc1.json"
    obj = auth("codex", 1)
    store.save_path(path, {"alias": "测试", "auth": obj})
    assert "test-refresh-1" not in path.read_text(encoding="utf-8")
    assert store.load("codex", "1")["auth"] == obj


@pytest.mark.skipif(os.name != "nt", reason="Windows Credential Manager integration")
def test_native_credential_roundtrip_uses_unique_test_target():
    credential = WindowsCredentials("QuotaDesk.Test." + uuid.uuid4().hex)
    try:
        assert credential.read() is None
        credential.write({"synthetic": "test-only"})
        assert credential.read() == {"synthetic": "test-only"}
    finally:
        credential.clear()
    assert credential.read() is None
