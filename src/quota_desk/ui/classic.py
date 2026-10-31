import os
import sys
import time
import queue
import threading
import subprocess
import ctypes
from .. import backend
from ..commands import launch_operation, launch_plain
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from ..backend import (AGY_DIR, CODEX_DIR, DASHBOARD_ICON_PATH, PYTHONW_EXE,
                           CREATE_NO_WINDOW, DETACHED_PROCESS, AUTO_REFRESH_MS, fetch_all_quotas)

# ============================================================================
# Responsive supersampled renderer (Pillow)
# ============================================================================

_FONT_CACHE = {}
BASE_W, BASE_H = 1280.0, 820.0


def get_font(size, bold=False):
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = ["msyhbd.ttc", "segoeuib.ttf"] if bold else ["msyh.ttc", "segoeui.ttf"]
    for c in candidates:
        p = os.path.join(r"C:\Windows\Fonts", c)
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                _FONT_CACHE[key] = f
                return f
            except Exception:
                pass
    f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def hex_to_rgba(hex_str, alpha=255):
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def blend_rgb(c1_hex, c2_hex, t):
    c1, c2 = hex_to_rgba(c1_hex), hex_to_rgba(c2_hex)
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def get_quota_colors(pct, bound=True):
    if not bound or pct is None:
        return ("#334155", "#475569", "#64748B")
    if pct >= 65.0:
        return ("#0EA5E9", "#10B981", "#34D399")
    if pct >= 30.0:
        return ("#F59E0B", "#FBBF24", "#FBBF24")
    return ("#EF4444", "#F43F5E", "#F87171")


def draw_lightning_bolt(draw, cx, cy, size, fill):
    pts = [
        (cx + size * 0.10, cy - size * 0.55), (cx - size * 0.38, cy + size * 0.05),
        (cx - size * 0.02, cy + size * 0.05), (cx - size * 0.14, cy + size * 0.55),
        (cx + size * 0.38, cy - size * 0.05), (cx + size * 0.02, cy - size * 0.05),
    ]
    draw.polygon(pts, fill=fill)


