import os
import re
import sys
import json
import time
import base64
import threading
import queue
import subprocess
import urllib.request
import urllib.error
import ctypes
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from functools import wraps
from pathlib import Path
from .accounts import Provider
from .config import Settings
from .storage import AccountStore
from .windows import operation_lock

ASSETS_DIR = Path(__file__).parent / "assets"
DASHBOARD_ICON_PATH = str(ASSETS_DIR / "quota_desk.ico")
PYTHONW_EXE = str(Path(sys.executable).with_name("pythonw.exe"))
if not Path(PYTHONW_EXE).exists():
    PYTHONW_EXE = sys.executable
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)
DEMO = False


def configure(settings=None, demo=False, store=None):
    global SETTINGS, DEMO, STORE, agy_sw, codex_sw, AGY_DIR, CODEX_DIR, ARGON_CACHE_PATH, AUTO_REFRESH_MS
    SETTINGS, DEMO = settings or Settings.load(), demo
    STORE = store or AccountStore(SETTINGS.home)
    agy_sw, codex_sw = Provider("agy", SETTINGS, STORE, discover=not demo), Provider("codex", SETTINGS, STORE, discover=not demo)
    AGY_DIR, CODEX_DIR = agy_sw.ACCOUNTS_DIR, codex_sw.ACCOUNTS_DIR
    ARGON_CACHE_PATH = str(SETTINGS.home / "argon_quota_cache.json")
    AUTO_REFRESH_MS = SETTINGS.refresh_seconds * 1000


def provider_operation(function):
    @wraps(function)
    def locked(provider, *args, **kwargs):
        if provider not in ("agy", "codex"):
            raise ValueError("未知服务。")
        if DEMO:
            return function(provider, *args, **kwargs)
        with operation_lock(SETTINGS.home, provider):
            return function(provider, *args, **kwargs)
    return locked


# Official Antigravity Daily CloudCode endpoint (returns Gemini + Claude/GPT groups)
AGY_QUOTA_SUMMARY_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"

# Gemini 4 Argon has its own weekly bucket, only exposed by the local Antigravity client
ARGON_BID = "gemini-argon-wtu-weekly"
LS_LOG_PATH = os.path.join(os.environ.get("APPDATA", ""), "Antigravity", "logs", "language_server.log")

configure(Settings(), demo=True)

_PROXY_URL_CACHE = {"checked": False, "url": None}


# ============================================================================
# Networking helpers
# ============================================================================

def http_open_json(req, timeout=6.5, retries=0):
    """HTTP JSON request using the system proxy; authenticated errors never include response bodies."""
    if not _PROXY_URL_CACHE["checked"]:
        try:
            _PROXY_URL_CACHE["url"] = agy_sw.get_active_local_proxy_url()
        except Exception:
            _PROXY_URL_CACHE["url"] = None
        _PROXY_URL_CACHE["checked"] = True

    proxy_url = _PROXY_URL_CACHE["url"]
    last_err = None
    for attempt in range(retries + 1):
        try:
            if proxy_url:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
                )
            else:
                opener = urllib.request.build_opener()
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.15 * (attempt + 1))
    raise last_err


def format_duration_seconds(seconds):
    """Format remaining seconds into concise countdown string."""
    if seconds is None:
        return "未知"
    try:
        sec = int(float(seconds))
    except Exception:
        return "未知"
    if sec <= 0:
        return "即将重置"
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}天{hours:02d}小时后重置"
    if hours > 0:
        return f"{hours}小时{mins:02d}分后重置"
    return f"{max(1, mins)}分钟后重置"


def iso_to_epoch(iso_str):
    if not iso_str:
        return None
    try:
        s = str(iso_str).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def format_iso_reset(iso_str):
    """Parse ISO8601 UTC timestamp and return countdown string."""
    ep = iso_to_epoch(iso_str)
    if ep is None:
        return "充裕" if not iso_str else str(iso_str)
    return format_duration_seconds(ep - time.time())


