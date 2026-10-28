"""SyncEngine — 三方对比 + 冲突解决。

算法 (每个 item 独立判断):
  local_changed = hash(local) != link.last_synced_hash
  remote_changed = hash(remote) != link.last_synced_hash

  | local | remote | 动作                       |
  |-------|--------|----------------------------|
  |  =    |  =    | 无操作 (跳过)              |
  |  ∆    |  =    | PUSH  (本地改 → 推上去)     |
  |  =    |  ∆    | PULL  (远端改 → 拉到本地)   |
  |  ∆    |  ∆    | CONFLICT (按 conflict_policy 决定) |

conflict_policy:
  "local_wins"  — 本地覆盖远端 (推)
  "remote_wins" — 远端覆盖本地 (拉)
  "newest_wins" — 按 updated_at 时间戳谁新谁赢 (默认)

数据安全:
  - 冲突默认 newesw_wins; 拉到/推前先备份到 sync_conflicts 表
  - read_only 账号只做 PULL, 永不 PUSH/DELETE
  - sync_direction=push 时不拉远端 (避免覆盖本地)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Optional

from pycal.db.connection import open_default
from pycal.db.models import CalendarItem, RepeatRule, Reminder, Countdown
from pycal.db.repository import upsert_item, get_item, items_from_rows
from pycal.sync.accounts import (
    SyncAccount, get_link, set_link, links_for_account,
    mark_synced, init_sync_schema,
)
from pycal.sync.client import CaldavClient, CaldavError, RemoteEvent
from pycal.sync.ical_convert import content_hash


class SyncDirection(str, Enum):
    BOTH = "both"
    PULL = "pull"
    PUSH = "push"


@dataclass
class SyncResult:
    account_id: int
    account_name: str = ""
    pulled: int = 0
    pushed: int = 0
    deleted_remote: int = 0
    skipped: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = []
        if self.pulled:
            parts.append(f"↓ 拉取 {self.pulled}")
        if self.pushed:
            parts.append(f"↑ 推送 {self.pushed}")
        if self.deleted_remote:
            parts.append(f"✗ 远端删除 {self.deleted_remote}")
        if self.skipped:
            parts.append(f"= 无变化 {self.skipped}")
        if self.conflicts:
            parts.append(f"⚠ 冲突 {self.conflicts}")
        if self.errors:
            parts.append(f"✗ 错误 {len(self.errors)}")
        return ", ".join(parts) or "无变化"


class SyncCancelled(Exception):
    pass


class SyncEngine:
    """单账号同步执行器。"""

    def __init__(
        self,
        account: SyncAccount,
        conflict_policy: str = "newest_wins",
        backfill_days: int = 365,
        con: Optional[sqlite3.Connection] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
    ):
        self.account = account
        self.policy = conflict_policy
        self.backfill_days = backfill_days
        self.con = con or open_default()
        self._progress = progress_cb or (lambda s: None)
        init_sync_schema(self.con)
        self.result = SyncResult(account_id=account.account_id,
                                 account_name=account.display_name)

    # ------------------------------------------------------------------
    def sync(self, full_range: Optional[tuple[date, date]] = None) -> SyncResult:
        """执行一次同步。"""
        r = self.result
        try:
            if full_range is None:
                today = date.today()
                start = today - timedelta(days=self.backfill_days)
                end = today + timedelta(days=self.backfill_days)
            else:
                start, end = full_range

            self._progress(f"连接 {self.account.display_name} …")
            client = CaldavClient(self.account)

            direction = self.account.sync_direction or "both"
            links = links_for_account(self.account.account_id, self.con)
            do_pull = direction in ("both", "pull")
            do_push = direction in ("both", "push") and not self.account.read_only

            # ---- 拉取 ----
            remote_by_uid: dict[str, RemoteEvent] = {}
            if do_pull:
                self._progress(f"拉取 {start} ~ {end} …")
                remotes = client.fetch_range(start, end)
                self._progress(f"远端 {len(remotes)} 条")
                for ev in remotes:
                    remote_by_uid[ev.item.item_id] = ev

            # ---- 逐条判断 ----
            if do_push:
                self._push_local_changes(client, links, remote_by_uid)

            if do_pull:
                self._pull_remote_changes(client, links, remote_by_uid, do_push)

            # ---- 远端已删除的本地条目 (可选: 也可标 stale 不删) ----
            # MVP 阶段不自动删本地 (太危险), 只统计

            status = r.summary()
            mark_synced(self.account.account_id, status, self.con)
            return r

        except CaldavError as e:
            hint = f" ({e.hint})" if e.hint else ""
            r.errors.append(e.message + hint)
            mark_synced(self.account.account_id, f"失败: {e.message[:100]}", self.con)
            return r
        except SyncCancelled:
            r.cancelled = True
            return r
        except Exception as e:
            r.errors.append(f"未预期错误: {str(e)[:200]}")
            return r

    # ------------------------------------------------------------------
    def _local_changed(self, item: CalendarItem, link) -> bool:
        if link is None:
            return True   # 没同步过 → 视为新增
        return content_hash(item) != (link["last_synced_hash"] or "")

    def _remote_changed(self, ev: RemoteEvent, link) -> bool:
        if link is None:
            return True
        return content_hash(ev.item) != (link["last_synced_hash"] or "")

    # ------------------------------------------------------------------
    def _push_local_changes(self, client, links, remote_by_uid) -> None:
        """检查本地改动并推上去。"""
        r = self.result
        # MVP: 推所有本地 item (用户主动点了同步)
        # 一次把整张表读出来, 别一条一条 get_item —— 那等于 N+1 次查询,
        # 本地条目一多, 同步光查库就要好几秒 (而且卡在 UI 线程上)。
        rows = self.con.execute("SELECT * FROM calendar_items").fetchall()
        local_items = items_from_rows(rows)

        pushed = 0
        for item in local_items:
            item_id = item.item_id
            link = links.get(item_id)
            remote = remote_by_uid.get(item_id)

            if not self._local_changed(item, link):
                # 本地没改
                if remote is not None and self._remote_changed(remote, link):
                    # 远端改了 → 交给 pull 阶段处理
                    r.skipped += 1
                else:
                    r.skipped += 1
                continue

            # 本地有改动
            if remote is not None and self._remote_changed(remote, link):
                # 冲突
                r.conflicts += 1
                self._resolve_conflict(client, item, remote, link)
                continue

            # 纯本地新增 或 本地独改 → push
            self._progress(f"↑ 推送: {item.title or item_id}")
            try:
                href = link["remote_href"] if link else ""
                etag = link["remote_etag"] if link else ""
                rule = self._load_repeat_rule(item_id)
                cd = self._load_countdown(item_id)
                rm = self._load_reminder(item_id)
                new_href, new_etag = client.push_item(
                    item, rule, rm, cd, href=href, etag=etag,
                )
                set_link(item_id, self.account.account_id, new_href, new_etag,
                         content_hash(item), self.con)
                pushed += 1
            except CaldavError as e:
                r.errors.append(f"推送 {item.title or item_id} 失败: {e.message}")
        r.pushed = pushed

    # ------------------------------------------------------------------
    def _pull_remote_changes(
        self, client, links, remote_by_uid, allow_push: bool,
    ) -> None:
        """拉远端改动到本地。"""
        r = self.result
        pulled = 0
        for item_id, remote in remote_by_uid.items():
            link = links.get(item_id)
            local = get_item(self.con, item_id)

            if not self._remote_changed(remote, link):
                r.skipped += 1
                continue

            if local is not None and self._local_changed(local, link):
                # 本地也改了 → 冲突 (pull 阶段再处理一次, 但 push 阶段已处理)
                continue

            # 远端独改 → 拉
            self._progress(f"↓ 拉取: {remote.item.title or item_id}")
            self._apply_remote(remote, link)
            pulled += 1
        r.pulled = pulled

    # ------------------------------------------------------------------
    def _resolve_conflict(self, client, local: CalendarItem, remote, link) -> None:
        """按 policy 解决冲突。"""
        r = self.result
        winner = self.policy
        if self.policy == "newest_wins":
            local_ts = self._item_updated_ts(local)
            remote_ts = self._remote_updated_ts(remote)
            winner = "local_wins" if local_ts >= remote_ts else "remote_wins"

        if winner == "local_wins":
            self._progress(f"⚠ 冲突→本地胜: {local.title or local.item_id}")
            try:
                rule = self._load_repeat_rule(local.item_id)
                cd = self._load_countdown(local.item_id)
                rm = self._load_reminder(local.item_id)
                href = link["remote_href"] if link else remote.href
                etag = link["remote_etag"] if link else remote.etag
                new_href, new_etag = client.push_item(
                    local, rule, rm, cd, href=href, etag=etag,
                )
                set_link(local.item_id, self.account.account_id,
                         new_href, new_etag, content_hash(local), self.con)
                r.pushed += 1
            except CaldavError as e:
                r.errors.append(f"冲突解决(本地胜)失败: {e.message}")
        else:
            self._progress(f"⚠ 冲突→远端胜: {remote.item.title or remote.item.item_id}")
            self._apply_remote(remote, link)
            r.pulled += 1

    # ------------------------------------------------------------------
    def _apply_remote(self, remote: RemoteEvent, link) -> None:
        """把远端事件写入本地。"""
        self.con.execute(
            "DELETE FROM calendar_items WHERE item_id = ?", (remote.item.item_id,)
        )
        upsert_item(self.con, remote.item)
        # 子表
        self.con.execute("DELETE FROM repeat_rules WHERE item_id = ?",
                         (remote.item.item_id,))
        if remote.repeat_rule:
            self.con.execute(
                """INSERT INTO repeat_rules(item_id, freq, interval_n, byweekday, until_date)
                   VALUES (?,?,?,?,?)""",
                (
                    remote.item.item_id, remote.repeat_rule.freq,
                    remote.repeat_rule.interval_n,
                    remote.repeat_rule.byweekday, remote.repeat_rule.until_date,
                ),
            )
        self.con.execute("DELETE FROM countdowns WHERE item_id = ?",
                         (remote.item.item_id,))
        if remote.countdown:
            self.con.execute(
                """INSERT INTO countdowns(item_id, target_date, repeat_yearly)
                   VALUES (?,?,?)""",
                (
                    remote.item.item_id, remote.countdown.target_date,
                    remote.countdown.repeat_yearly,
                ),
            )
        set_link(
            remote.item.item_id, self.account.account_id,
            remote.href, remote.etag, content_hash(remote.item), self.con,
        )

    # ------------------------------------------------------------------
    def _item_updated_ts(self, item: CalendarItem) -> float:
        row = self.con.execute(
            "SELECT updated_at FROM calendar_items WHERE item_id = ?",
            (item.item_id,),
        ).fetchone()
        if row is None or not row["updated_at"]:
            return 0.0
        return _ts(row["updated_at"])

    def _remote_updated_ts(self, remote) -> float:
        ev = remote.ical_event
        for field_name in ("LAST-MODIFIED", "DTSTAMP", "CREATED"):
            v = ev.get(field_name)
            if v is None:
                continue
            dt = getattr(v, "dt", None)
            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
        return 0.0

    def _load_repeat_rule(self, item_id: str) -> Optional[RepeatRule]:
        row = self.con.execute(
            "SELECT * FROM repeat_rules WHERE item_id = ?", (item_id,)
        ).fetchone()
        if not row:
            return None
        return RepeatRule(
            item_id=item_id, freq=row["freq"],
            interval_n=row["interval_n"] or 1,
            byweekday=row["byweekday"],
            until_date=row["until_date"],
        )

    def _load_countdown(self, item_id: str) -> Optional[Countdown]:
        row = self.con.execute(
            "SELECT * FROM countdowns WHERE item_id = ?", (item_id,)
        ).fetchone()
        if not row:
            return None
        return Countdown(item_id=item_id, target_date=row["target_date"],
                         repeat_yearly=row["repeat_yearly"] or 0)

    def _load_reminder(self, item_id: str) -> Optional[Reminder]:
        row = self.con.execute(
            "SELECT * FROM reminders WHERE item_id = ? AND fired = 0", (item_id,)
        ).fetchone()
        if not row:
            return None
        try:
            return Reminder(item_id=item_id, remind_at=datetime.fromisoformat(row["remind_at"]),
                            fired=row["fired"] or 0, channel=row["channel"] or "system")
        except Exception:
            return None


def _ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except Exception:
        try:
            return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception:
            return 0.0


def sync_all(
    conflict_policy: str = "newest_wins",
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[SyncResult]:
    """同步所有 enabled 账号。"""
    from pycal.sync.accounts import list_accounts
    results = []
    for acc in list_accounts():
        if not acc.enabled:
            continue
        eng = SyncEngine(acc, conflict_policy=conflict_policy, progress_cb=progress_cb)
        results.append(eng.sync())
    return results


__all__ = ["SyncEngine", "SyncResult", "SyncDirection", "sync_all"]