"""日报 / 周报测试 — 覆盖解析、统计、渲染。

跑: python tests/test_report.py
"""

from __future__ import annotations
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = ""):
    if cond:
        PASS.append(name)
        print(f"  [OK]   {name}")
    else:
        FAIL.append(f"{name} {extra}")
        print(f"  [FAIL] {name} {extra}")


def _fixture(con):
    """造一周的测试数据。"""
    from pycal.db.models import CalendarItem
    from pycal.db.repository import upsert_item
    base = date(2026, 10, 19)      # 周一
    for i in range(7):
        d = base + timedelta(days=i)
        iso = d.isoformat()
        # 周一：4 行，2 完成
        upsert_item(con, CalendarItem(
            item_id=f"m{i}", date=iso, type=0, title=f"第{i+1}天计划",
            content="[+] 晨会\n[ ] 写文档\n[ ] code review\n[+] 买咖啡"))
        if i % 2 == 0:
            upsert_item(con, CalendarItem(
                item_id=f"r{i}", date=iso, type=1, title="每天跑步",
                content="[ ] 3km", is_completed=1 if i < 2 else 0))
        if i == 2:
            upsert_item(con, CalendarItem(
                item_id=f"c{i}", date=iso, type=3, title="项目截止"))
            con.execute(
                "INSERT INTO countdowns(item_id,target_date,repeat_yearly) "
                "VALUES (?,?,0)", (f"c{i}", "2026-11-11"))
    return base


def test_parse():
    print("\n=== 1. [+] 完成标记解析 ===")
    from pycal.report import _parse_content
    from desktopcal_search import line_is_completed
    r = _parse_content("[+] 晨会 30min\n[ ] 写周报\n普通行")
    check("3 行解析", len(r) == 3, f"got {len(r)}")
    check("[+] 标记为完成", r[0].completed and r[0].text == "晨会 30min",
          f"got {r[0]}")
    check("[ ] 标记为未完成", not r[1].completed and r[1].text == "[ ] 写周报",
          f"got {r[1]}")
    check("普通行", not r[2].completed and r[2].text == "普通行")
    # 跟搜索层保持一致
    check("与搜索层一致: [+]", line_is_completed("[+] x"))
    check("与搜索层一致: [ ]", not line_is_completed("[ ] x"))


def test_daily(con):
    print("\n=== 2. 日报统计 ===")
    from pycal.report import load_day, render_daily_markdown
    d = date(2026, 10, 19)     # 周一, fixture base
    stats = load_day(d, con)
    check("有条目", stats.has_items)
    check("总行数 = 4 (格子)", stats.buckets[0].total == 4,
          f"got {stats.buckets[0].total}")
    check("完成 2 行", stats.buckets[0].done == 2,
          f"got {stats.buckets[0].done}")
    check("含重复条目", any(b.type_id == 1 for b in stats.buckets))
    md = render_daily_markdown(d, con)
    check("日报含标题", "日报" in md)
    check("日报含完成率", "完成率" in md)
    check("日报含 task list", "- [x]" in md and "- [ ]" in md)
    check("日报含农历", any(x in md for x in ("八月", "九月", "十月", "初", "廿", "十")))


def test_weekly(con):
    print("\n=== 3. 周报统计 ===")
    from pycal.report import (load_week, week_bounds, render_weekly_markdown,
                              extract_keywords)
    base = date(2026, 10, 19)
    days = load_week(base, con)
    check("7 天", len(days) == 7, f"got {len(days)}")
    start, end = week_bounds(base)
    check("周一起算", start.weekday() == 0, f"got {start.weekday()}")
    check("周日结束", end.weekday() == 6 and (end - start).days == 6)
    total = sum(x.total_lines for x in days)
    check("周总行数 > 0", total > 0, f"got {total}")
    md = render_weekly_markdown(base, con)
    check("周报含总览", "本周总览" in md)
    check("周报含每日矩阵", "每日完成情况" in md)
    check("周报含类型分布", "类型分布" in md)
    check("周报含 ISO 周", "第" in md and "周" in md)
    check("周报含明细", "逐日明细" in md)
    kws = extract_keywords(days)
    check("关键词提取", isinstance(kws, list))


def test_countdown(con):
    print("\n=== 4. 倒数日 ===")
    from pycal.report import upcoming_countdowns
    cds = upcoming_countdowns(within_days=365, con=con)
    check("找到倒数日", len(cds) >= 1, f"got {len(cds)}")
    if cds:
        title, tgt, left = cds[0]
        check("days_left 是整数", isinstance(left, int))
        check("days_left 合理", -400 <= left <= 400, f"got {left}")


def test_html(con):
    print("\n=== 5. HTML 导出 ===")
    from pycal.report import render_daily_markdown, render_html
    md = render_daily_markdown(date(2026, 10, 19), con)
    html = render_html(md, "测试")
    check("含 DOCTYPE", html.startswith("<!DOCTYPE html>"))
    check("含 charset", 'charset="utf-8"' in html)
    check("含 h1", "<h1>" in html)
    check("含 table", "<table>" in html)
    check("含 checkbox", 'type="checkbox"' in html)
    check("含 print CSS", "@media print" in html)
    check("无未转义的 <font>", "<font" not in html)


def test_empty(con):
    print("\n=== 6. 空数据优雅降级 ===")
    from pycal.report import render_daily_markdown, render_weekly_markdown
    empty_day = date(2030, 1, 1)
    md = render_daily_markdown(empty_day, con)
    check("空日报不崩", "暂无日程" in md, md[:80])
    md2 = render_weekly_markdown(empty_day, con)
    check("空周报不崩", "周报" in md2)


def main() -> int:
    print("=" * 70)
    print("PyCal 报告模块测试")
    print("=" * 70)
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    from pycal.db.connection import _make_conn
    con = _make_conn(tmp)
    try:
        _fixture(con)
        test_parse()
        test_daily(con)
        test_weekly(con)
        test_countdown(con)
        test_html(con)
        test_empty(con)
    finally:
        try:
            con.close()
        except Exception:
            pass
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(tmp + suffix)
            except OSError:
                pass

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("\n失败项:")
        for f in FAIL:
            print(f"  x {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())