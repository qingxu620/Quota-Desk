"""Command-line entry and desktop launcher."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import sys

from . import __version__
from .config import Settings


def parser():
    p = argparse.ArgumentParser(description="Quota Desk — Antigravity / Codex 额度与账号看板")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--simple", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--demo", action="store_true", help="完全离线的演示模式，不读取或更改真实账号")
    p.add_argument("--cli", action="store_true", help="在终端查看额度")
    p.add_argument("--data-dir", type=Path, help="独立数据目录（默认 LOCALAPPDATA/QuotaDesk）")
    p.add_argument("--refresh-seconds", type=int, choices=range(30, 3601), metavar="30..3600")
    p.add_argument("--install-shortcuts", action="store_true", help="创建桌面快捷方式")
    p.add_argument("--diagnostics", action="store_true", help="输出不含账号、邮箱和令牌的本机诊断摘要")
    sub = p.add_subparsers(dest="command")
    account = sub.add_parser("account", help="显式管理本地账号")
    commands = account.add_subparsers(dest="action", required=True)
    for command in ("list", "import-current", "import-file", "bind", "switch", "recover"):
        item = commands.add_parser(command)
        item.add_argument("provider", choices=("agy", "codex"))
        if command == "import-file":
            item.add_argument("file", type=Path)
        if command in ("bind", "switch"):
            item.add_argument("slot", nargs="?" if command == "bind" else None)
        if command in ("import-current", "import-file", "bind"):
            item.add_argument("--name", default="")
    return p


def launch_gui(q):
    from .tray import reveal_existing
    if not q.DEMO and reveal_existing(q.SETTINGS.home):
        return
    import tkinter as tk
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except OSError:
            pass
    from .ui.dashboard import QuotaDashboard
    root = tk.Tk()
    app = QuotaDashboard(root, q)
    scale = root.winfo_fpixels("1i") / 96
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    width, height = min(round(1080 * scale), round(sw * .88)), min(round(820 * scale), round(sh * .88))
    root.geometry(f"{width}x{height}+{(sw-width)//2}+{max(0,(sh-height)//2-30)}")
    root.mainloop()


def account_command(q, args):
    from .operations import AccountOperations
    from .storage import write_json
    if q.DEMO:
        raise ValueError("演示模式不会读取、导入、绑定、恢复或切换真实账号。")
    sw = q.provider_switcher(args.provider)
    if args.action == "list":
        for idx in sw.SLOT_ORDER:
            print(f"{idx}: {sw.SHORTCUT_META[idx]['badge_title']}")
        return
    if args.action == "import-current":
        print("已保存账号", q.save_current_account(args.provider, sw.read_current(), args.name))
        return
    if args.action == "import-file":
        source = json.loads(args.file.read_text(encoding="utf-8-sig"))
        print("已导入账号", q.save_new_account(args.provider, source, args.name))
        return
    operation = AccountOperations(q)
    receipt = q.SETTINGS.home / f"operation_status_{args.provider}.json"
    try:
        if args.action == "recover":
            operation.recover(args.provider)
        else:
            slot = args.slot or q.reserve_account(args.provider, args.name)
            operation.run(args.provider, slot, bind=args.action == "bind")
    except Exception as exc:
        write_json(receipt, {"pid": os.getpid(), "error": str(exc)[:200]})
        raise
    write_json(receipt, {"pid": os.getpid(), "error": None})
    print("账号操作完成。")


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        if not args.demo and os.name != "nt":
            raise ValueError("真实账号仅支持 Windows；可以用 --demo --cli 离线体验。")
        settings = (Settings(home=args.data_dir) if args.data_dir else Settings()) if args.demo else Settings.load(args.data_dir)
        if args.refresh_seconds is not None:
            settings.refresh_seconds = args.refresh_seconds
        from . import backend as q
        q.configure(settings, demo=args.demo)
        if args.diagnostics:
            print(json.dumps({"version": __version__, "python": platform.python_version(),
                              "platform": platform.system(), "demo": q.DEMO,
                              "refresh_seconds": settings.refresh_seconds,
                              "accounts": {k: len(q.STORE.slots(k)) for k in ("agy", "codex")} if not q.DEMO else {},
                              "credential_storage": "Windows DPAPI"}, ensure_ascii=False, indent=2))
        elif args.command == "account":
            account_command(q, args)
        elif args.install_shortcuts:
            from .shortcuts import install
            install(q)
        elif args.cli:
            q.print_cli_report()
        else:
            launch_gui(q)
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        if sys.stderr is not None:
            print(f"Quota Desk: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
