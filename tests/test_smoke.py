"""smoke test — 不依赖 Qt, 跑数据 + 搜索层。

跑法:
    python tests/test_smoke.py

覆盖:
  1. 各层 import 干净
  2. 数据库初始化
  3. CRUD (item / note / reminder)
  4. 搜索 9 个场景 (同 demo_search.py)
  5. importer 不报错 (无源 DB 时 gracefully)
"""

from __future__ import annotations
import os
import sys
import tempfile
import sqlite3
from datetime import datetime
from pathlib import Path

# 让父目录的 pycal/、desktopcal_search.py 可被发现
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_imports() -> None:
    from pycal import __version__
    from pycal.db import open_default, open_connection
    from pycal.db.models import CalendarItem, Note
    from pycal.db.repository import (
        list_items_by_date, upsert_item, delete_item,
        add_reminder, due_reminders,
    )
    from pycal.search import SearchEngine, SearchOptions
    from pycal.importer import import_from_desktopcal
    from pycal.desktop.wallpaper import render_calendar_panel
    print("[OK] imports")


def _cleanup(con, path: str) -> None:
    """Windows 下 SQLite WAL 会锁文件, 先关连接再删。"""
    try:
        con.close()
    except Exception:
        pass
    import gc, time
    gc.collect()
    for _ in range(3):
        try:
            os.unlink(path)
            return
        except PermissionError:
            time.sleep(0.1)
    for suffix in ("-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except OSError:
            pass


def test_db_init() -> None:
    """临时内存 DB 走一遍 schema。"""
    from pycal.db.connection import _make_conn
    from pycal.db.repository import upsert_item, list_items_by_date
    from pycal.db.models import CalendarItem

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    con = None
    try:
        con = _make_conn(tmp)
        item = CalendarItem(
            item_id="it-test-1", date="2026-10-21", type=0,
            title="测试任务", content="[ ] 写代码\n[ ] 跑步",
            color_ref=0xF12424,
        )
        upsert_item(con, item)
        rows = list_items_by_date(con, "2026-10-21")
        assert rows and rows[0].title == "测试任务"
        print(f"[OK] db_init ({len(rows)} row)")
    finally:
        _cleanup(con, tmp)


def test_search() -> None:
    """走 demo_search.py 同等场景。"""
    from pycal.db.connection import _make_conn
    from pycal.db.models import CalendarItem
    from pycal.db.repository import upsert_item
    from pycal.search import SearchEngine, SearchOptions

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        con = _make_conn(tmp)
        sample = [
            CalendarItem(
                item_id="20261021", date="2026-10-21", type=0,
                title="今日",
                content="[ ] 跑步 [+] 24分钟",
            ),
            CalendarItem(
                item_id="20261022", date="2026-10-22", type=0,
                title="颜色测试",
                content='[ ] 普通 [+] 重点 24 项 [+] <font color="#F12424">生日蛋糕</font>',
            ),
            CalendarItem(
                item_id="20100101", date="2010-01-01", type=1,
                title="重复",
                content="[+] 跑步 24 分钟",
                is_completed=1, completed_dates="2026-10-15",
            ),
            CalendarItem(
                item_id="timetable-x", date="2026-10-21", type=4,
                title="课程", content="{}",
            ),
        ]
        for it in sample:
            upsert_item(con, it)

        eng = SearchEngine(con)

        # 关键字 24
        hits = eng.search(SearchOptions(keyword="24"))
        assert len(hits) == 3, f"expected 3, got {len(hits)}"

        # 颜色 + 关键字 (色值数字误命中)
        hits = eng.search(SearchOptions(
            keyword="24", text_color=0xF12424,
        ))
        assert len(hits) == 0, f"should drop color-hex false positive, got {len(hits)}"

        # 完成度
        hits = eng.search(SearchOptions(completion=1))
        assert any(h.item_id == "20100101" for h in hits)
        assert not any(h.item_id == "20261022" for h in hits)

        # 课程表永远排除
        hits = eng.search(SearchOptions(keyword=""))
        ids = {h.item_id for h in hits}
        assert "timetable-x" not in ids

        print("[OK] search")
    finally:
        _cleanup(con, tmp)


def test_reminders() -> None:
    from pycal.db.connection import _make_conn
    from pycal.db.repository import add_reminder, due_reminders
    from pycal.db.models import CalendarItem
    from pycal.db.repository import upsert_item

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        con = _make_conn(tmp)
        upsert_item(con, CalendarItem(
            item_id="rem-1", date="2026-10-21", title="提醒测试",
        ))
        add_reminder(con, "rem-1", "2026-10-21T09:00:00")
        due = due_reminders(con, "2026-10-21T10:00:00")
        assert due and due[0][1] == "rem-1"
        print(f"[OK] reminders ({len(due)} due)")
    finally:
        _cleanup(con, tmp)


def test_importer_safe() -> None:
    from pycal.importer import import_from_desktopcal
    # 不存在的路径应优雅失败
    n = import_from_desktopcal("Z:/definitely/not/exist.db",
                               ":memory:")
    assert n == 0
    print("[OK] importer graceful on missing source")


def main() -> int:
    test_imports()
    test_db_init()
    test_search()
    test_reminders()
    test_importer_safe()
    print("\n[SUCCESS] all smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
