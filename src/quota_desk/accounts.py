"""Provider account adapters. Public metadata is separate from encrypted credentials."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

from .storage import CredentialError, write_json
from .windows import WindowsCredentials


def jwt_payload(value):
    try:
        return json.loads(base64.urlsafe_b64decode(value.split(".")[1] + "===").decode("utf-8"))
    except (ValueError, IndexError, AttributeError, UnicodeDecodeError):
        return {}


class Provider:
    def __init__(self, kind, settings, store, discover=True):
        self.kind, self.settings, self.store = kind, settings, store
        self.ACCOUNTS_DIR = str(store.directory(kind))
        self.CODEX_AUTH_PATH = str(settings.codex_auth_path)
        self.key = "cred" if kind == "agy" else "auth"
        self.credentials = WindowsCredentials()
        self.SLOT_ORDER, self.SHORTCUT_META = (), {}
        if discover:
            self.refresh_slots()

    def refresh_slots(self):
        self.SLOT_ORDER = self.store.slots(self.kind)
        self.SHORTCUT_META = {}
        for idx in self.SLOT_ORDER:
            path = Path(self.ACCOUNTS_DIR) / f"acc{idx}.json"
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                label = saved.get("alias") or f"账号 {idx}"
            except (OSError, ValueError, AttributeError):
                label = f"账号 {idx}"
            self.SHORTCUT_META[idx] = {"alias": label, "badge_title": label,
                                       "accent": "#A78BFA" if self.kind == "agy" else "#38BDF8"}

    def load_account_slot(self, idx):
        try:
            return self.store.load(self.kind, str(idx))
        except CredentialError as exc:
            return {"alias": self.SHORTCUT_META.get(str(idx), {}).get("alias", f"账号 {idx}"),
                    "email": "凭证不可用，请重新绑定", "credential_error": str(exc)}

    def is_valid_auth_obj(self, auth):
        if not isinstance(auth, dict):
            return False
        token = auth.get("token" if self.kind == "agy" else "tokens")
        return (isinstance(token, dict) and bool(token.get("access_token"))
                and bool(token.get("refresh_token")) and (self.kind != "agy" or bool(auth.get("id_token"))))

    @staticmethod
    def decode_id_token_claims(auth):
        return jwt_payload((auth or {}).get("id_token", ""))

    @staticmethod
    def get_jwt_payload(token):
        return jwt_payload(token)

    @staticmethod
    def parse_iso_expiry(value):
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
        except ValueError:
            return 0

    def metadata(self, auth):
        if not self.is_valid_auth_obj(auth):
            return "", "", ""
        if self.kind == "agy":
            c = self.decode_id_token_claims(auth)
            return c.get("sub", ""), c.get("email", ""), "Google AI Pro"
        tokens = auth["tokens"]
        access, identity = jwt_payload(tokens["access_token"]), jwt_payload(tokens.get("id_token", ""))
        claims = access.get("https://api.openai.com/auth") or identity.get("https://api.openai.com/auth") or {}
        return (tokens.get("account_id") or claims.get("chatgpt_account_id", ""),
                identity.get("email") or (access.get("https://api.openai.com/profile") or {}).get("email", ""),
                claims.get("chatgpt_plan_type", "Plus"))

    def extract_auth_metadata(self, auth):
        return self.metadata(auth)

    def same_identity(self, one, two):
        aid, email, _ = self.metadata(one)
        bid, other_email, _ = self.metadata(two)
        if aid and bid:
            return aid == bid
        return bool(email and other_email and email.casefold() == other_email.casefold())

    def read_current(self):
        if self.kind == "agy":
            return self.credentials.read()
        path = self.settings.codex_auth_path
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def read_wincred_auth(self):
        return self.credentials.read()

    def write_current(self, auth):
        if self.kind == "agy":
            self.credentials.write(auth)
            storage = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Antigravity/app_storage.json"
            if storage.exists():
                data = json.loads(storage.read_text(encoding="utf-8"))
                email = self.metadata(auth)[1]
                if email and data.get("jetski.onboarding.lastLoginUsername") != email:
                    data["jetski.onboarding.lastLoginUsername"] = email
                    write_json(storage, data)
        else:
            write_json(self.settings.codex_auth_path, auth)
            if self.read_current() != auth:
                raise OSError("客户端凭证写入读回不一致。")

    def clear_current(self):
        if self.kind == "agy":
            self.credentials.clear()
        elif self.settings.codex_auth_path.exists():
            self.settings.codex_auth_path.unlink()

    def write_wincred_auth(self, auth):
        self.credentials.write(auth)

    def get_current_active_idx(self):
        current = self.read_current()
        for idx in self.SLOT_ORDER:
            if self.same_identity(current, (self.load_account_slot(idx) or {}).get(self.key)):
                return idx
        return None

    def auto_save_current_auth(self, force_idx=None):
        current = self.read_current()
        if not self.is_valid_auth_obj(current):
            return None
        idx = str(force_idx) if force_idx is not None else self.get_current_active_idx()
        if idx is None:
            return None  # Adding an unfamiliar account is always an explicit action.
        existing = self.load_account_slot(idx) or {}
        identity, email, plan = self.metadata(current)
        if not email:
            return None
        payload = dict(slot=idx, alias=existing.get("alias") or email.split("@")[0],
                       email=email, plan=plan, **{self.key: current})
        if self.kind == "agy":
            payload["sub"] = identity
        if payload != existing:
            self.store.save_path(Path(self.ACCOUNTS_DIR) / f"acc{idx}.json", payload)
        return payload

    def write_json_atomic(self, path, data):
        path = Path(path)
        if path.parent.resolve() == Path(self.ACCOUNTS_DIR).resolve() and path.name.startswith("acc"):
            self.store.save_path(path, data)
        else:
            write_json(path, data)

    def sync_current_if_matches(self, auth):
        if self.same_identity(self.read_current(), auth):
            self.write_current(auth)

    @staticmethod
    def get_active_local_proxy_url():
        proxies = urllib.request.getproxies()
        return proxies.get("https") or proxies.get("http")

    def refresh_google_oauth_token(self, cred, min_remaining_sec=300):
        token = cred["token"]
        if self.parse_iso_expiry(token.get("expiry", "")) - time.time() >= min_remaining_sec:
            return True
        if not self.settings.google_client_id or not self.settings.google_client_secret:
            return False  # Never redistribute a third-party application's client secret.
        payload = urllib.parse.urlencode(dict(client_id=self.settings.google_client_id,
                    client_secret=self.settings.google_client_secret, refresh_token=token["refresh_token"],
                    grant_type="refresh_token")).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=payload,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                result = json.load(resp)
            token["access_token"] = result["access_token"]
            token["expiry"] = datetime.fromtimestamp(time.time() + int(result.get("expires_in", 3600)), timezone.utc).isoformat()
            if result.get("id_token"):
                cred["id_token"] = result["id_token"]
            return True
        except (OSError, ValueError, KeyError):
            return False

    def refresh_openai_token(self, tokens):
        payload = dict(grant_type="refresh_token", client_id=self.settings.openai_client_id,
                       refresh_token=tokens["refresh_token"])
        req = urllib.request.Request("https://auth.openai.com/oauth/token", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                result = json.load(resp)
            tokens["access_token"] = result["access_token"]
            for key in ("refresh_token", "id_token"):
                if result.get(key):
                    tokens[key] = result[key]
            return True, None
        except urllib.error.HTTPError as exc:
            return False, f"HTTP {exc.code}"
        except (OSError, ValueError, KeyError):
            return False, "无法刷新登录状态，请在客户端重新登录后保存当前账号。"
