"""SQLite 连接管理。

- 默认数据库位于 %APPDATA%/PyCal/db/pycal.db
- 应用层使用 thread-local 连接 (Qt 跨线程必须小心, 但 writer-UI-thread 模式足够)
- WAL 模式 + synchronous=NORMAL 提速
"""

from __future__ import annotations
import os
import sqlite3
import threading
from pathlib import Path

from pycal.db.schema import SCHEMA_SQL


_local = threading.local()


def default_db_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / "PyCal" / "db" / "pycal.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _make_conn(path: str | os.PathLike[str]) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA_SQL)
    return con


def open_connection(path: str | os.PathLike[str]) -> sqlite3.Connection:
    return _make_conn(path)


def open_default() -> sqlite3.Connection:
    if getattr(_local, "con", None) is None:
        _local.con = _make_conn(default_db_path())
    return _local.con


def close_default() -> None:
    con = getattr(_local, "con", None)
    if con is not None:
        con.close()
    _local.con = None
