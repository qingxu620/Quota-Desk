from unittest.mock import Mock

from quota_desk import backend
from quota_desk import __main__ as cli


def test_default_command_launches_the_single_formal_ui(monkeypatch, tmp_path):
    launch = Mock()
    monkeypatch.setattr(cli, "launch_gui", launch)
    assert cli.main(["--demo", "--data-dir", str(tmp_path)]) == 0
    launch.assert_called_once_with(backend)


def test_legacy_simple_flag_uses_the_same_ui(monkeypatch, tmp_path):
    launch = Mock()
    monkeypatch.setattr(cli, "launch_gui", launch)
    assert cli.main(["--demo", "--data-dir", str(tmp_path), "--simple"]) == 0
    launch.assert_called_once_with(backend)
