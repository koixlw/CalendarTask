"""pycal 入口。

默认行为：
  python -m pycal           # 启动 GUI
  python -m pycal --import  # 仅导入 DesktopCal 数据并退出
  python -m pycal --search 关键词   # 命令行搜索
"""

from __future__ import annotations
import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="pycal")
    sub = parser.add_subparsers(dest="cmd")

    p_gui = sub.add_parser("gui", help="启动桌面日历窗口")
    p_gui.add_argument("--no-fullscreen", action="store_true")

    p_imp = sub.add_parser("import", help="从 DesktopCal calendar.db 导入")
    p_imp.add_argument(
        "--source",
        default=r"C:\Users\Administrator\AppData\Roaming\CalendarTask\Db\calendar.db",
        help="DesktopCal 数据库路径",
    )

    p_search = sub.add_parser("search", help="命令行搜索")
    p_search.add_argument("keyword")

    p_report = sub.add_parser("report", help="生成日报 / 周报")
    p_report.add_argument("--day", metavar="YYYY-MM-DD", help="日报 (默认今天)")
    p_report.add_argument("--week", action="store_true", help="周报 (本周)")
    p_report.add_argument("--html", action="store_true")
    p_report.add_argument("--out", metavar="PATH", help="输出路径 / 目录")
    p_report.add_argument("--open", action="store_true")

    p_sync = sub.add_parser("sync", help="CalDAV 云同步")
    p_sync.add_argument("action", nargs="?",
                        choices=["list", "sync", "sync-all", "add"],
                        default="list")

    p_install = sub.add_parser("install", help="自启动 / 快捷方式 / 状态")
    p_install.add_argument("action", nargs="?", default="status",
                           choices=["status", "autostart-on", "autostart-off",
                                    "shortcut", "uninstall"])

    args = parser.parse_args()

    if args.cmd in (None, "gui"):
        from pycal.ui.main_window import run
        return run(fullscreen=not args.no_fullscreen)
    if args.cmd == "import":
        from pycal.importer import run_import
        n = run_import(args.source)
        print(f"imported {n} items from {args.source}")
        return 0
    if args.cmd == "search":
        from pycal.db.connection import open_default
        from pycal.search.engine import SearchEngine, SearchOptions
        con = open_default()
        eng = SearchEngine(con)
        hits = eng.search(SearchOptions(keyword=args.keyword))
        for h in hits:
            print(f"  • [{h.date}] {h.title!r}")
        return 0
    if args.cmd == "report":
        import sys as _sys
        from datetime import date as _date
        from pycal.report import (
            render_daily_markdown, render_weekly_markdown, render_html,
        )
        d = _date.fromisoformat(args.day) if args.day else _date.today()
        md = (render_weekly_markdown(d) if args.week
              else render_daily_markdown(d))
        content = (render_html(md, "PyCal Report") if args.html else md)
        if args.out:
            from pathlib import Path
            p = Path(args.out)
            if p.suffix not in (".md", ".html"):
                p.mkdir(parents=True, exist_ok=True)
                p = p / f"pycal_report_{d:%Y%m%d}.{'html' if args.html else 'md'}"
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            print(f"saved: {p}")
            if args.open:
                import webbrowser
                webbrowser.open(p.as_uri())
        else:
            _sys.stdout.buffer.write(content.encode("utf-8"))
            print()
        return 0
    if args.cmd == "sync":
        import sys as _sys
        from pycal.sync.__main__ import main as sync_main
        _sys.argv = ["pycal.sync"] + (
            [] if args.action == "list" else
            ["--sync-all"] if args.action == "sync-all" else
            ["--sync", "1"] if args.action == "sync" else
            ["--list"]
        )
        return sync_main()
    if args.cmd == "install":
        import sys as _sys
        from pycal.installer import main as inst_main
        mapping = {
            "status": [],
            "autostart-on": ["--autostart", "on"],
            "autostart-off": ["--autostart", "off"],
            "shortcut": ["--shortcut", "create"],
            "uninstall": ["--uninstall"],
        }
        extra = mapping.get(args.action, [])
        # status 是无参的默认动作, 不塞 --status 避免和 argparse 冲突
        _sys.argv = ["pycal.installer"] + (extra or ["--status"])
        return inst_main()
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
