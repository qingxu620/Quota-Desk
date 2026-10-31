from types import SimpleNamespace
from unittest.mock import Mock

from quota_desk.ui.dashboard import QuotaDashboard


def test_timer_refreshes_both_pages_and_reschedules_at_configured_interval():
    app = SimpleNamespace(closed=False, pages={"agy": {}, "codex": {}},
                          q=SimpleNamespace(AUTO_REFRESH_MS=60000), root=Mock(), refresh=Mock())
    app.auto_refresh = lambda: QuotaDashboard.auto_refresh(app)
    app.auto_refresh()
    assert [call.args[0] for call in app.refresh.call_args_list] == ["agy", "codex"]
    assert app.root.after.call_args.args[0] == 60000


def test_closed_ui_does_not_schedule_more_work():
    app = SimpleNamespace(closed=True, root=Mock(), refresh=Mock())
    QuotaDashboard.auto_refresh(app)
    app.root.after.assert_not_called()
    app.refresh.assert_not_called()
