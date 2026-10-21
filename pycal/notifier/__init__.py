"""系统通知 — Windows 上用 QSystemTrayIcon, 跨平台 fallback。

扫描 reminders 表, 把过期的 fire 掉, 弹 toast 或发邮件 (后续接)。
"""

from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QSystemTrayIcon
from PySide6.QtGui import QIcon

from pycal.db.connection import open_default
from pycal.db.repository import due_reminders, mark_reminder_fired, get_item


class ReminderWorker(QObject):
    """定期扫描 reminders 表, 弹出已到期的提醒。"""

    fired = Signal(str, str)  # (item_id, title)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._tray: Optional[QSystemTrayIcon] = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(30_000)   # 30 秒一次

    def attach_tray(self, tray: QSystemTrayIcon) -> None:
        self._tray = tray

    def _poll(self) -> None:
        try:
            con = open_default()
            now_iso = datetime.now().isoformat(timespec="seconds")
            for rid, item_id, remind_at in due_reminders(con, now_iso):
                item = get_item(con, item_id)
                title = item.title if item and item.title else "(无标题)"
                self._show(title, remind_at)
                mark_reminder_fired(con, rid)
                self.fired.emit(item_id, title)
        except Exception as e:
            print(f"[ReminderWorker] poll failed: {e}")

    def _show(self, title: str, when: str) -> None:
        if self._tray is None:
            print(f"[提醒] {title}  (原定 {when})")
            return
        self._tray.showMessage(
            "PyCal 提醒",
            f"{title}\n原定: {when}",
            QSystemTrayIcon.MessageIcon.Information,
            8_000,
        )


__all__ = ["ReminderWorker"]
