"""iCalendar (VEVENT) <-> CalendarItem 双向转换。

映射规则 (PyCal <-> iCal):
  item_id          ↔ UID
  title            ↔ SUMMARY
  content          ↔ DESCRIPTION
  date (date-only) ↔ DTSTART (VALUE=DATE)
  is_completed     ↔ STATUS: COMPLETED / CONFIRMED
  type=1 (重复)     ↔ RRULE
  type=2 (提醒)     ↔ VALARM (TRIGGER)
  type=3 (倒数日)   ↔ TRANSP:TRANSPARENT + CATEGORIES:COUNTDOWN
  color_ref        ↔ X-PYCAL-COLOR (私有属性)

故意不映射的:
  - DesktopCal 的 [+]/[ ] 行内完成标记 — iCal 没有"格子内多行清单"概念,
    远端格子条目只同步 title, content 留在本地 (避免覆盖云端 DESCRIPTION)
  - lunar / 颜色条等纯本地展示属性

重复事件展开:
  recurring_ical_events 库负责 RRULE → 实际日期列表 (含 EXDATE/RDATE)
"""

from __future__ import annotations
import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from pycal.db.models import CalendarItem, RepeatRule, Reminder, Countdown


# PyCal <-> iCal 私有扩展 (用 X- 前缀保证其他客户端忽略)
X_COLOR = "X-PYCAL-COLOR"
X_TYPE = "X-PYCAL-TYPE"
X_COUNTDOWN = "X-PYCAL-COUNTDOWN"
X_COMPLETED_DATES = "X-PYCAL-COMPLETED-DATES"
X_PRIORITY_LOCAL = "X-PYCAL-PRIORITY"


