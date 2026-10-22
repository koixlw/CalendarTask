"""CLI 报告入口 — python -m pycal.report

用法:
  python -m pycal.report --today                    # 今天的日报 (Markdown, stdout)
  python -m pycal.report --today --html              # HTML 格式
  python -m pycal.report --day 2026-10-21            # 指定日期
  python -m pycal.report --week                      # 本周周报
  python -m pycal.report --week --html
  python -m pycal.report --yesterday
  python -m pycal.report --last-week
  python -m pycal.report --today --out D:/reports    # 存到目录 (自动命名)
  python -m pycal.report --today --open              # 生成后用默认程序打开
  python -m pycal.report --auto-daily                # 每天 18:00 自动生成日报
  python -m pycal.report --auto-weekly               # 每周五 17:00 自动生成周报
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path


def _default_out_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(base) / "PyCal" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_day(args) -> date:
    if args.day:
        return date.fromisoformat(args.day)
    if args.yesterday:
        return date.today() - timedelta(days=1)
    return date.today()


def _resolve_week_day(args) -> date:
    if args.week_day:
        return date.fromisoformat(args.week_day)
    if args.last_week:
        return date.today() - timedelta(days=7)
    return date.today()


def _emit(markdown: str, args, kind: str, ref: str) -> None:
    """输出或保存。"""
    if args.html:
        from pycal.report import render_html
        content = render_html(markdown, title=f"PyCal {kind} {ref}")
        ext = "html"
    else:
        content = markdown
        ext = "md"

    if args.out:
        base = Path(args.out)
        if base.suffix in (".md", ".html", ".htm"):
            base.parent.mkdir(parents=True, exist_ok=True)
            path = base
        else:
            base.mkdir(parents=True, exist_ok=True)
            path = base / f"pycal_{kind}_{ref.replace('-', '')}.{ext}"
    else:
        path = None

    if path:
        path.write_text(content, encoding="utf-8")
        print(f"✓ 已保存: {path}", file=sys.stderr)
        if args.open:
            webbrowser.open(path.as_uri())
    else:
        print(content)
        if args.open:
            # 写临时文件再打开
            tmp = _default_out_dir() / f"pycal_{kind}_{ref.replace('-', '')}.{ext}"
            tmp.write_text(content, encoding="utf-8")
            webbrowser.open(tmp.as_uri())


def _auto_mode(args) -> int:
    """常驻进程: 每天/每周定时生成。"""
    import time
    print("[auto] PyCal 报告自动生成已启动", file=sys.stderr)
    print("[auto] 日报: 每天 18:00", file=sys.stderr)
    print("[auto] 周报: 每周五 17:00", file=sys.stderr)
    print("[auto] Ctrl+C 退出", file=sys.stderr)
    out_dir = _default_out_dir()
    last_daily = None
    last_weekly = None
    while True:
        now = datetime.now()
        # 日报
        if args.auto_daily and now.hour >= 18 and last_daily != now.date():
            from pycal.report import render_daily_markdown
            md = render_daily_markdown(date.today())
            p = out_dir / f"pycal_daily_{now.date().strftime('%Y%m%d')}.md"
            p.write_text(md, encoding="utf-8")
            print(f"[auto] 日报已生成: {p}", file=sys.stderr)
            last_daily = now.date()
        # 周报
        if args.auto_weekly and now.weekday() == 4 and now.hour >= 17 \
                and last_weekly != now.isocalendar()[:2]:
            from pycal.report import render_weekly_markdown
            md = render_weekly_markdown(date.today())
            p = out_dir / f"pycal_weekly_{now.strftime('%Y%m%d')}.md"
            p.write_text(md, encoding="utf-8")
            print(f"[auto] 周报已生成: {p}", file=sys.stderr)
            last_weekly = now.isocalendar()[:2]
        time.sleep(60)


def main() -> int:
    p = argparse.ArgumentParser(prog="pycal.report")
    p.add_argument("--today", action="store_true")
    p.add_argument("--yesterday", action="store_true")
    p.add_argument("--week", action="store_true")
    p.add_argument("--last-week", action="store_true")
    p.add_argument("--day", metavar="YYYY-MM-DD")
    p.add_argument("--week-day", metavar="YYYY-MM-DD")
    p.add_argument("--html", action="store_true")
    p.add_argument("--out", metavar="PATH")
    p.add_argument("--open", action="store_true")
    p.add_argument("--auto-daily", action="store_true")
    p.add_argument("--auto-weekly", action="store_true")
    args = p.parse_args()

    if args.auto_daily or args.auto_weekly:
        return _auto_mode(args)

    from pycal.report import render_daily_markdown, render_weekly_markdown

    if args.week or args.last_week or args.week_day:
        d = _resolve_week_day(args)
        md = render_weekly_markdown(d)
        _emit(md, args, "weekly", d.isoformat())
        return 0

    d = _resolve_day(args)
    md = render_daily_markdown(d)
    _emit(md, args, "daily", d.isoformat())
    return 0


if __name__ == "__main__":
    sys.exit(main())