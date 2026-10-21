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
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
