"""Desktop preferences, account metadata, and filtering without network requests."""
import json
from pathlib import Path

from .storage import write_json
from .windows import operation_lock


SORT_OPTIONS = ("默认顺序", "额度从多到少", "额度从少到多", "最早重置", "名称")
DEFAULTS = dict(close_to_tray=True, notifications=True, low_quota=True,
                quota_recovered=True, login_expired=True, threshold=20)


class DesktopPreferences:
    def __init__(self, home, demo=False):
        self.path = Path(home) / "desktop.json"
        self.demo = demo
        self.values = dict(DEFAULTS)
        if not demo and self.path.exists():
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            self.values.update({k: doc[k] for k in DEFAULTS if k in doc})
        self.validate(self.values)

    @staticmethod
    def validate(values):
        if type(values["threshold"]) is not int or not 1 <= values["threshold"] <= 99:
            raise ValueError("提醒阈值必须是 1 到 99 的整数。")
        if any(type(values[k]) is not bool for k in DEFAULTS if k != "threshold"):
            raise ValueError("桌面设置中的开关必须为 true 或 false。")

    def save(self, values):
        values = {k: values[k] for k in DEFAULTS}
        self.validate(values)
        if not self.demo:
            write_json(self.path, values)
        self.values = values


class AccountManager:
    """Edit only public fields; never decrypt or rewrite a credential envelope."""
    def __init__(self, backend):
        self.q = backend

    def _path(self, provider, slot):
        if provider not in ("agy", "codex") or not str(slot).isascii() or not str(slot).isdigit() or int(slot) <= 0:
            raise ValueError("账号编号无效。")
        sw = self.q.provider_switcher(provider)
        return sw, Path(sw.ACCOUNTS_DIR) / f"acc{int(slot)}.json"

    def decorate(self, provider, items):
        if self.q.DEMO:
            return
        for item in items:
            _, path = self._path(provider, item["slot"])
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            item["alias"] = saved.get("alias") or item["alias"]
            item["group"] = saved.get("group", "")

    def update(self, provider, slot, alias, group):
        if self.q.DEMO:
            raise ValueError("演示模式不修改真实账号。")
        alias, group = alias.strip(), group.strip()
        if not alias or len(alias) > 80 or len(group) > 40:
            raise ValueError("名称需为 1–80 个字符，分组最多 40 个字符。")
        sw, path = self._path(provider, slot)
        with operation_lock(self.q.SETTINGS.home, provider):
            saved = json.loads(path.read_text(encoding="utf-8"))
            saved.update(alias=alias, group=group)
            write_json(path, saved)
            sw.refresh_slots()

    def delete(self, provider, slot):
        if self.q.DEMO:
            raise ValueError("演示模式不删除真实账号。")
        sw, path = self._path(provider, slot)
        with operation_lock(self.q.SETTINGS.home, provider):
            path.unlink()
            sw.refresh_slots()


def measured_windows(item):
    """Only current measurements take part in notifications or quota sorting."""
    return [w for w in item.get("quota_windows", [])
            if w.get("source") == "live" and w.get("pct") is not None]


def visible_accounts(items, query="", group=None, order="默认顺序"):
    query = query.strip().casefold()
    selected = [i for i in items
                if (group is None or i.get("group", "") == group)
                and (not query or query in " ".join(str(i.get(k, "")) for k in
                                                   ("alias", "email", "group", "plan")).casefold())]

    def key(item):
        slot = int(item["slot"])
        windows = measured_windows(item)
        if order in ("额度从多到少", "额度从少到多"):
            pct = min((w["pct"] for w in windows), default=None)
            return (pct is None, (-pct if order == "额度从多到少" else pct) if pct is not None else 0, slot)
        if order == "最早重置":
            reset = min((w["reset_at"] for w in windows if w.get("reset_at") is not None), default=None)
            return (reset is None, reset or 0, slot)
        if order == "名称":
            return (item["alias"].casefold(), slot)
        return (slot,)

    return sorted(selected, key=key)