def summary_to_buckets(groups):
    """Flatten quota-summary groups into {bucketId: {frac, reset}}.
    NOTE: proto3 JSON omits zero values, so a missing remainingFraction means 0% left."""
    out = {}
    for g in groups or []:
        for b in g.get("buckets", []) or []:
            bid = b.get("bucketId")
            if not bid:
                continue
            try:
                frac = float(b.get("remainingFraction", 0.0))
            except Exception:
                frac = 0.0
            out[bid] = {"frac": frac, "reset": b.get("resetTime")}
    return out


# ============================================================================
# Local Antigravity client (read-only) -> exact Gemini 4 Argon weekly quota
# ============================================================================

def _list_language_servers():
    """Return [(pid, csrf_token)] for running Antigravity language servers."""
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'language_server*' } | "
        "ForEach-Object { [string]$_.ProcessId + '|' + $_.CommandLine }"
    )
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
            capture_output=True, text=True, errors="replace", timeout=12,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return []
    res = []
    for line in (r.stdout or "").splitlines():
        if "|" not in line:
            continue
        pid_s, cmd = line.split("|", 1)
        m = re.search(r"--csrf_token[=\s]+\"?([0-9A-Za-z\-]+)", cmd)
        if pid_s.strip().isdigit() and m:
            res.append((int(pid_s.strip()), m.group(1)))
    return res


def _ls_http_ports(pid):
    ports = []
    try:
        with open(LS_LOG_PATH, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 262144))
            txt = f.read().decode("utf-8", errors="ignore")
        idx = txt.rfind(f"process with pid {pid}")
        if idx >= 0:
            m = re.search(r"port at (\d+) for HTTP\s*$", txt[idx:], re.M)
            if m:
                ports.append(int(m.group(1)))
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
            errors="replace", timeout=8, creationflags=CREATE_NO_WINDOW,
        ).stdout or ""
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[-1] == str(pid) and parts[1].startswith("127.0.0.1:") and "LISTEN" in parts[3].upper():
                p = int(parts[1].rsplit(":", 1)[1])
                if p not in ports:
                    ports.append(p)
    except Exception:
        pass
    return ports


def _ls_post(port, csrf, method, timeout=2.5):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/{method}",
        data=b"{}",
        headers={"Content-Type": "application/json", "x-codeium-csrf-token": csrf},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_local_ls_quota():
    """Ask the running Antigravity client for its full quota summary (incl. Gemini 4 Argon)."""
    if os.name != "nt":
        return None
    for pid, csrf in _list_language_servers():
        for port in _ls_http_ports(pid):
            try:
                summ = _ls_post(port, csrf, "RetrieveUserQuotaSummary")
            except Exception:
                continue
            groups = (summ.get("response") or summ).get("groups", [])
            email = ""
            try:
                st = _ls_post(port, csrf, "GetUserStatus")
                email = (st.get("userStatus") or {}).get("email", "") or ""
            except Exception:
                pass
            if groups:
                return {"email": email.strip().lower(), "buckets": summary_to_buckets(groups)}
    return None


