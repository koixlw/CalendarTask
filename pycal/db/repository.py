"""Repository 层 — 给 UI / 业务层调用的 CRUD。

复用 sqlite3.Row, 但暴露为 CalendarItem 等 dataclass。
"""

from __future__ import annotations
import sqlite3
from typing import Optional, Iterable

from pycal.db.models import (
    CalendarItem, Note, RepeatRule, Reminder, Countdown,
)


def _row_to_item(row: sqlite3.Row) -> CalendarItem:
    return CalendarItem(
        item_id=row["item_id"],
        date=row["date"],
        type=row["type"] or 0,
        title=row["title"] or "",
        content=row["content"] or "",
        color_ref=row["color_ref"] or 0,
        is_completed=row["is_completed"] or 0,
        completed_dates=row["completed_dates"],
        priority=row["priority"] or 0,
        group_id=row["group_id"],
        subaccount_id=row["subaccount_id"] or 0,
    )


def list_items_by_date(con: sqlite3.Connection, date_iso: str) -> list[CalendarItem]:
    cur = con.execute(
        "SELECT * FROM calendar_items WHERE date = ? AND type != 4 ORDER BY type, item_id",
        (date_iso,),
    )
    return [_row_to_item(r) for r in cur.fetchall()]


def list_items_in_range(
    con: sqlite3.Connection, start_iso: str, end_iso: str,
) -> list[CalendarItem]:
    cur = con.execute(
        "SELECT * FROM calendar_items WHERE date BETWEEN ? AND ? ORDER BY date, type",
        (start_iso, end_iso),
    )
    return [_row_to_item(r) for r in cur.fetchall()]


def get_item(con: sqlite3.Connection, item_id: str) -> Optional[CalendarItem]:
    cur = con.execute("SELECT * FROM calendar_items WHERE item_id = ?", (item_id,))
    row = cur.fetchone()
    return _row_to_item(row) if row else None


def upsert_item(con: sqlite3.Connection, item: CalendarItem) -> None:
    con.execute(
        """INSERT INTO calendar_items
           (item_id, date, type, title, content, color_ref,
            is_completed, completed_dates, priority, group_id, subaccount_id,
            updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(item_id) DO UPDATE SET
             date=excluded.date, type=excluded.type,
             title=excluded.title, content=excluded.content,
             color_ref=excluded.color_ref, is_completed=excluded.is_completed,
             completed_dates=excluded.completed_dates, priority=excluded.priority,
             group_id=excluded.group_id, subaccount_id=excluded.subaccount_id,
             updated_at=CURRENT_TIMESTAMP""",
        (
            item.item_id, item.date, item.type, item.title, item.content,
            item.color_ref, item.is_completed, item.completed_dates,
            item.priority, item.group_id, item.subaccount_id,
        ),
    )


def delete_item(con: sqlite3.Connection, item_id: str) -> None:
    con.execute("DELETE FROM calendar_items WHERE item_id = ?", (item_id,))


# ───── Note ─────

def _row_to_note(row: sqlite3.Row) -> Note:
    return Note(
        note_id=row["note_id"],
        title=row["title"] or "",
        content=row["content"] or "",
        color_ref=row["color_ref"] or 0,
        pinned=row["pinned"] or 0,
        pos_x=row["pos_x"] or 100,
        pos_y=row["pos_y"] or 100,
        width=row["width"] or 320,
        height=row["height"] or 240,
    )


def list_notes(con: sqlite3.Connection, include_deleted: bool = False) -> list[Note]:
    sql = "SELECT * FROM notes"
    if not include_deleted:
        sql += " WHERE deleted_at IS NULL"
    sql += " ORDER BY pinned DESC, updated_at DESC"
    return [_row_to_note(r) for r in con.execute(sql).fetchall()]


def upsert_note(con: sqlite3.Connection, note: Note) -> int:
    if note.note_id is None:
        cur = con.execute(
            """INSERT INTO notes
               (title, content, color_ref, pinned, pos_x, pos_y, width, height)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                note.title, note.content, note.color_ref, note.pinned,
                note.pos_x, note.pos_y, note.width, note.height,
            ),
        )
        note.note_id = cur.lastrowid
        return note.note_id
    con.execute(
        """UPDATE notes SET
             title=?, content=?, color_ref=?, pinned=?,
             pos_x=?, pos_y=?, width=?, height=?,
             updated_at=CURRENT_TIMESTAMP
           WHERE note_id=?""",
        (
            note.title, note.content, note.color_ref, note.pinned,
            note.pos_x, note.pos_y, note.width, note.height, note.note_id,
        ),
    )
    return note.note_id


def soft_delete_note(con: sqlite3.Connection, note_id: int) -> None:
    con.execute(
        "UPDATE notes SET deleted_at=CURRENT_TIMESTAMP WHERE note_id=?",
        (note_id,),
    )


# ───── Reminders ─────

def add_reminder(
    con: sqlite3.Connection,
    item_id: str,
    remind_at_iso: str,
    channel: str = "system",
) -> int:
    cur = con.execute(
        "INSERT INTO reminders(item_id, remind_at, channel) VALUES (?,?,?)",
        (item_id, remind_at_iso, channel),
    )
    return cur.lastrowid


def due_reminders(
    con: sqlite3.Connection, now_iso: str,
) -> list[tuple[int, str, str]]:
    """返回 (reminder_id, item_id, remind_at)。"""
    cur = con.execute(
        "SELECT reminder_id, item_id, remind_at FROM reminders "
        "WHERE fired = 0 AND remind_at <= ? ORDER BY remind_at",
        (now_iso,),
    )
    return [(r["reminder_id"], r["item_id"], r["remind_at"]) for r in cur.fetchall()]


def mark_reminder_fired(con: sqlite3.Connection, reminder_id: int) -> None:
    con.execute(
        "UPDATE reminders SET fired = 1 WHERE reminder_id = ?",
        (reminder_id,),
    )
