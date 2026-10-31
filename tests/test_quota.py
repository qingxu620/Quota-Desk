import time

from quota_desk import backend as q
from conftest import add


def raw():
    return dict(slot="1", alias="测试", accent="#64748B", bound=True, is_active=False,
                email="test@example.test", plan="TEST", error=None, buckets={})


def test_expired_history_is_unknown_without_destroying_original():
    cache = {"test@example.test": {"ts": 1, "buckets": {q.ARGON_BID: {"frac": .2, "reset": "2000-01-01T00:00:00Z"}}}}
    item = q.build_agy_item(raw(), None, cache)
    assert item["ring_pct"] is None
    assert "过期" in item["bars"][0]["reset"]
    assert cache["test@example.test"]["buckets"][q.ARGON_BID]["frac"] == .2


def test_local_quota_requires_matching_identity():
    local = {"email": "other@example.test", "buckets": {q.ARGON_BID: {"frac": .6, "reset": None}}}
    assert q.build_agy_item(raw(), local, {})["ring_pct"] is None
    local["email"] = "test@example.test"
    assert q.build_agy_item(raw(), local, {})["ring_pct"] == 60


def test_proto_omitted_fraction_means_zero():
    assert q.summary_to_buckets([{"buckets": [{"bucketId": "zero"}]}])["zero"]["frac"] == 0


def test_missing_codex_windows_are_unknown(app, monkeypatch):
    add(app, "codex", 1)
    monkeypatch.setattr(app, "http_open_json", lambda *a, **kw: {})
    item = app.fetch_codex_slot_quota("1", None)
    assert item["ring_pct"] is None
    assert all(b["pct"] is None for b in item["bars"])
    assert "未知" in item["sub_chips"][0][1]


def test_partial_codex_window_and_zero_usage(app, monkeypatch):
    add(app, "codex", 1)
    monkeypatch.setattr(app, "http_open_json", lambda *a, **kw: {"rate_limit": {"primary_window": {"used_percent": 0}}})
    item = app.fetch_codex_slot_quota("1", None)
    assert item["bars"][0]["pct"] == 100
    assert item["bars"][1]["pct"] is None
    assert item["ring_pct"] is None


def test_one_failed_slot_does_not_hide_other_accounts(app, monkeypatch):
    for n in (1, 2):
        add(app, "agy", n)
    monkeypatch.setattr(app.agy_sw, "read_current", lambda: None)
    monkeypatch.setattr(app, "fetch_local_ls_quota", lambda: None)
    def fetch(idx, active):
        if idx == "1":
            raise ValueError("synthetic failure")
        return dict(raw(), slot=idx)
    monkeypatch.setattr(app, "fetch_agy_remote", fetch)
    items = app.fetch_provider_quotas("agy")["agy"]
    assert len(items) == 2 and items[0]["error"] and items[1]["error"] is None