def load_argon_cache():
    try:
        with open(ARGON_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_argon_cache(cache):
    try:
        agy_sw.write_json_atomic(ARGON_CACHE_PATH, cache)
    except Exception:
        try:
            with open(ARGON_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


# ============================================================================
# Antigravity accounts
# ============================================================================

def _agy_meta(slot_idx):
    meta = agy_sw.SHORTCUT_META.get(str(slot_idx), {})
    return meta.get("badge_title", f"槽位{slot_idx}"), meta.get("accent", "#A78BFA")


def fetch_agy_remote(slot_idx, active_idx):
    """Fetch Gemini Models + Claude/GPT buckets from Google for one Antigravity slot."""
    alias, accent = _agy_meta(slot_idx)
    is_active = str(slot_idx) == str(active_idx)
    slot_data = agy_sw.load_account_slot(str(slot_idx))
    if not slot_data or not agy_sw.is_valid_auth_obj(slot_data.get("cred")):
        return {"slot": str(slot_idx), "alias": alias, "accent": accent, "is_active": is_active,
                "bound": False, "email": "未绑定账号", "plan": "PRO", "buckets": {}, "error": None}

    email = slot_data.get("email", "未知邮箱")
    plan = str(slot_data.get("plan", "Google AI Pro")).replace("Google AI ", "").upper()
    cred = slot_data["cred"]

    try:
        rem = agy_sw.parse_iso_expiry(cred.get("token", {}).get("expiry", "")) - time.time()
        if rem < 300 and agy_sw.refresh_google_oauth_token(cred, min_remaining_sec=300):
            agy_sw.write_json_atomic(os.path.join(AGY_DIR, f"acc{slot_idx}.json"), slot_data)
            agy_sw.sync_current_if_matches(cred)
    except Exception:
        pass

    def _call():
        req = urllib.request.Request(
            AGY_QUOTA_SUMMARY_URL,
            data=b"{}",
            headers={
                "Authorization": f"Bearer {cred.get('token', {}).get('access_token', '')}",
                "Content-Type": "application/json",
                "User-Agent": "antigravity/2.28.2",
            },
            method="POST",
        )
        return http_open_json(req, timeout=6.5, retries=0)

    summary, err = {}, None
    try:
        summary = _call()
    except urllib.error.HTTPError as e:
        if e.code == 401 and agy_sw.refresh_google_oauth_token(cred, min_remaining_sec=3500):
            agy_sw.write_json_atomic(os.path.join(AGY_DIR, f"acc{slot_idx}.json"), slot_data)
            try:
                summary = _call()
            except Exception as e2:
                err = f"HTTP {getattr(e2, 'code', 'Err')}"
        else:
            err = f"HTTP {e.code}"
            if e.code == 401:
                err += " · 请在客户端重新登录后刷新"
    except Exception as e:
        err = str(e)[:34]

    return {"slot": str(slot_idx), "alias": alias, "accent": accent, "is_active": is_active,
            "bound": True, "email": email, "plan": plan,
            "buckets": summary_to_buckets(summary.get("groups", [])), "error": err}


def build_agy_item(raw, local_ls, cache):
    """Merge remote buckets, local live client data and cached Argon value into a card model."""
    item = {
        "provider": "agy", "slot": raw["slot"], "alias": raw["alias"], "accent": raw["accent"],
        "bound": raw["bound"], "is_active": raw["is_active"], "email": raw["email"],
        "plan": raw["plan"], "error": raw["error"], "ring_label": "ARGON 周",
        "cache_update": None,
    }
    if not raw["bound"]:
        item.update({
            "ring_pct": None,
            "bars": [
                {"label": "◆ Gemini 4 Argon · 周限额（独立池）", "pct": None, "reset": "未绑定", "hl": True},
                {"split": [
                    {"label": "Gemini Pro/Flash 5h", "pct": None, "reset": "未绑定"},
                    {"label": "Gemini Pro/Flash 周", "pct": None, "reset": "未绑定"},
                ]},
            ],
            "sub_chips": [("neutral", "点击右上角「+ 绑定此号」")],
        })
        return item

    email_key = raw["email"].strip().lower()
    buckets = dict(raw["buckets"])
    live_local = bool(local_ls and local_ls.get("email") and local_ls["email"] == email_key)
    if live_local:
        buckets.update(local_ls["buckets"])

    now = time.time()
    argon_src, argon_ts = None, None
    if ARGON_BID in buckets:
        argon_src = "live"
        item["cache_update"] = (email_key, {"buckets": buckets, "ts": now})
    else:
        entry = cache.get(email_key) or {}
        cb = (entry.get("buckets") or {}).get(ARGON_BID)
        if cb:
            argon_src, argon_ts = "cache", entry.get("ts")
            b = dict(cb)
            ep = iso_to_epoch(b.get("reset"))
            if ep is not None and ep <= now:
                b["expired"] = True
            buckets[ARGON_BID] = b

    def pct_of(bid):
        b = buckets.get(bid)
        return None if b is None or b.get("expired") else max(0.0, min(100.0, b["frac"] * 100.0))

    def rst_of(bid, short=False):
        b = buckets.get(bid)
        if b is None:
            return "未知"
        if b.get("expired"):
            return "记录已过期 · 切到此号更新"
        s = format_iso_reset(b.get("reset"))
        return s.replace("后重置", "") if short else s

    argon_pct = pct_of(ARGON_BID)
    if argon_src == "cache":
        ts_txt = datetime.fromtimestamp(argon_ts).strftime("%m-%d %H:%M") if argon_ts else "--"
        argon_reset = f"{rst_of(ARGON_BID)} · 记录于 {ts_txt}"
    elif argon_src == "live":
        argon_reset = rst_of(ARGON_BID)
    else:
        argon_reset = "未记录 · 切到此号后自动记录"

    g5, gw = pct_of("gemini-5h"), pct_of("gemini-weekly")
    c5, cw = pct_of("3p-5h"), pct_of("3p-weekly")

    item["bars"] = [
        {"label": "◆ Gemini 4 Argon · 周限额（独立池）", "pct": argon_pct, "reset": argon_reset, "hl": True},
        {"split": [
            {"label": "Gemini Pro/Flash 5h", "short": "Gemini 5h", "pct": g5, "reset": rst_of("gemini-5h", True)},
            {"label": "Gemini Pro/Flash 周", "short": "Gemini 周", "pct": gw, "reset": rst_of("gemini-weekly", True)},
        ]},
    ]
    item["ring_pct"] = argon_pct
    item["ring_label"] = "ARGON 周" if argon_pct is not None else "ARGON 未记录"

    def fmt(p):
        return "--" if p is None else f"{p:.0f}%"

    chips = [
        ("ok" if (c5 or 0) >= 30 else "warn", f"Claude/GPT 5h {fmt(c5)}（{rst_of('3p-5h', True)}）"),
        ("ok" if (cw or 0) >= 30 else "warn", f"Claude/GPT 周 {fmt(cw)}"),
    ]
    if argon_src == "live":
        chips.append(("accent", "Argon: 本机实时"))
    elif argon_src == "cache":
        chips.append(("neutral", "Argon: 历史记录"))
    else:
        chips.append(("neutral", "Argon: 未记录"))
    if raw["error"] and not live_local:
        chips = [("warn", f"接口提示: {raw['error']}")] + chips[2:]
    item["sub_chips"] = chips
    item["quota_windows"] = [
        dict(id=bid, label=label, pct=pct_of(bid),
             source=argon_src if bid == ARGON_BID else "live",
             reset_at=iso_to_epoch((buckets.get(bid) or {}).get("reset")))
        for bid, label in ((ARGON_BID, "Argon 周"), ("gemini-5h", "Gemini 5小时"),
                           ("gemini-weekly", "Gemini 周"), ("3p-5h", "Claude/GPT 5小时"),
                           ("3p-weekly", "Claude/GPT 周"))
    ]
    return item


# ============================================================================
# OpenAI Codex accounts
# ============================================================================

def fetch_codex_slot_quota(slot_idx, active_idx):
    """Fetch live quota for a single Codex account."""
    meta = codex_sw.SHORTCUT_META.get(str(slot_idx), {})
    alias = meta.get("badge_title", f"槽位{slot_idx}")
    accent = meta.get("accent", "#38BDF8")
    is_active = str(slot_idx) == str(active_idx)

    acc_data = codex_sw.load_account_slot(str(slot_idx))
    if not acc_data or not codex_sw.is_valid_auth_obj(acc_data.get("auth")):
        return {
            "provider": "codex", "slot": str(slot_idx), "alias": alias, "accent": accent,
            "bound": False, "is_active": is_active, "email": "未绑定账号", "plan": "待绑定",
            "error": None, "ring_pct": None, "ring_label": "CODEX",
            "bars": [
                {"label": "◆ Codex · 5小时窗口", "pct": None, "reset": "未绑定", "hl": True},
                {"label": "Codex · 7天周度总池", "pct": None, "reset": "未绑定"},
            ],
            "sub_chips": [("neutral", "点击右上角「+ 绑定此号」启动登录")],
        }

    email = acc_data.get("email", "未知邮箱")
    plan = str(acc_data.get("plan", "Plus")).upper()
    auth_obj = acc_data["auth"]
    tokens = auth_obj.get("tokens", {})

    # The official client owns OAuth renewal. A quota read must never rotate a
    # copied refresh token or replace credentials behind a running client's back.

    headers = {
        "Authorization": f"Bearer {tokens.get('access_token', '')}",
        "User-Agent": "codex-cli/1.0",
        "Accept": "application/json",
    }
    if tokens.get("account_id"):
        headers["ChatGPT-Account-Id"] = tokens["account_id"]

    usage, err = {}, None
    try:
        req = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers=headers, method="GET")
        usage = http_open_json(req, timeout=6.5, retries=0)
    except urllib.error.HTTPError as e:
        err = f"HTTP {e.code}"
    except Exception as e:
        err = str(e)[:34]

    rl = usage.get("rate_limit", {}) or {}
    w5h = rl.get("primary_window") or {}
    w7d = rl.get("secondary_window") or {}
    def remaining(window):
        used = window.get("used_percent")
        return None if used is None else max(0.0, min(100.0, 100.0 - float(used)))

    rem_5h, rem_7d = remaining(w5h), remaining(w7d)
    if usage.get("plan_type"):
        plan = str(usage["plan_type"]).upper()
    allowed = rl.get("allowed")
    credits_cnt = (usage.get("rate_limit_reset_credits") or {}).get("available_count", 0)

    if err:
        bars = [
            {"label": "◆ Codex · 5小时窗口", "pct": None, "reset": "查询失败", "hl": True},
            {"label": "Codex · 7天周度总池", "pct": None, "reset": "查询失败"},
        ]
        chips = [("warn", f"接口提示: {err}")]
        ring = None
    else:
        bars = [
            {"label": "◆ Codex · 5小时窗口", "pct": rem_5h,
             "reset": format_duration_seconds(w5h.get("reset_after_seconds")) if rem_5h is not None else "接口未返回此窗口", "hl": True},
            {"label": "Codex · 7天周度总池", "pct": rem_7d,
             "reset": format_duration_seconds(w7d.get("reset_after_seconds")) if rem_7d is not None else "接口未返回此窗口"},
        ]
        chips = [
            ("neutral" if allowed is None else "ok" if allowed else "warn",
             "速率状态: 未知" if allowed is None else "速率状态: 正常" if allowed else "速率状态: 已限流"),
            ("accent" if credits_cnt > 0 else "neutral", f"满血重置卡: {credits_cnt} 张"),
        ]
        ring = min(rem_5h, rem_7d) if rem_5h is not None and rem_7d is not None else None

    return {
        "provider": "codex", "slot": str(slot_idx), "alias": alias, "accent": accent,
        "bound": True, "is_active": is_active, "email": email, "plan": plan, "error": err,
        "ring_pct": ring, "ring_label": "CODEX", "bars": bars, "sub_chips": chips,
        "quota_windows": [
            dict(id=key, label=label, pct=pct, source="live",
                 reset_at=time.time() + window["reset_after_seconds"] if window.get("reset_after_seconds") is not None else None)
            for key, label, pct, window in (("5h", "5小时窗口", rem_5h, w5h), ("weekly", "周额度", rem_7d, w7d))
        ] if not err else [],
    }


def launch_operation(backend, provider, slot, bind=False):
    from .commands import launch_operation as launch
    return launch(backend, provider, slot, bind=bind)


def provider_switcher(provider):
    if provider not in ("agy", "codex"):
        raise ValueError("未知服务。")
    return agy_sw if provider == "agy" else codex_sw


@provider_operation
def fetch_provider_quotas(provider):
    """Refresh one provider. Demo never reads credentials or uses the network."""
    if DEMO:
        from .demo import provider_data
        return provider_data(provider)
    t0 = time.perf_counter()
    sw = provider_switcher(provider)
    sw.refresh_slots()
    sw.auto_save_current_auth()
    active = sw.get_current_active_idx()
    slots = sw.SLOT_ORDER
    with ThreadPoolExecutor(max_workers=8) as pool:
        if provider == "agy":
            local_future = pool.submit(fetch_local_ls_quota)
            futures = {i: pool.submit(fetch_agy_remote, i, active) for i in slots}
            try:
                local = local_future.result()
            except Exception:
                local = None
            cache = load_argon_cache()
            items = []
            for idx in slots:
                try:
                    raw = futures[idx].result()
                except Exception as ex:
                    saved = sw.load_account_slot(idx) or {}
                    alias, accent = _agy_meta(idx)
                    raw = dict(slot=idx, alias=alias, accent=accent, is_active=idx == active,
                               bound=sw.is_valid_auth_obj(saved.get("cred")), email=saved.get("email", "查询异常"),
                               plan=saved.get("plan", "PRO"), buckets={}, error=str(ex)[:80])
                items.append(build_agy_item(raw, local, cache))
            changed = False
            for item in items:
                update = item.pop("cache_update", None)
                if update:
                    cache[update[0]] = update[1]
                    changed = True
            if changed:
                save_argon_cache(cache)
        else:
            local = None
            futures = {i: pool.submit(fetch_codex_slot_quota, i, active) for i in slots}
            items = []
            for idx in slots:
                try:
                    items.append(futures[idx].result())
                except Exception as ex:
                    saved = sw.load_account_slot(idx) or {}
                    meta = sw.SHORTCUT_META[idx]
                    items.append(dict(provider="codex", slot=idx, alias=meta["badge_title"],
                                      accent=meta["accent"], is_active=idx == active,
                                      bound=sw.is_valid_auth_obj(saved.get("auth")), email=saved.get("email", "查询异常"),
                                      plan=saved.get("plan", "ERROR"), error=str(ex)[:80],
                                      ring_pct=None, ring_label="CODEX", bars=[],
                                      sub_chips=[("warn", "额度查询异常；可点击刷新重试")]))
    return {provider: items, "local_ok": local is not None,
            "elapsed": time.perf_counter() - t0,
            "timestamp": datetime.now().strftime("%H:%M:%S")}


def fetch_all_quotas():
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        agy = pool.submit(fetch_provider_quotas, "agy")
        codex = pool.submit(fetch_provider_quotas, "codex")
        a, c = agy.result(), codex.result()
    return {**a, "codex": c["codex"], "elapsed": time.perf_counter() - t0}


def next_account_slot(provider):
    sw = provider_switcher(provider)
    sw.refresh_slots()
    return str(max(map(int, sw.SLOT_ORDER), default=0) + 1)


@provider_operation
def reserve_account(provider, alias):
    if DEMO:
        raise ValueError("演示模式不更改账号。")
    sw = provider_switcher(provider)
    slot = next_account_slot(provider)
    sw.write_json_atomic(os.path.join(sw.ACCOUNTS_DIR, f"acc{slot}.json"),
                         {"slot": slot, "alias": alias.strip() or f"账号 {slot}"})
    sw.refresh_slots()
    return slot


@provider_operation
def save_new_account(provider, source, alias):
    """Import a native auth object or an existing account export, without replacing any slot."""
    if DEMO:
        raise ValueError("演示模式不导入账号。")
    sw = provider_switcher(provider)
    sw.refresh_slots()
    key = "cred" if provider == "agy" else "auth"
    auth = source.get(key, source) if isinstance(source, dict) else None
    if not sw.is_valid_auth_obj(auth):
        raise ValueError("账号文件格式不正确，未找到有效的登录凭证。")
    if provider == "agy":
        claims = sw.decode_id_token_claims(auth)
        identity, email, plan = claims.get("sub"), claims.get("email"), source.get("plan", "Google AI Pro")
    else:
        identity, email, plan = sw.extract_auth_metadata(auth)
    if not email:
        raise ValueError("无法从登录凭证确认账号邮箱，未保存。")
    for idx in sw.SLOT_ORDER:
        existing = sw.load_account_slot(idx) or {}
        old_auth = existing.get(key)
        if not sw.is_valid_auth_obj(old_auth):
            continue
        if provider == "agy":
            old_id = sw.decode_id_token_claims(old_auth).get("sub")
        else:
            old_id = sw.extract_auth_metadata(old_auth)[0]
        same = identity == old_id if identity and old_id else email.lower() == existing.get("email", "").lower()
        if same:
            raise ValueError(f"此账号已在列表中：{sw.SHORTCUT_META[idx]['badge_title']}。")
    slot = next_account_slot(provider)
    label = alias.strip() or email.split("@")[0]
    payload = {"slot": slot, "alias": label, "email": email, "plan": plan, key: auth}
    if provider == "agy":
        payload["sub"] = identity
    else:
        payload["name"] = label
    sw.write_json_atomic(os.path.join(sw.ACCOUNTS_DIR, f"acc{slot}.json"), payload)
    sw.refresh_slots()
    return slot


@provider_operation
def save_current_account(provider, source, alias=""):
    """Update the known current identity, or explicitly add an unfamiliar identity."""
    if DEMO:
        raise ValueError("演示模式不保存账号。")
    sw = provider_switcher(provider)
    sw.refresh_slots()
    if not sw.is_valid_auth_obj(source):
        raise ValueError("当前客户端尚未登录，未保存账号。")
    for idx in sw.SLOT_ORDER:
        saved = sw.load_account_slot(idx) or {}
        if sw.same_identity(source, saved.get(sw.key)):
            saved[sw.key] = source
            if alias.strip():
                saved["alias"] = alias.strip()
            sw.write_json_atomic(Path(sw.ACCOUNTS_DIR) / f"acc{idx}.json", saved)
            sw.refresh_slots()
            return idx
    return save_new_account(provider, source, alias)


def operation_error(provider, pid):
    try:
        data = json.loads((SETTINGS.home / f"operation_status_{provider}.json").read_text(encoding="utf-8"))
        return data.get("error") if data.get("pid") == pid else None
    except (OSError, ValueError):
        return None


def print_cli_report():
    data = fetch_all_quotas()
    print("=" * 84)
    print(f" Quota Desk | Antigravity & Codex 账号额度 (耗时 {data['elapsed']:.2f}s | {data['timestamp']} | 本机Argon: {'OK' if data['local_ok'] else '不可用'})")
    print("=" * 84)
    for title, key in (("【Antigravity】", "agy"), ("【OpenAI Codex】", "codex")):
        print("\n" + title)
        for it in data[key]:
            act = "★ 在线" if it["is_active"] else ("  未绑" if not it["bound"] else "  待机")
            print(f"  [{it['slot']} · {it['alias']}] {act} | {it['email']} | {it['plan']}")
            for bb in it["bars"]:
                for b in bb.get("split", [bb]):
                    p = "   --  " if b["pct"] is None else f"{b['pct']:6.2f}%"
                    print(f"      ├─ {b['label']:<30} {p}  ({b['reset']})")
            print("      └─ " + "  ·  ".join(t for _, t in it["sub_chips"]))
    print("=" * 84)
