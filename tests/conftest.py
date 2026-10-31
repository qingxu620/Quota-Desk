import base64
import json
from pathlib import Path

import pytest

from quota_desk import backend
from quota_desk.config import Settings
from quota_desk.storage import AccountStore, DpapiProtector


class TestProtector:
    """Test-only envelope. Production never selects this implementation."""
    def protect(self, payload):
        return base64.b64encode(b"TEST-ONLY:" + payload).decode()
    def unprotect(self, value):
        return base64.b64decode(value)[10:]


def token(claims):
    return "test." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".test"


def auth(provider, n):
    identity = token({"sub": f"test-{n}", "email": f"test{n}@example.test", "exp": 9999999999})
    if provider == "agy":
        return {"id_token": identity, "token": {"access_token": identity, "refresh_token": f"test-refresh-{n}",
                                                "expiry": "2286-11-20T17:46:39Z"}}
    return {"tokens": {"access_token": identity, "id_token": identity,
                       "refresh_token": f"test-refresh-{n}", "account_id": f"test-account-{n}"}}


@pytest.fixture
def app(tmp_path):
    settings = Settings(home=tmp_path, codex_auth_path=tmp_path / "client/auth.json")
    store = AccountStore(tmp_path, TestProtector())
    backend.configure(settings, store=store)
    yield backend
    backend.configure(Settings(home=tmp_path), demo=True)


def add(q, provider, n, label=None):
    return q.save_new_account(provider, auth(provider, n), label or f"测试账号 {n}")