def _hash_item(item: CalendarItem) -> str:
    """用于冲突检测的内容指纹。"""
    parts = [
        item.title or "", item.content or "", item.date or "",
        str(item.type), str(item.is_completed), str(item.color_ref),
        item.completed_dates or "",
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def content_hash(item: CalendarItem) -> str:
    return _hash_item(item)


# ============================================================================
# CalendarItem -> iCalendar
# ============================================================================
def item_to_ical(
    item: CalendarItem,
    repeat_rule: Optional[RepeatRule] = None,
    reminder: Optional[Reminder] = None,
    countdown: Optional[Countdown] = None,
    prodid: str = "-//PyCal//Desktop Calendar//ZH",
):
    """把 CalendarItem 转成 icalendar.Event。"""
    from icalendar import Event, vText

    ev = Event()
    ev.add("uid", item.item_id)
    ev.add("prodid", prodid)
    ev.add("version", "2.0")

    # DTSTART
    if item.date:
        try:
            y, m, d = (int(x) for x in item.date.split("-"))
            ev.add("dtstart", date(y, m, d))
        except (ValueError, AttributeError):
            pass

    ev.add("dtstamp", datetime.now(timezone.utc))

    # SUMMARY / DESCRIPTION
    ev.add("summary", item.title or "")
    if item.content:
        ev.add("description", item.content)

    # STATUS
    if item.is_completed:
        ev.add("status", "COMPLETED")
    else:
        ev.add("status", "CONFIRMED")

    # RRULE
    if item.type == 1 and repeat_rule is not None:
        freq_map = {
            "daily": "DAILY", "weekly": "WEEKLY",
            "monthly": "MONTHLY", "yearly": "YEARLY",
        }
        rule: dict = {"FREQ": freq_map.get(repeat_rule.freq or "daily", "DAILY")}
        interval = repeat_rule.interval_n or 1
        if interval > 1:
            rule["INTERVAL"] = interval
        if repeat_rule.byweekday:
            rule["BYDAY"] = [
                d.strip() for d in str(repeat_rule.byweekday).split(",") if d.strip()
            ]
        if repeat_rule.until_date:
            try:
                y, m, d = (int(x) for x in repeat_rule.until_date.split("-"))
                # UNTIL 必须是 date/datetime 对象, 且 RFC5545 要求 UTC (带 Z)
                rule["UNTIL"] = datetime(y, m, d, 23, 59, 59, tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                pass
        ev.add("rrule", rule)

    # VALARM (提醒)
    if item.type == 2 and reminder is not None:
        from icalendar import Alarm
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", item.title or "Reminder")
        # 计算 trigger 偏移: 现在 → 提醒时刻
        offset = timedelta(0)
        try:
            rdt = reminder.remind_at
            if rdt.tzinfo is None:
                rdt = rdt.replace(tzinfo=timezone.utc)
            offset = rdt - datetime.now(timezone.utc)
            if offset.total_seconds() <= 0:
                offset = timedelta(0)     # 已过期 → 立即触发
        except Exception:
            offset = timedelta(0)
        alarm.add("trigger", offset)
        ev.add_component(alarm)

    # COUNTDOWN (倒数日) — 用 TRANSP:TRANSPARENT 标记为"不占用时间"
    if item.type == 3 and countdown is not None:
        ev.add("transp", "TRANSPARENT")
        ev.add("categories", ["COUNTDOWN"])
        ev.add(X_COUNTDOWN, countdown.target_date)
        if countdown.repeat_yearly:
            ev.add(X_COUNTDOWN + "-YEARLY", "1")

    # 私有扩展
    if item.color_ref:
        ev.add(X_COLOR, str(item.color_ref))
    ev.add(X_TYPE, str(item.type))
    if item.priority:
        ev.add(X_PRIORITY_LOCAL, str(item.priority))
    if item.completed_dates:
        ev.add(X_COMPLETED_DATES, item.completed_dates)

    return ev


# ============================================================================
# iCalendar -> CalendarItem
# ============================================================================
def ical_to_item(ev, group_id: str = "caldav", subaccount_id: int = 0) -> Optional[CalendarItem]:
    """把 icalendar.Event 转成 CalendarItem。失败返回 None。"""
    uid = str(ev.get("UID", "") or "").strip()
    if not uid:
        uid = f"caldav-{hashlib.sha1(str(ev.to_ical()).encode()).hexdigest()[:12]}"
    item_id = f"cdav_{uid}"

    # SUMMARY
    title = str(ev.get("SUMMARY", "") or "")

    # DESCRIPTION
    content = str(ev.get("DESCRIPTION", "") or "")

    # DTSTART -> date
    date_iso: Optional[str] = None
    dtstart = ev.get("DTSTART")
    dt_obj = getattr(dtstart, "dt", None)
    if isinstance(dt_obj, datetime):
        date_iso = dt_obj.date().isoformat()
    elif isinstance(dt_obj, date):
        date_iso = dt_obj.isoformat()
    elif dt_obj is None:
        # 有些事件 DTSTART 是字符串
        raw = str(dtstart.dt) if hasattr(dtstart, "dt") else str(dtstart or "")
        if len(raw) >= 8 and raw[:8].isdigit():
            date_iso = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
        elif len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            date_iso = raw[:10]

    # STATUS -> is_completed
    status = str(ev.get("STATUS", "") or "").upper()
    is_completed = 1 if status in ("COMPLETED", "DONE") else 0

    # 私有扩展
    color_ref = 0
    raw_color = ev.get(X_COLOR)
    if raw_color:
        try:
            color_ref = int(str(raw_color))
        except ValueError:
            color_ref = 0
    priority = 0
    raw_prio = ev.get(X_PRIORITY_LOCAL)
    if raw_prio:
        try:
            priority = int(str(raw_prio))
        except ValueError:
            priority = 0
    completed_dates = str(ev.get(X_COMPLETED_DATES, "") or "") or None

    # type 判定: 优先用私有扩展, 其次推断
    raw_type = ev.get(X_TYPE)
    type_code = 0
    if raw_type:
        try:
            type_code = int(str(raw_type))
        except ValueError:
            type_code = 0
    else:
        if ev.get("RRULE"):
            type_code = 1
        elif str(ev.get("CATEGORIES", "")) == "COUNTDOWN" or ev.get(X_COUNTDOWN):
            type_code = 3
        elif any(getattr(c, "name", "") == "VALARM" for c in ev.subcomponents):
            type_code = 2

    return CalendarItem(
        item_id=item_id,
        date=date_iso,
        type=type_code,
        title=title,
        content=content,
        color_ref=color_ref,
        is_completed=is_completed,
        completed_dates=completed_dates,
        priority=priority,
        group_id=group_id,
        subaccount_id=subaccount_id,
    )


def _vprop(obj, *names):
    """取 iCalendar 属性值 — 兼容 dict 型 (icalendar 7.x vRecur) 和属性型 (vDDDTypes)。"""
    for name in names:
        if obj is None:
            continue
        # dict 优先 (vRecur / vDDDLists 都是 dict 子类)
        if isinstance(obj, dict):
            for k in obj.keys():
                if str(k).upper() == name.upper():
                    return obj[k]
            continue
        for k in names:
            if hasattr(obj, k):
                v = getattr(obj, k)
                if v is not None:
                    return v
    return None


def ical_to_repeat_rule(ev, item_id: str) -> Optional[RepeatRule]:
    rrule = ev.get("RRULE")
    if rrule is None:
        return None

    freq_rev = {
        "DAILY": "daily", "WEEKLY": "weekly",
        "MONTHLY": "monthly", "YEARLY": "yearly",
    }
    freq = str(_vprop(rrule, "FREQ") or "").upper()
    if not freq:
        return None

    interval_raw = _vprop(rrule, "INTERVAL")
    try:
        interval = int(interval_raw) if interval_raw is not None else 1
    except (TypeError, ValueError):
        interval = 1

    byday_raw = _vprop(rrule, "BYDAY")
    byweekday = None
    if byday_raw:
        if isinstance(byday_raw, (list, tuple, set)):
            byweekday = ",".join(str(x) for x in byday_raw if str(x))
        else:
            byweekday = str(byday_raw)

    until = _vprop(rrule, "UNTIL")
    until_date = None
    if until is not None:
        u = getattr(until, "dt", until)   # vDDDTypes -> 内部值
        if isinstance(u, datetime):
            until_date = u.date().isoformat()
        elif isinstance(u, date):
            until_date = u.isoformat()
        else:
            digits = "".join(c for c in str(u) if c.isdigit())
            if len(digits) >= 8:
                until_date = f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"

    return RepeatRule(
        item_id=item_id,
        freq=freq_rev.get(freq, "daily"),
        interval_n=interval,
        byweekday=byweekday,
        until_date=until_date,
    )


def ical_to_countdown(ev, item_id: str) -> Optional[Countdown]:
    target = ev.get(X_COUNTDOWN)
    if not target:
        return None
    target_str = str(target)
    yearly = bool(ev.get(X_COUNTDOWN + "-YEARLY"))
    return Countdown(item_id=item_id, target_date=target_str, repeat_yearly=1 if yearly else 0)


def expand_recurring(
    ev,
    start: date,
    end: date,
) -> list[date]:
    """展开 RRULE 在 [start, end] 内的所有日期。"""
    from recurring_ical_events import of
    try:
        events = of(ev).between(start, end)
        out = []
        for e in events:
            dt = getattr(e.get("DTSTART"), "dt", None)
            if isinstance(dt, datetime):
                out.append(dt.date())
            elif isinstance(dt, date):
                out.append(dt)
        return sorted(set(out))
    except Exception:
        # 展开失败就退回单点
        return [start] if start <= end else []


__all__ = [
    "item_to_ical", "ical_to_item", "ical_to_repeat_rule",
    "ical_to_countdown", "content_hash", "expand_recurring",
    "X_COLOR", "X_TYPE", "X_COUNTDOWN", "X_COMPLETED_DATES",
]