def draw_gradient_pill_bar(base_img, x1, y1, x2, y2, radius, c1_hex, c2_hex):
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    w, h = max(2, x2 - x1), max(2, y2 - y1)
    strip = Image.new("RGB", (w, 1))
    px = strip.load()
    for x in range(w):
        px[x, 0] = blend_rgb(c1_hex, c2_hex, x / max(1, w - 1))
    grad = strip.resize((w, h), Image.Resampling.NEAREST)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=min(int(radius), h // 2, w // 2), fill=255)
    base_img.paste(grad, (x1, y1), mask)


_BG_CACHE = {}


def _background(W, H):
    key = (W, H)
    if key in _BG_CACHE:
        return _BG_CACHE[key].copy()
    sw, sh = max(8, W // 8), max(8, H // 8)
    small = Image.new("RGBA", (sw, sh), (8, 12, 22, 255))
    gl = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gl)
    gd.ellipse((-0.08 * sw, -0.12 * sh, 0.45 * sw, 0.50 * sh), fill=(124, 58, 237, 50))
    gd.ellipse((0.04 * sw, 0.50 * sh, 0.45 * sw, 1.02 * sh), fill=(16, 185, 129, 26))
    gd.ellipse((0.52 * sw, -0.12 * sh, 1.06 * sw, 0.50 * sh), fill=(14, 165, 233, 46))
    gd.ellipse((0.56 * sw, 0.50 * sh, 1.00 * sw, 1.02 * sh), fill=(168, 85, 247, 32))
    gl = gl.filter(ImageFilter.GaussianBlur(max(2, int(sw * 0.06))))
    img = Image.alpha_composite(small, gl).convert("RGB").resize((W, H), Image.Resampling.BICUBIC)
    _BG_CACHE.clear()
    _BG_CACHE[key] = img
    return img.copy()


def render_dashboard_frame(width, height, data, hover_id=None, is_refreshing=False):
    """Render the whole dashboard for any window size. Everything scales with the window."""
    width, height = max(480, int(width)), max(320, int(height))
    k = max(0.62, min(width / BASE_W, height / BASE_H))
    area = width * height
    S = 2.0 if area <= 1_800_000 else (1.5 if area <= 4_000_000 else 1.0)
    W, H = int(width * S), int(height * S)
    u = S * k
    DW, DH = W / u, H / u  # canvas size in design units

    base = _background(W, H)
    draw = ImageDraw.Draw(base, "RGBA")
    hit_boxes = {}

    def P(v):
        return int(round(v * u))

    def F(sz, bold=False):
        return get_font(max(8, int(round(sz * u))), bold)

    def rr(x1, y1, x2, y2, r, fill=None, outline=None, w=1.0):
        if x2 - x1 < 1 or y2 - y1 < 1:
            return
        draw.rounded_rectangle((P(x1), P(y1), P(x2), P(y2)), radius=P(r), fill=fill,
                               outline=outline, width=max(1, P(w)) if outline else 0)

    def txt(x, y, s, size, fill, bold=False, anchor="la"):
        draw.text((P(x), P(y)), s, font=F(size, bold), fill=fill, anchor=anchor)

    def tlen(s, size, bold=False):
        return draw.textlength(s, font=F(size, bold)) / u

    def fit(s, size, maxw, bold=False):
        if tlen(s, size, bold) <= maxw:
            return s
        while s and tlen(s + "…", size, bold) > maxw:
            s = s[:-1]
        return s + "…"

    def grad(x1, y1, x2, y2, r, c1, c2):
        if x2 - x1 >= 1:
            draw_gradient_pill_bar(base, P(x1), P(y1), P(x2), P(y2), P(r), c1, c2)

    def hit(hid, x1, y1, x2, y2, meta):
        hit_boxes[hid] = (x1 * k, y1 * k, x2 * k, y2 * k, meta)

    def button(hid, x1, y1, x2, y2, label, size, style="ghost", accent="#38BDF8", icon=None):
        hov = hover_id == hid
        if style == "primary":
            grad(x1, y1, x2, y2, 11, "#3B82F6" if hov else "#2563EB", "#06B6D4" if hov else "#1D4ED8")
            rr(x1, y1, x2, y2, 11, outline=(147, 197, 253, 200))
            fg = (255, 255, 255, 255)
        elif style == "disabled":
            rr(x1, y1, x2, y2, 11, fill=(51, 65, 85, 220), outline=(100, 116, 139, 180))
            fg = (203, 213, 225, 255)
        elif style == "online":
            rr(x1, y1, x2, y2, 10, fill=(5, 150, 105, 255) if hov else (6, 78, 59, 230), outline=(16, 185, 129, 200))
            fg = (255, 255, 255, 255) if hov else (52, 211, 153, 255)
        elif style == "bind":
            rr(x1, y1, x2, y2, 10, fill=(147, 51, 234, 255) if hov else (126, 34, 206, 230), outline=(192, 132, 252, 200))
            fg = (255, 255, 255, 255)
        else:  # ghost / accent-on-hover
            rr(x1, y1, x2, y2, 10, fill=hex_to_rgba(accent, 240) if hov else (30, 41, 59, 240),
               outline=hex_to_rgba(accent, 200) if hov else (71, 85, 105, 190))
            fg = (9, 13, 22, 255) if hov else (226, 232, 240, 255)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if icon == "bolt":
            bc = fg if style != "ghost" or hov else hex_to_rgba(accent, 255)
            tw = tlen(label, size, True)
            draw_lightning_bolt(draw, P(cx - tw / 2 - 4), P(cy), P(size * 1.1), bc)
            txt(cx + 8, cy, label, size, fg, True, "mm")
        elif icon == "refresh":
            tw = tlen(label, size, True)
            ix, r = cx - tw / 2 - 6, size * 0.48
            draw.arc((P(ix - r), P(cy - r), P(ix + r), P(cy + r)), start=35, end=315, fill=fg, width=max(1, P(2)))
            draw.polygon([(P(ix + r - 2.5), P(cy - r - 2)), (P(ix + r + 3), P(cy - r - 2)), (P(ix + r + 1), P(cy - r + 3))], fill=fg)
            txt(cx + 9, cy, label, size, fg, True, "mm")
        elif icon == "full":
            tw = tlen(label, size, True)
            ix, r, l = cx - tw / 2 - 6, size * 0.45, size * 0.28
            lw = max(1, P(1.8))
            for sx in (-1, 1):
                for sy in (-1, 1):
                    px, py = ix + sx * r, cy + sy * r
                    draw.line((P(px), P(py), P(px - sx * l), P(py)), fill=fg, width=lw)
                    draw.line((P(px), P(py), P(px), P(py - sy * l)), fill=fg, width=lw)
            txt(cx + 9, cy, label, size, fg, True, "mm")
        else:
            txt(cx, cy, label, size, fg, True, "mm")

    m = 24.0

    # ---------------- Header ----------------
    hy1, hy2 = 16.0, 92.0
    rr(m, hy1, DW - m, hy2, 18, fill=(15, 23, 42, 215), outline=(51, 65, 85, 190))
    ex1, ey1, ex2, ey2 = m + 18, hy1 + 16, m + 62, hy2 - 16
    rr(ex1, ey1, ex2, ey2, 12, fill=(37, 99, 235, 60), outline=(56, 189, 248, 210))
    draw_lightning_bolt(draw, P((ex1 + ex2) / 2), P((ey1 + ey2) / 2), P(25), (56, 189, 248, 255))

    bx2 = DW - m - 16
    bx1 = bx2 - 176
    by1, by2 = hy1 + 17, hy2 - 17
    button("refresh", bx1, by1, bx2, by2, "正在同步..." if is_refreshing else "立即刷新 (F5)", 14,
           style="disabled" if is_refreshing else "primary", icon=None if is_refreshing else "refresh")
    hit("refresh", bx1, by1, bx2, by2, {"action": "refresh"})

    tx = ex2 + 16
    tmax = bx1 - 16 - tx
    txt(tx, hy1 + 12, fit("Quota Desk · Antigravity & Codex", 22, tmax, True), 22, (248, 250, 252, 255), True)
    if is_refreshing or not data:
        sub, sub_col = "● 正在同步账号的实时额度…", (251, 191, 36, 255)
    else:
        lt = "本机 Antigravity 已连接" if data.get("local_ok") else "本机 Antigravity 未连接（Argon 显示历史记录）"
        sub = f"● 更新于 {data['timestamp']} · 耗时 {data['elapsed']:.1f}s · {lt}"
        sub_col = (148, 163, 184, 255)
    txt(tx, hy1 + 47, fit(sub, 13, tmax), 13, sub_col)

    # ---------------- Columns ----------------
    col_gap = 20.0
    col_w = (DW - 2 * m - col_gap) / 2
    cols = [(m, "agy"), (m + col_w + col_gap, "codex")]
    sec_y = 110.0
    col_titles = {
        "agy": ("Antigravity 账号", (167, 139, 250, 255), "◆ 重点: Gemini 4 Argon 周限额", (88, 28, 135, 100), (221, 214, 254, 255)),
        "codex": ("OpenAI Codex / ChatGPT", (56, 189, 248, 255), "◆ 5小时窗口 + 7天周池", (12, 74, 110, 100), (186, 230, 253, 255)),
    }
    for cx, key in cols:
        title, dot, tag, tag_bg, tag_fg = col_titles[key]
        draw.ellipse((P(cx + 4), P(sec_y + 8), P(cx + 16), P(sec_y + 20)), fill=dot)
        txt(cx + 24, sec_y, title, 18, (241, 245, 249, 255), True)
        rx2 = cx + col_w
        rx1 = rx2 - 156
        hid = f"rotate_{key}"
        button(hid, rx1, sec_y - 2, rx2, sec_y + 30, "轮换下一号", 13, style="ghost",
               accent="#A78BFA" if key == "agy" else "#38BDF8", icon="bolt")
        hit(hid, rx1, sec_y - 2, rx2, sec_y + 30, {"action": "rotate", "provider": key})
        tag_x = cx + 24 + tlen(title, 18, True) + 14
        tag_w = tlen(tag, 12, True) + 24
        if tag_x + tag_w < rx1 - 10:
            rr(tag_x, sec_y + 2, tag_x + tag_w, sec_y + 27, 12, fill=tag_bg, outline=dot[:3] + (170,))
            txt(tag_x + tag_w / 2, sec_y + 14.5, tag, 12, tag_fg, True, "mm")

    # ---------------- Cards ----------------
    cards_top = 154.0
    footer_h = 36.0
    card_gap = 16.0
    card_h = max(160.0, (DH - cards_top - footer_h - 2 * card_gap) / 3)

    def draw_card(cx1, cy1, cw, ch, item):
        cx2, cy2 = cx1 + cw, cy1 + ch
        bound, active = item["bound"], item["is_active"]
        accent = item["accent"]
        acc = hex_to_rgba(accent, 255)
        if active:
            rr(cx1, cy1, cx2, cy2, 18, fill=(17, 25, 44, 238), outline=hex_to_rgba(accent, 235), w=2)
            rr(cx1 + 4, cy1 + 16, cx1 + 9, cy2 - 16, 3, fill=acc)
        else:
            rr(cx1, cy1, cx2, cy2, 18, fill=(13, 19, 34, 215), outline=(30, 41, 59, 220))

        # Ring gauge
        ring_r = min(ch * 0.31, 60.0)
        rcx, rcy = cx1 + 24 + ring_r, cy1 + ch / 2
        rw = max(6.0, ring_r * 0.17)
        draw.ellipse((P(rcx - ring_r - 5), P(rcy - ring_r - 5), P(rcx + ring_r + 5), P(rcy + ring_r + 5)), fill=(15, 23, 42, 170))
        box = (P(rcx - ring_r), P(rcy - ring_r), P(rcx + ring_r), P(rcy + ring_r))
        draw.arc(box, start=0, end=360, fill=(30, 41, 59, 255), width=P(rw))
        rp = item.get("ring_pct")
        c1, c2, ct = get_quota_colors(rp, bound)
        if bound and rp is not None and rp > 0:
            draw.arc(box, start=-90, end=-90 + max(5.0, 360.0 * rp / 100.0), fill=hex_to_rgba(c2, 255), width=P(rw))
        if rp is None or not bound:
            ps = "--"
        elif rp >= 99.95:
            ps = "100%"
        elif rp >= 10:
            ps = f"{rp:.0f}%"
        else:
            ps = f"{rp:.1f}%"
        txt(rcx, rcy - ring_r * 0.10, ps, ring_r * 0.40, hex_to_rgba(ct, 255), True, "mm")
        txt(rcx, rcy + ring_r * 0.34, item.get("ring_label", ""), max(9.0, ring_r * 0.18), (148, 163, 184, 230), True, "mm")

        rx1 = rcx + ring_r + 26
        rx2 = cx2 - 18
        cwid = rx2 - rx1

        # Row 1: alias, plan, email, action button
        r1 = cy1 + 16
        rh = 30.0
        alias = item["alias"]
        aw = tlen(alias, 15, True) + 22
        rr(rx1, r1, rx1 + aw, r1 + rh, 8, fill=acc)
        txt(rx1 + aw / 2, r1 + rh / 2, alias, 15, (9, 13, 22, 255), True, "mm")
        px1 = rx1 + aw + 8
        plan = item["plan"]
        pw = tlen(plan, 12, True) + 18
        rr(px1, r1 + 3, px1 + pw, r1 + rh - 3, 7, fill=(30, 41, 59, 235), outline=hex_to_rgba(accent, 140))
        txt(px1 + pw / 2, r1 + rh / 2, plan, 12, acc, True, "mm")

        btn_w = 128.0
        b_x2, b_x1 = rx2, rx2 - btn_w
        bid = f"{item['provider']}_{item['slot']}"
        if not bound:
            button(bid, b_x1, r1, b_x2, r1 + rh, "+ 绑定此号", 13, style="bind")
        elif active:
            button(bid, b_x1, r1, b_x2, r1 + rh, "★ 当前在线", 13, style="online")
        else:
            button(bid, b_x1, r1, b_x2, r1 + rh, "秒切此号", 13, style="ghost", accent=accent, icon="bolt")
        hit(bid, b_x1, r1, b_x2, r1 + rh, {
            "action": "slot_click", "provider": item["provider"], "slot": item["slot"],
            "alias": alias, "bound": bound, "is_active": active,
        })
        e_x = px1 + pw + 12
        email = fit(item["email"], 14, max(30.0, b_x1 - 12 - e_x))
        txt(e_x, r1 + rh / 2, email, 14, (226, 232, 240, 255) if bound else (100, 116, 139, 255), False, "lm")

        # Bars
        chip_h = 26.0
        chip_y = cy2 - 14 - chip_h
        bars = item.get("bars", [])
        b_start = r1 + rh + 14
        avail = chip_y - 10 - b_start
        n = max(1, len(bars))
        step = min(62.0, avail / n)

        def draw_bar(x1, x2, y, b, hl, lsz, prefix="剩余 "):
            pct = b.get("pct")
            bc1, bc2, btxt = get_quota_colors(pct, bound)
            bw = x2 - x1
            label_col = (237, 233, 254, 255) if (hl and item["provider"] == "agy") else (216, 224, 236, 255)
            if not bound:
                rhs = "未绑定"
            elif pct is None:
                rhs = b.get("reset", "未记录")
            else:
                pf = f"{pct:.0f}%" if (pct >= 99.95 or pct <= 0.0) else f"{pct:.1f}%"
                rhs = f"{prefix}{pf} · {b['reset']}"
            rhs = fit(rhs, lsz, bw * 0.66, True)
            rhs_w = tlen(rhs, lsz, True)
            lab_max = max(30.0, bw - rhs_w - 12)
            lab = b["label"]
            if tlen(lab, lsz, True) > lab_max and b.get("short"):
                lab = b["short"]
            lab = fit(lab, lsz, lab_max, True)
            txt(x1, y, lab, lsz, label_col, True)
            txt(x2, y, rhs, lsz, hex_to_rgba(btxt, 255), True, "ra")
            th = 13.0 if hl else 10.0
            ty1 = y + min(lsz * 1.75, max(lsz * 1.4, step - th - 8))
            ty2 = ty1 + th
            rr(x1, ty1, x2, ty2, th / 2, fill=(24, 34, 52, 255), outline=(38, 52, 76, 210))
            if bound and pct is not None and pct > 0:
                grad(x1, ty1, x1 + max(th, bw * pct / 100.0), ty2, th / 2, bc1, bc2)

        for i, b in enumerate(bars):
            y = b_start + i * step
            if b.get("split"):
                halves = b["split"]
                gap = 22.0
                hw = (cwid - gap * (len(halves) - 1)) / len(halves)
                for j, hb in enumerate(halves):
                    hx1 = rx1 + j * (hw + gap)
                    draw_bar(hx1, hx1 + hw, y, hb, False, 13.0, prefix="")
            else:
                hl = b.get("hl", False)
                draw_bar(rx1, rx2, y, b, hl, 14.5 if hl else 13.5)

        # Chips
        chx = rx1
        for kind, ctext in item.get("sub_chips", []):
            tw = tlen(ctext, 12)
            cwc = tw + 30
            if chx + cwc > rx2:
                ctext = fit(ctext, 12, max(20.0, rx2 - chx - 30))
                cwc = tlen(ctext, 12) + 30
                if cwc < 50:
                    break
            rr(chx, chip_y, chx + cwc, chip_y + chip_h, 7, fill=(15, 23, 42, 235), outline=(45, 58, 82, 190))
            dot = {"ok": (52, 211, 153, 255), "accent": (56, 189, 248, 255),
                   "warn": (248, 113, 113, 255)}.get(kind, (100, 116, 139, 255))
            draw.ellipse((P(chx + 9), P(chip_y + chip_h / 2 - 3.5), P(chx + 16), P(chip_y + chip_h / 2 + 3.5)), fill=dot)
            txt(chx + 21, chip_y + chip_h / 2, ctext, 12, (176, 190, 210, 255), False, "lm")
            chx += cwc + 8

    if data:
        for cx, key in cols:
            for i, item in enumerate(data.get(key, [])):
                draw_card(cx, cards_top + i * (card_h + card_gap), col_w, card_h, item)

    # ---------------- Footer ----------------
    fy = DH - footer_h / 2 - 4
    txt(m + 4, fy, fit("F5 刷新 · F6 账号管理 / 朴素版 · PgUp/PgDn 翻页 · Argon 非当前账号为历史记录",
                       11.5, DW - 2 * m - 200), 11.5, (100, 116, 139, 255), False, "lm")
    txt(DW - m - 4, fy, f"每 {AUTO_REFRESH_MS // 60000} 分钟刷新{(data or {}).get('page_label', '')}", 11.5, (100, 116, 139, 255), False, "rm")

    final = base.resize((width, height), Image.Resampling.LANCZOS) if S != 1.0 else base
    return final, hit_boxes


# ============================================================================
# GUI
# ============================================================================

def launch_gui():
    import tkinter as tk
    from tkinter import messagebox
    from PIL import ImageTk

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()
    root.title("Quota Desk · 经典版" + (" · 离线演示" if backend.DEMO else ""))
    root.configure(bg="#080C16")
    if os.path.exists(DASHBOARD_ICON_PATH):
        try:
            root.iconbitmap(DASHBOARD_ICON_PATH)
        except Exception:
            pass

    scr_w, scr_h = root.winfo_screenwidth(), root.winfo_screenheight()
    win_w, win_h = min(int(scr_w * 0.85), 1600), min(int(scr_h * 0.85), 1000)
    root.geometry(f"{win_w}x{win_h}+{(scr_w - win_w) // 2}+{max(10, (scr_h - win_h) // 2 - 20)}")
    root.minsize(900, 580)
    root.resizable(True, True)

    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        v = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), ctypes.sizeof(v))
        c = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(c), ctypes.sizeof(c))
    except Exception:
        pass

    try:
        root.state("zoomed")  # open maximized
    except Exception:
        pass

    canvas = tk.Canvas(root, bg="#080C16", highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)

    st = {"data": None, "hover": None, "refreshing": False, "boxes": {}, "img": None,
          "pending": None, "last_size": None, "switching": {}, "refresh_again": False, "page": 0}
    results = queue.Queue()

    def redraw():
        st["pending"] = None
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w < 50 or h < 50:
            return
        display = st["data"]
        if display:
            pages = max(1, (max(len(display["agy"]), len(display["codex"])) + 2) // 3)
            st["page"] %= pages
            display = dict(display)
            for provider in ("agy", "codex"):
                display[provider] = display[provider][st["page"] * 3:(st["page"] + 1) * 3]
            display["page_label"] = f" · 第 {st['page'] + 1}/{pages} 页" if pages > 1 else ""
        pil, boxes = render_dashboard_frame(w, h, display, hover_id=None,
                                            is_refreshing=st["refreshing"])
        st["boxes"] = boxes
        st["img"] = ImageTk.PhotoImage(pil)
        st["last_size"] = (w, h)
        canvas.delete("all")
        canvas.create_image(0, 0, anchor="nw", image=st["img"])

    def schedule_redraw(delay=70):
        if st["pending"] is not None:
            root.after_cancel(st["pending"])
        st["pending"] = root.after(delay, redraw)

    def on_configure(e):
        if st["last_size"] != (e.width, e.height):
            schedule_redraw(80)

    def find_hit(x, y):
        for hid, (x1, y1, x2, y2, meta) in st["boxes"].items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return hid, meta
        return None, None

    def on_move(e):
        hid, _ = find_hit(e.x, e.y)
        if hid != st["hover"]:
            st["hover"] = hid
            canvas.config(cursor="hand2" if hid else "")
            # Cursor feedback is immediate; hovering does not repaint six cards.

    def on_leave(_e):
        if st["hover"]:
            st["hover"] = None

    def launch_switch(provider, slot, bind=False):
        if backend.DEMO:
            messagebox.showinfo("离线演示", "演示模式不会更改真实客户端账号。", parent=root)
            return
        if provider in st["switching"]:
            return
        st["switching"][provider] = None
        root.title("额度监控中心 · 正在绑定账号…" if bind else "额度监控中心 · 正在切换账号…")

        def wait_for_switch():
            proc = st["switching"][provider]
            if proc is None:
                # Let an in-flight quota/token update finish before changing identity.
                if st["refreshing"]:
                    root.after(100, wait_for_switch)
                    return
                try:
                    proc = launch_operation(backend, provider, slot, bind=bind)
                    st["switching"][provider] = proc
                except OSError as ex:
                    del st["switching"][provider]
                    root.title("Quota Desk · 经典版" + (" · 离线演示" if backend.DEMO else ""))
                    messagebox.showerror("启动失败", str(ex), parent=root)
                    return
            code = proc.poll()
            if code is None:
                root.after(250, wait_for_switch)
                return
            del st["switching"][provider]
            if not st["switching"]:
                root.title("Quota Desk · 经典版" + (" · 离线演示" if backend.DEMO else ""))
            if code != 0:
                messagebox.showerror("切换未完成", backend.operation_error(provider, proc.pid) or f"账号操作退出，状态码 {code}。请重试。", parent=root)
            trigger_refresh()
            if provider == "agy" and code == 0:
                # Client startup can outlast the switch helper; capture Argon once it is ready.
                root.after(8000, trigger_refresh)

        wait_for_switch()

    def handle_slot(meta):
        prov, slot, alias = meta["provider"], meta["slot"], meta["alias"]
        if prov in st["switching"]:
            return
        if not meta["bound"]:
            launch_switch(prov, slot, bind=True)
            return
        if meta["is_active"]:
            return
        if prov in ("agy", "codex"):
            ok = messagebox.askyesno(
                "确认切换账号",
                f"切换到 [{alias}] 会重启对应客户端以加载新凭证。\n\n是否立即切换？",
                parent=root,
            )
            if not ok:
                return
            launch_switch(prov, slot)
        else:
            launch_switch(prov, slot)

    def handle_rotate(provider):
        items = (st["data"] or {}).get(provider, [])
        bound = [it for it in items if it["bound"]]
        if len(bound) < 2:
            return
        idx = next((i for i, it in enumerate(bound) if it["is_active"]), -1)
        nxt = bound[(idx + 1) % len(bound)]
        handle_slot({"provider": provider, "slot": nxt["slot"], "alias": nxt["alias"],
                     "bound": True, "is_active": False})

    def on_click(e):
        hid, meta = find_hit(e.x, e.y)
        if not meta:
            return
        act = meta.get("action")
        if act == "refresh":
            trigger_refresh()
        elif act == "rotate":
            handle_rotate(meta["provider"])
        elif act == "slot_click":
            handle_slot(meta)

    def worker():
        try:
            d = fetch_all_quotas()
        except Exception as ex:
            d = {"agy": [], "codex": [], "elapsed": 0.0, "timestamp": str(ex)[:30], "local_ok": False}

        results.put(d)

    def poll_results():
        try:
            d = results.get_nowait()
        except queue.Empty:
            pass
        else:
            st["data"] = d
            st["refreshing"] = False
            redraw()
            if st["refresh_again"] and not st["switching"]:
                trigger_refresh()
        root.after(80, poll_results)

    def trigger_refresh(_e=None):
        if st["refreshing"] or st["switching"]:
            st["refresh_again"] = True
            return
        st["refresh_again"] = False
        st["refreshing"] = True
        threading.Thread(target=worker, daemon=True).start()
        schedule_redraw(0)

    def auto_refresh():
        trigger_refresh()
        root.after(AUTO_REFRESH_MS, auto_refresh)

    def change_page(delta):
        st["page"] += delta
        schedule_redraw(0)

    def open_simple(_event=None):
        launch_plain(backend)

    root.bind("<F6>", open_simple)
    root.bind("<Next>", lambda e: change_page(1))
    root.bind("<Prior>", lambda e: change_page(-1))
    canvas.bind("<Configure>", on_configure)
    canvas.bind("<Motion>", on_move)
    canvas.bind("<Leave>", on_leave)
    canvas.bind("<Button-1>", on_click)
    root.bind("<F5>", trigger_refresh)

    root.after(80, poll_results)
    root.after(120, auto_refresh)
    root.mainloop()
