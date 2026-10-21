"""数据模型 (dataclass)。"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class CalendarItem:
    """日历条目 — 对应 calendar_items 表一条记录。

    type 含义:
      0 = 格子 (日期+列表)
      1 = 重复任务
      2 = 提醒
      3 = 倒数日
      4 = 课程表 (本期不实现, 留空)
    """
    item_id: str
    date: Optional[str] = None                # 'YYYY-MM-DD'
    type: int = 0
    title: str = ""
    content: str = ""
    color_ref: int = 0
    is_completed: int = 0
    completed_dates: Optional[str] = None
    priority: int = 0
    group_id: Optional[str] = None
    subaccount_id: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_grid(self) -> bool:
        return self.type == 0

    @property
    def is_repeat(self) -> bool:
        return self.type == 1

    @property
    def is_reminder(self) -> bool:
        return self.type == 2

    @property
    def is_countdown(self) -> bool:
        return self.type == 3


@dataclass(slots=True)
class Note:
    note_id: Optional[int] = None
    title: str = ""
    content: str = ""
    color_ref: int = 0
    pinned: int = 0
    pos_x: int = 100
    pos_y: int = 100
    width: int = 320
    height: int = 240
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(slots=True)
class RepeatRule:
    item_id: str
    freq: str = "daily"           # daily / weekly / monthly / yearly
    interval_n: int = 1
    byweekday: Optional[str] = None
    until_date: Optional[str] = None


@dataclass(slots=True)
class Reminder:
    item_id: str
    remind_at: datetime = field(default_factory=datetime.now)
    fired: int = 0
    channel: str = "system"


@dataclass(slots=True)
class Countdown:
    item_id: str
    target_date: str = ""         # 'YYYY-MM-DD'
    repeat_yearly: int = 0
