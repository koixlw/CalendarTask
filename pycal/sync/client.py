"""CaldavClient — 屏蔽 caldav 库差异, 暴露干净的上层 API。

职责:
  - 连接 + 认证 (账号密码 / app password)
  - 列出可用 calendar collection
  - 按时间窗拉取 VEVENT
  - 推送 (create / update / delete)
  - 返回远端 etag 以便增量同步
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from pycal.sync.accounts import SyncAccount
from pycal.sync.ical_convert import (
    ical_to_item, item_to_ical, ical_to_repeat_rule, ical_to_countdown,
)


class CaldavError(Exception):
    """CalDAV 层错误 — message 面向用户展示。"""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass
class RemoteEvent:
    """远端一条 VEVENT 的抽象。"""
    uid: str
    href: str
    etag: str
    ical_event: Any           # icalendar.Event
    item: Any                 # CalendarItem (已转换)
    repeat_rule: Any = None
    countdown: Any = None


class CaldavClient:
    """单个 CalDAV 账号的客户端。

    用法:
        with CaldavClient(account) as c:
            cals = c.list_calendars()
            for ev in c.fetch_range(date(2026,1,1), date(2026,12,31)):
                print(ev.item.title)
    """

    TIMEOUT = 30

    def __init__(self, account: SyncAccount):
        self.account = account
        self._client = None
        self._calendar = None

    # ------------------------------------------------------------------
    def connect(self) -> Any:
        """建立连接 (惰性; 失败抛 CaldavError)。"""
        if self._client is not None:
            return self._client
        try:
            import caldav
        except ImportError:
            raise CaldavError(
                "缺少 caldav 库",
                "请运行: pip install caldav icalendar",
            )
        try:
            self._client = caldav.DAVClient(
                url=self.account.base_url,
                username=self.account.username,
                password=self.account.password,
                timeout=self.TIMEOUT,
            )
        except socket.gaierror:
            raise CaldavError(
                "无法解析服务器地址",
                f"检查 base_url 是否正确: {self.account.base_url}",
            )
        except ssl.SSLError:
            raise CaldavError(
                "SSL 握手失败",
                "可能是证书问题或被中间设备拦截。试试换网络环境。",
            )
        except Exception as e:
            msg = str(e)
            if "401" in msg or "Unauthorized" in msg or "credentials" in msg.lower():
                raise CaldavError(
                    "认证失败 (401)",
                    "用户名/密码不对。注意 Google/iCloud 用的是应用专用密码, "
                    "不是主账号密码。",
                )
            if "403" in msg or "Forbidden" in msg:
                raise CaldavError(
                    "拒绝访问 (403)",
                    "该账号可能没开启对应服务 (如 iCloud 日历), "
                    "或应用专用密码被吊销。",
                )
            raise CaldavError(f"连接失败: {msg[:150]}")
        return self._client

    # ------------------------------------------------------------------
    def list_calendars(self) -> list[tuple[str, str]]:
        """返回 [(display_name, href), ...]"""
        try:
            principal = self.connect().principal()
            cals = principal.calendars()
        except CaldavError:
            raise
        except Exception as e:
            raise CaldavError(f"列举日历失败: {str(e)[:150]}")
        out = []
        for c in cals:
            name = getattr(c, "name", "") or "未命名"
            href = str(getattr(c, "url", "") or c)
            out.append((name, href))
        return out

    # ------------------------------------------------------------------
    def _get_calendar(self):
        """定位 calendar collection: 优先 account.calendar_url。"""
        if self._calendar is not None:
            return self._calendar
        c = self.connect()
        if self.account.calendar_url:
            try:
                self._calendar = c.calendar(url=self.account.calendar_url)
                return self._calendar
            except Exception:
                self._calendar = None
        # fallback: 第一个可用日历
        cals = self.list_calendars()
        if not cals:
            raise CaldavError(
                "该账号下没有可用日历",
                "请先在服务端创建一个日历, 或在账号设置里手动填 calendar_url。",
            )
        self._calendar = c.calendar(url=cals[0][1])
        return self._calendar

    def calendar_display_name(self) -> str:
        try:
            name = getattr(self._get_calendar(), "name", "")
            return str(name or "")
        except Exception:
            return ""

    # ------------------------------------------------------------------
    def fetch_range(
        self, start: date, end: date,
        expand_recurring: bool = True,
    ) -> list[RemoteEvent]:
        """拉取 [start, end] 内的事件 (含重复展开)。"""
        try:
            cal = self._get_calendar()
            # CalDAV time-range 查询 (naive datetime 视作 UTC)
            s = datetime.combine(start, datetime.min.time())
            e = datetime.combine(end, datetime.max.time())
            objs = cal.date_search(start=s, end=e, expand=expand_recurring)
        except CaldavError:
            raise
        except Exception as ex:
            raise CaldavError(f"拉取失败: {str(ex)[:150]}")

        out: list[RemoteEvent] = []
        seen: set[tuple[str, str]] = set()
        for obj in objs:
            try:
                inst = obj
                # expand=True 时是 (master, recurrence_id) 结构
                if isinstance(obj, tuple):
                    inst = obj[0]
                ev = inst.icalendar_instance if hasattr(inst, "icalendar_instance") else inst
                if not hasattr(ev, "get"):
                    # 直接是 icalendar.Event
                    ev = inst
                uid = str(ev.get("UID", "") or "")
                if not uid:
                    continue
                # 去重: 同 UID + 同日期只留一条
                dt = getattr(ev.get("DTSTART"), "dt", None)
                if isinstance(dt, datetime):
                    key_date = dt.date().isoformat()
                elif isinstance(dt, date):
                    key_date = dt.isoformat()
                else:
                    key_date = "?"
                key = (uid, key_date)
                if key in seen:
                    continue
                seen.add(key)

                href = ""
                etag = ""
                for attr in ("url", "href"):
                    v = getattr(inst, attr, None)
                    if v:
                        href = str(v)
                        break
                for attr in ("etag", "getetag"):
                    v = getattr(inst, attr, None)
                    if callable(v):
                        try:
                            etag = str(v())
                        except Exception:
                            etag = ""
                        break
                    if v:
                        etag = str(v)
                        break

                item = ical_to_item(ev, group_id="caldav")
                if item is None:
                    continue
                item.group_id = f"caldav:{self.account.account_id}"
                out.append(RemoteEvent(
                    uid=uid, href=href, etag=etag,
                    ical_event=ev, item=item,
                    repeat_rule=ical_to_repeat_rule(ev, item.item_id),
                    countdown=ical_to_countdown(ev, item.item_id),
                ))
            except Exception:
                continue  # 单条解析失败不影响整体
        return out

    # ------------------------------------------------------------------
    def push_item(
        self,
        item,
        repeat_rule=None,
        reminder=None,
        countdown=None,
        href: str = "",
        etag: str = "",
    ) -> tuple[str, str]:
        """推送一条到远端 (新建或更新)。返回 (href, etag)。"""
        try:
            cal = self._get_calendar()
            ev = item_to_ical(item, repeat_rule, reminder, countdown)
            data = ev.to_ical()
        except Exception as e:
            raise CaldavError(f"序列化失败: {str(e)[:120]}")

        try:
            import caldav
            if href and etag:
                # 更新: 带 If-Match
                ev_obj = cal.event_by_href(href)
                ev_obj.data = data
                ev_obj.save()
                new_etag = ""
                try:
                    new_etag = str(ev_obj.getetag() or "")
                except Exception:
                    pass
                return href, new_etag
            elif href:
                ev_obj = cal.event_by_href(href)
                ev_obj.data = data
                ev_obj.save()
                return href, ""
            else:
                # 新建
                new_ev = cal.save_event(data)
                new_href = str(getattr(new_ev, "url", "") or href or "")
                new_etag = ""
                try:
                    new_etag = str(new_ev.getetag() or "")
                except Exception:
                    pass
                return new_href, new_etag
        except CaldavError:
            raise
        except Exception as e:
            msg = str(e)
            if "412" in msg:
                raise CaldavError(
                    "远端已被修改 (412 Precondition Failed)",
                    "有人在你本地也改了这条。点『重新拉取』后重试。",
                )
            if "409" in msg:
                raise CaldavError("UID 冲突 (409)", "远端已存在同 UID 条目。")
            raise CaldavError(f"推送失败: {msg[:150]}")

    # ------------------------------------------------------------------
    def delete_item(self, href: str, etag: str = "") -> bool:
        """从远端删除。成功返回 True。"""
        try:
            cal = self._get_calendar()
            ev = cal.event_by_href(href)
            ev.delete()
            return True
        except Exception as e:
            if "404" in str(e):
                return True   # 已经不在了, 视为成功
            raise CaldavError(f"删除失败: {str(e)[:150]}")

    # ------------------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


__all__ = ["CaldavClient", "CaldavError", "RemoteEvent"]