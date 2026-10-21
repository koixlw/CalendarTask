"""同步账号 CRUD + 凭据混淆。

凭据安全:
  - 用 base64 + 简单 XOR 打乱, 只防"随手打开 DB 看到明文", 不是强加密
  - 真正敏感的生产环境应改用 Windows Credential Manager / keyring
  - 强调: 应用专用密码泄露风险有限 (可单独吊销, 不影响主账号)
"""

from __future__ import annotations
import base64
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from pycal.db.connection import open_default
from pycal.sync.presets import get_preset


# XOR 密钥 — 每个安装随机生成会更安全, 这里用固定值保证跨会话可解密
_XOR_KEY = b"pycal-sync-v1"


def _obfuscate(plain: str) -> str:
    if not plain:
        return ""
    data = plain.encode("utf-8")
    xored = bytes(b ^ _XOR_KEY[i % len(_XOR_KEY)] for i, b in enumerate(data))
    return base64.b64encode(xored).decode("ascii")


def _deobfuscate(encoded: str) -> str:
    if not encoded:
        return ""
    try:
        xored = base64.b64decode(encoded.encode("ascii"))
        data = bytes(b ^ _XOR_KEY[i % len(_XOR_KEY)] for i, b in enumerate(xored))
        return data.decode("utf-8")
    except Exception:
        return ""


@dataclass
class SyncAccount:
    account_id: int
    provider: str                  # google / icloud / outlook / nextcloud
    display_name: str
    base_url: str
    username: str
    password: str                  # 运行时已解密
    calendar_url: str = ""         # 选定/发现的 calendar collection URL
    enabled: int = 1
    last_sync: Optional[str] = None
    last_status: str = ""
    read_only: int = 0             # 只读模式: 只拉不推 (避免误改云端)
    sync_direction: str = "both"   # both / pull / push


# ============================================================================
# Schema
# ============================================================================
ACCOUNT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sync_accounts (
    account_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    provider       TEXT NOT NULL,
    display_name   TEXT NOT NULL DEFAULT '',
    base_url       TEXT NOT NULL,
    username       TEXT NOT NULL,
    password_enc   TEXT NOT NULL DEFAULT '',
    calendar_url   TEXT NOT NULL DEFAULT '',
    enabled        INTEGER NOT NULL DEFAULT 1,
    read_only      INTEGER NOT NULL DEFAULT 0,
    sync_direction TEXT NOT NULL DEFAULT 'both',
    last_sync      TIMESTAMP,
    last_status    TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 记录每个 item 对应的远端 href, 用于双向对比
CREATE TABLE IF NOT EXISTS sync_links (
    item_id        TEXT NOT NULL,
    account_id     INTEGER NOT NULL,
    remote_href    TEXT NOT NULL DEFAULT '',
    remote_etag    TEXT NOT NULL DEFAULT '',
    last_synced_at TIMESTAMP,
    last_synced_hash TEXT,
    PRIMARY KEY (item_id, account_id),
    FOREIGN KEY (account_id) REFERENCES sync_accounts(account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sync_links_acct ON sync_links(account_id);
"""


def init_sync_schema(con: Optional[sqlite3.Connection] = None) -> sqlite3.Connection:
    c = con or open_default()
    c.executescript(ACCOUNT_SCHEMA_SQL)
    return c


# ============================================================================
# CRUD
# ============================================================================
def _row_to_account(row: sqlite3.Row) -> SyncAccount:
    return SyncAccount(
        account_id=row["account_id"],
        provider=row["provider"],
        display_name=row["display_name"] or "",
        base_url=row["base_url"],
        username=row["username"],
        password=_deobfuscate(row["password_enc"] or ""),
        calendar_url=row["calendar_url"] or "",
        enabled=row["enabled"] or 0,
        last_sync=row["last_sync"],
        last_status=row["last_status"] or "",
        read_only=row["read_only"] or 0,
        sync_direction=row["sync_direction"] or "both",
    )


def create_account(
    provider: str, base_url: str, username: str, password: str,
    display_name: str = "", calendar_url: str = "",
    read_only: int = 0, sync_direction: str = "both",
    con: Optional[sqlite3.Connection] = None,
) -> int:
    c = init_sync_schema(con)
    spec = get_preset(provider)
    name = display_name or spec.label
    cur = c.execute(
        """INSERT INTO sync_accounts
           (provider, display_name, base_url, username, password_enc,
            calendar_url, read_only, sync_direction)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            provider, name, base_url, username, _obfuscate(password),
            calendar_url, read_only, sync_direction,
        ),
    )
    return cur.lastrowid


def list_accounts(con: Optional[sqlite3.Connection] = None) -> list[SyncAccount]:
    c = init_sync_schema(con)
    rows = c.execute(
        "SELECT * FROM sync_accounts ORDER BY enabled DESC, account_id"
    ).fetchall()
    return [_row_to_account(r) for r in rows]


def get_account(account_id: int, con: Optional[sqlite3.Connection] = None) -> Optional[SyncAccount]:
    c = init_sync_schema(con)
    row = c.execute(
        "SELECT * FROM sync_accounts WHERE account_id = ?", (account_id,)
    ).fetchone()
    return _row_to_account(row) if row else None


def update_account(account_id: int, con=None, **fields) -> None:
    c = init_sync_schema(con)
    allowed = {
        "provider", "display_name", "base_url", "username",
        "calendar_url", "enabled", "read_only", "sync_direction",
        "last_status",
    }
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k} = ?")
        vals.append(v)
    if "password" in fields:
        sets.append("password_enc = ?")
        vals.append(_obfuscate(fields["password"]))
    if not sets:
        return
    vals.append(account_id)
    c.execute(f"UPDATE sync_accounts SET {', '.join(sets)} WHERE account_id = ?", vals)


