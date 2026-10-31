"""Offline, deterministic demo data; no tokens, credential access, or network calls."""
from datetime import datetime
import time


def provider_data(provider):
    items = []
    for n, label in enumerate(("个人账号", "工作账号", "备用账号"), 1):
        active = n == (1 if provider == "agy" else 2)
        if provider == "agy":
            bars = [dict(label="◆ Gemini 4 Argon · 周限额（独立池）", pct=[68, 32, 91][n-1],
                         reset="2天04小时后重置" + ("" if active else " · 历史演示记录"), hl=True),
                    dict(split=[dict(label="Gemini Pro/Flash 5h", short="Gemini 5h", pct=80-n*8, reset="3小时12分"),
                                dict(label="Gemini Pro/Flash 周", short="Gemini 周", pct=90-n*7, reset="4天08小时")])]
            chips = [("neutral", "离线演示数据"), ("ok", "Claude/GPT 5h 76%"),
                     ("neutral", "Argon: 实时演示" if active else "Argon: 历史演示")]
            pct, ring = bars[0]["pct"], "ARGON 周"
        else:
            bars = [dict(label="◆ Codex · 5小时窗口", pct=92-n*11, reset="3小时12分后重置", hl=True),
                    dict(label="Codex · 7天周度总池", pct=90-n*17, reset="4天08小时后重置")]
            chips = [("neutral", "离线演示数据"), ("ok", "速率状态: 正常")]
            pct, ring = min(b["pct"] for b in bars), "CODEX"
        windows = [dict(id=str(index), label=bar["label"], pct=bar["pct"],
                        source="cache" if provider == "agy" and "Argon" in bar["label"] and not active else "live",
                        reset_at=time.time() + n * 3600 + index * 86400)
                   for index, bar in enumerate(b for group in bars for b in group.get("split", [group]))]
        items.append(dict(provider=provider, slot=str(n), alias=label, group=["个人", "工作", ""][n-1],
                          email=f"{provider}{n}@example.test", plan="DEMO", bound=True,
                          is_active=active, accent="#A78BFA" if provider == "agy" else "#38BDF8",
                          error=None, ring_pct=pct, ring_label=ring, bars=bars, sub_chips=chips,
                          quota_windows=windows))
    return {provider: items, "local_ok": False, "elapsed": 0.0,
            "timestamp": datetime.now().strftime("%H:%M:%S")}
