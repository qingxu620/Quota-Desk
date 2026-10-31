from pathlib import Path

import pytest

from quota_desk import backend
from quota_desk.accounts import Provider
from quota_desk.config import Settings
from quota_desk.storage import AccountStore
from quota_desk.__main__ import main


def test_demo_does_not_access_credentials_accounts_or_network(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Demo accessed a live dependency")
    monkeypatch.setattr(Provider, "read_current", forbidden)
    monkeypatch.setattr(AccountStore, "slots", forbidden)
    monkeypatch.setattr(backend.urllib.request, "urlopen", forbidden)
    backend.configure(Settings(home=tmp_path), demo=True)
    data = backend.fetch_all_quotas()
    assert len(data["agy"]) == len(data["codex"]) == 3
    assert all(i["email"].endswith("@example.test") for key in ("agy", "codex") for i in data[key])
    with pytest.raises(ValueError):
        backend.reserve_account("codex", "disabled")
    assert not list(tmp_path.iterdir())


def test_demo_cli_and_diagnostics_do_not_leak_account_details(tmp_path, capsys):
    assert main(["--demo", "--data-dir", str(tmp_path), "--cli"]) == 0
    assert "离线演示数据" in capsys.readouterr().out
    assert main(["--demo", "--data-dir", str(tmp_path), "--diagnostics"]) == 0
    output = capsys.readouterr().out
    assert "@" not in output and "access_token" not in output and "refresh_token" not in output


def test_config_and_environment_data_path(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_DESK_HOME", str(tmp_path))
    assert Settings().home == tmp_path
    (tmp_path / "config.toml").write_text('[app]\nrefresh_seconds = 90\n', encoding="utf-8")
    assert Settings.load().refresh_seconds == 90
    (tmp_path / "config.toml").write_text('[app]\nrefresh_seconds = 0\n', encoding="utf-8")
    with pytest.raises(ValueError):
        Settings.load()
