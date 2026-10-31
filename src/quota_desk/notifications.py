"""Transitions based on measured quotas; stale or failed reads never imply recovery."""
from .desktop import measured_windows


class QuotaMonitor:
    def __init__(self):
        self.quotas = {}
        self.login = set()

    def forget(self, provider, slot):
        self.quotas = {key: value for key, value in self.quotas.items() if key[:2] != (provider, slot)}
        self.login.discard((provider, slot))

    def observe(self, provider, items, settings):
        notices = []
        for item in items:
            slot = str(item["slot"])
            account_key = (provider, slot)
            prefix = f"{'Antigravity' if provider == 'agy' else 'Codex'} · {item['alias']}"
            error = str(item.get("error") or "")
            expired = any(signal in error for signal in ("HTTP 401", "invalid_grant", "凭证已过期", "登录已失效"))
            if expired:
                if account_key not in self.login and settings["login_expired"]:
                    notices.append(prefix + "：登录已失效，请重新登录。")
                self.login.add(account_key)
            elif not error and item.get("bound"):
                self.login.discard(account_key)
            for window in measured_windows(item):
                key = (provider, slot, window["id"])
                low = window["pct"] <= settings["threshold"]
                previous = self.quotas.get(key)
                if low and previous is not True and settings["low_quota"]:
                    notices.append(f"{prefix} · {window['label']}：剩余 {window['pct']:.1f}%。")
                elif not low and previous is True and settings["quota_recovered"]:
                    notices.append(f"{prefix} · {window['label']}：额度恢复至 {window['pct']:.1f}%。")
                self.quotas[key] = low
        return notices if settings["notifications"] else []