def mark_synced(account_id: int, status: str, con: Optional[sqlite3.Connection] = None) -> None:
    c = init_sync_schema(con)
    c.execute(
        "UPDATE sync_accounts SET last_sync = CURRENT_TIMESTAMP, last_status = ? "
        "WHERE account_id = ?",
        (status[:200], account_id),
    )


def delete_account(account_id: int, con: Optional[sqlite3.Connection] = None) -> None:
    c = init_sync_schema(con)
    c.execute("DELETE FROM sync_links WHERE account_id = ?", (account_id,))
    c.execute("DELETE FROM sync_accounts WHERE account_id = ?", (account_id,))


# ============================================================================
# sync_links
# ============================================================================
def get_link(item_id: str, account_id: int, con: Optional[sqlite3.Connection] = None):
    c = init_sync_schema(con)
    return c.execute(
        "SELECT * FROM sync_links WHERE item_id = ? AND account_id = ?",
        (item_id, account_id),
    ).fetchone()


def set_link(
    item_id: str, account_id: int, remote_href: str, remote_etag: str,
    content_hash: str, con: Optional[sqlite3.Connection] = None,
) -> None:
    c = init_sync_schema(con)
    c.execute(
        """INSERT OR REPLACE INTO sync_links
           (item_id, account_id, remote_href, remote_etag, last_synced_at, last_synced_hash)
           VALUES (?,?,?,?,CURRENT_TIMESTAMP,?)""",
        (item_id, account_id, remote_href, remote_etag, content_hash),
    )


def links_for_account(account_id: int, con: Optional[sqlite3.Connection] = None) -> dict[str, sqlite3.Row]:
    """返回 {item_id: row}。"""
    c = init_sync_schema(con)
    rows = c.execute(
        "SELECT * FROM sync_links WHERE account_id = ?", (account_id,)
    ).fetchall()
    return {r["item_id"]: r for r in rows}


__all__ = [
    "SyncAccount", "ACCOUNT_SCHEMA_SQL", "init_sync_schema",
    "create_account", "list_accounts", "get_account", "update_account",
    "mark_synced", "delete_account",
    "get_link", "set_link", "links_for_account",
]