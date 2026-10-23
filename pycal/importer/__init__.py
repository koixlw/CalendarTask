"""从 DesktopCal calendar.db 导入数据到 PyCal。

DesktopCal 的表名我在 CalendarTask/Db/calendar.db 上读不到 (拒绝访问 PII),
但参考它的 layout 已能用 Liqian/DkCalendar 公开的 schema:

  item_table:   主条目 (id PRIMARY KEY, type, content, dateline, ...)
  event_table:  事件 (unionid PRIMARY KEY, type, content, ...)

通常 DesktopCal 的 schema 大致是:
  item_table(
    id INTEGER PRIMARY KEY,
    type INTEGER,                   -- 0=格子 1=重复 2=提醒 3=倒数日 4=课程表
    content TEXT,                   -- 多行内容, [+]=完成
    dateline INTEGER,               -- YYYYMMDD 整数
    title TEXT,
    color INTEGER,
    groupId TEXT,
    has_completed INTEGER
  )

代码先 reflection 出真实 schema (查询 sqlite_master) 再做映射, 没有硬编码列名。
"""

from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Iterable

from pycal.db.connection import open_connection, default_db_path
from pycal.db.repository import upsert_item
from pycal.db.models import CalendarItem


def _table_columns(con: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    cur = con.execute(f"PRAGMA table_info({table})")
    return [(r["name"], r["type"]) for r in cur.fetchall()]


def _pick(columns: list[tuple[str, str]], *names: str) -> str | None:
    """从 PRAGMA 列里找出第一个大小写不敏感匹配的名字。"""
    lower = {c[0].lower(): c[0] for c in columns}
    for n in names:
        if n.lower() in lower:
            return lower[n.lower()]
    return None


def _date_int_to_iso(s: str | int | None) -> str | None:
    """YYYYMMDD -> YYYY-MM-DD。"""
    if s is None or s == "":
        return None
    s = str(s).strip()
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _dateline_to_iso(d: int | None) -> str | None:
    if d is None or d == 0:
        return None
    s = str(d)
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return None


def _iter_source_rows(con: sqlite3.Connection, table: str) -> Iterable[sqlite3.Row]:
    try:
        cur = con.execute(f"SELECT * FROM {table}")
        return cur.fetchall()
    except sqlite3.OperationalError:
        return []


def import_from_desktopcal(
    source_db: str | Path, target_db: str | Path | None = None,
) -> int:
    """从 DesktopCal 导入到 PyCal。返回导入的条目数。

    源文件不存在或不可读时返回 0 (不抛异常), 方便 GUI 直接调用。
    """
    if not Path(source_db).exists():
        print(f"[!] 源数据库不存在: {source_db}")
        return 0
    try:
        source = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    except sqlite3.Error as e:
        print(f"[!] 无法打开源数据库: {e}")
        return 0
    source.row_factory = sqlite3.Row
    try:
        target = open_connection(target_db or default_db_path())

        # 找到主表
        tables = {r["name"].lower() for r in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        main_table = None
        for cand in ("item_table", "item", "calendar_items"):
            if cand in tables:
                main_table = cand
                break
        if main_table is None:
            print("[!] DesktopCal DB 里没找到 item_table / item / calendar_items")
            return 0

        cols = _table_columns(source, main_table)
        id_col   = _pick(cols, "id", "unionid", "item_id", "primary_id")
        type_col = _pick(cols, "type", "item_type")
        date_col = _pick(cols, "dateline", "date", "date_yyyymmdd")
        title_col = _pick(cols, "title", "name")
        content_col = _pick(cols, "content", "description", "body")
        color_col = _pick(cols, "color", "color_ref", "text_color")
        group_col = _pick(cols, "groupId", "group_id")
        subs_col = _pick(cols, "subsId", "subaccount_id")
        completed_col = _pick(cols, "has_completed", "is_completed")

        n = 0
        for r in _iter_source_rows(source, main_table):
            t = (r[type_col] if type_col else 0) or 0
            # 课程表不导入
            if t == 4:
                continue
            date_iso = None
            if date_col:
                v = r[date_col]
                if isinstance(v, int):
                    date_iso = _dateline_to_iso(v)
                elif isinstance(v, str) and v.isdigit() and len(v) == 8:
                    date_iso = _date_int_to_iso(v)
                elif isinstance(v, str):
                    date_iso = v
            item = CalendarItem(
                item_id=f"imp_{r[id_col]}" if id_col else f"imp_{n}",
                date=date_iso,
                type=t,
                title=(r[title_col] or "") if title_col else "",
                content=(r[content_col] or "") if content_col else "",
                color_ref=(r[color_col] or 0) if color_col else 0,
                is_completed=(r[completed_col] or 0) if completed_col else 0,
                group_id=(r[group_col]) if group_col and r[group_col] else None,
                subaccount_id=(r[subs_col] or 0) if subs_col else 0,
            )
            upsert_item(target, item)
            n += 1

        target.commit()
        return n
    finally:
        source.close()


def run_import(source_db: str | Path) -> int:
    """CLI 入口。"""
    return import_from_desktopcal(source_db)
