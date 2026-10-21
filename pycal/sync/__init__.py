"""CalDAV 双向同步 — Google / Outlook / iCloud / 通用 CalDAV。

分层:
  accounts.py     账号 CRUD + sync_log 表操作
  presets.py      各服务商默认 endpoint + 认证方式
  ical_convert.py CalendarItem <-> iCalendar (VEVENT) 双向转换
  client.py       CaldavClient — 拉取/推送/删除, 屏蔽库差异
  engine.py       SyncEngine — 三方对比 (本地 vs 远端) + 冲突解决
"""

from pycal.sync.presets import PRESETS, ProviderSpec, get_preset
from pycal.sync.client import CaldavClient, CaldavError
from pycal.sync.engine import SyncEngine, SyncResult, SyncDirection

__all__ = [
    "PRESETS", "ProviderSpec", "get_preset",
    "CaldavClient", "CaldavError",
    "SyncEngine", "SyncResult", "SyncDirection",
]