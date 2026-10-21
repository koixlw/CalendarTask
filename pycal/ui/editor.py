"""条目编辑器 — 4 类型共用 Dialog。

type=0 (格子):     普通日期条目, 多行带 [+] 完成标记
type=1 (重复):     + 频率 / 周期 / 截止
type=2 (提醒):     + remind_at / channel
type=3 (倒数日):   + target_date / repeat_yearly

返回: CalendarItem (保存时) or None (取消)
"""

from __future__ import annotations
import uuid
from datetime import datetime, date
from typing import Optional

from PySide6.QtCore import Qt, Signal, QDate, QDateTime, QTime
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QTextEdit,
    QComboBox, QPushButton, QDateEdit, QDateTimeEdit, QSpinBox, QCheckBox,
    QLabel, QFrame, QStackedWidget, QWidget, QButtonGroup, QDialogButtonBox,
    QGroupBox, QToolButton,
)

from pycal.db.models import CalendarItem, RepeatRule, Reminder, Countdown
from pycal.ui.color_picker import ColorPickerButton, color_to_hex


TYPE_LABELS = {
    0: "格子 (普通日程)",
    1: "重复任务",
    2: "提醒",
    3: "倒数日",
}


class ItemEditorDialog(QDialog):
    """编辑对话框 — 通过 ItemEditorDialog.exec() 打开。

    用法:
        dlg = ItemEditorDialog(item=existing)  # 编辑现有
        dlg = ItemEditorDialog(initial_date=date(2026,10,21), type=0)  # 新建格子
        if dlg.exec():
            saved = dlg.result_item()
    """

    def __init__(
        self,
        item: Optional[CalendarItem] = None,
        initial_date: Optional[date] = None,
        initial_type: int = 0,
        repeat_rule: Optional[RepeatRule] = None,
        reminder: Optional[Reminder] = None,
        countdown: Optional[Countdown] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("PyCal — 编辑条目")
        self.setMinimumWidth(540)

        self._item = item or CalendarItem(item_id=self._make_id())
        self._repeat_rule = repeat_rule
        self._reminder = reminder
        self._countdown = countdown

        if initial_date:
            self._item.date = initial_date.isoformat()
        elif not self._item.date:
            self._item.date = date.today().isoformat()

        if self._item.type == 0 and self._item.type != initial_type:
            self._item.type = initial_type

        self._build()
        self._load_to_widgets()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def result_item(self) -> CalendarItem:
        return self._item

    def result_repeat_rule(self) -> Optional[RepeatRule]:
        return self._repeat_rule if self._item.type == 1 else None

    def result_reminder(self) -> Optional[Reminder]:
        return self._reminder if self._item.type == 2 else None

    def result_countdown(self) -> Optional[Countdown]:
        return self._countdown if self._item.type == 3 else None

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)

        # 顶部: 标题 + 类型
        head = QHBoxLayout()
        head.addWidget(QLabel("标题:"))
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("输入事项标题…")
        head.addWidget(self._title_edit, 1)
        head.addWidget(QLabel("类型:"))
        self._type_combo = QComboBox()
        for t, label in TYPE_LABELS.items():
            self._type_combo.addItem(label, t)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        head.addWidget(self._type_combo)
        root.addLayout(head)

        # 第二行: 日期 + 颜色
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("日期:"))
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDisplayFormat("yyyy-MM-dd")
        row2.addWidget(self._date_edit)
        row2.addSpacing(20)
        row2.addWidget(QLabel("颜色:"))
        self._color_btn = ColorPickerButton(0)
        self._color_btn.color_changed.connect(self._on_color_changed)
        row2.addWidget(self._color_btn)
        row2.addStretch()
        root.addLayout(row2)

        # 类型专属面板
        self._type_stack = QStackedWidget()
        self._type_stack.addWidget(self._build_grid_panel())        # 0
        self._type_stack.addWidget(self._build_repeat_panel())      # 1
        self._type_stack.addWidget(self._build_reminder_panel())    # 2
        self._type_stack.addWidget(self._build_countdown_panel())   # 3
        self._type_stack.setCurrentIndex(0)
        root.addWidget(self._type_stack)

        # 内容 (多行)
        root.addWidget(QLabel("内容 (每行一条; [+] 开头表示已完成):"))
        self._content_edit = QTextEdit()
        self._content_edit.setMinimumHeight(140)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._content_edit.setFont(mono)
        root.addWidget(self._content_edit, 1)

        # 状态行
        status = QHBoxLayout()
        self._completed_chk = QCheckBox("标记为已完成")
        status.addWidget(self._completed_chk)
        status.addStretch()
        self._priority_spin = QSpinBox()
        self._priority_spin.setRange(-3, 3)
        self._priority_spin.setToolTip("优先级: -3 (低) ~ +3 (高)")
        status.addWidget(QLabel("优先级:"))
        status.addWidget(self._priority_spin)
        root.addLayout(status)

        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    # 类型面板构造
    # ------------------------------------------------------------------
    def _build_grid_panel(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        note = QLabel("格子模式: 多行内容, [+] 开头代表已完成。\n"
                      "可在月视图里逐行勾选完成。")
        note.setStyleSheet("color: #666; font-size: 9pt;")
        form.addRow(note)
        return w

    def _build_repeat_panel(self) -> QWidget:
        w = QGroupBox("重复规则")
        form = QFormLayout(w)
        self._freq_combo = QComboBox()
        for f in ("daily", "weekly", "monthly", "yearly"):
            self._freq_combo.addItem(
                {"daily": "每天", "weekly": "每周", "monthly": "每月", "yearly": "每年"}[f], f
            )
        form.addRow("频率:", self._freq_combo)

        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(1, 99)
        self._interval_spin.setValue(1)
        form.addRow("间隔:", self._interval_spin)

        self._until_edit = QDateEdit()
        self._until_edit.setCalendarPopup(True)
        self._until_edit.setDisplayFormat("yyyy-MM-dd")
        self._until_edit.setSpecialValueText("不限")
        self._until_edit.setDate(self._until_edit.minimumDate())
        form.addRow("截止日期:", self._until_edit)

        return w

    def _build_reminder_panel(self) -> QWidget:
        w = QGroupBox("提醒设置")
        form = QFormLayout(w)
        self._remind_dt = QDateTimeEdit()
        self._remind_dt.setCalendarPopup(True)
        self._remind_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._remind_dt.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        form.addRow("提醒时间:", self._remind_dt)

        self._channel_combo = QComboBox()
        self._channel_combo.addItems(["系统通知", "邮件", "声音"])
        form.addRow("通道:", self._channel_combo)

        return w

    def _build_countdown_panel(self) -> QWidget:
        w = QGroupBox("倒数日设置")
        form = QFormLayout(w)
        self._target_edit = QDateEdit()
        self._target_edit.setCalendarPopup(True)
        self._target_edit.setDisplayFormat("yyyy-MM-dd")
        form.addRow("目标日期:", self._target_edit)

        self._repeat_yearly_chk = QCheckBox("每年重复")
        form.addRow(self._repeat_yearly_chk)

        note = QLabel("倒数日会在月视图和今天的面板里显示「还有 N 天」。")
        note.setStyleSheet("color: #666; font-size: 9pt;")
        form.addRow(note)
        return w

    # ------------------------------------------------------------------
    # 状态同步
    # ------------------------------------------------------------------
    def _on_type_changed(self, idx: int) -> None:
        self._type_stack.setCurrentIndex(idx)

    def _on_color_changed(self, color: int) -> None:
        self._item.color_ref = color

    def _load_to_widgets(self) -> None:
        self._title_edit.setText(self._item.title)
        if self._item.date:
            try:
                y, m, d = (int(x) for x in self._item.date.split("-"))
                self._date_edit.setDate(QDate(y, m, d))
            except (ValueError, AttributeError):
                pass
        self._color_btn.set_color(self._item.color_ref or 0)
        self._content_edit.setPlainText(self._item.content)
        self._completed_chk.setChecked(bool(self._item.is_completed))
        self._priority_spin.setValue(self._item.priority)
        # Type combo
        idx = self._type_combo.findData(self._item.type)
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)

        # 子实体
        if self._item.type == 1 and self._repeat_rule:
            r = self._repeat_rule
            i = self._freq_combo.findData(r.freq)
            if i >= 0:
                self._freq_combo.setCurrentIndex(i)
            self._interval_spin.setValue(r.interval_n or 1)
            if r.until_date:
                try:
                    y, m, d = (int(x) for x in r.until_date.split("-"))
                    self._until_edit.setDate(QDate(y, m, d))
                except (ValueError, AttributeError):
                    pass
        elif self._item.type == 2 and self._reminder:
            try:
                dt = datetime.fromisoformat(self._reminder.remind_at.isoformat()
                                            if isinstance(self._reminder.remind_at, datetime)
                                            else str(self._reminder.remind_at))
                self._remind_dt.setDateTime(QDateTime(dt.year, dt.month, dt.day,
                                                     dt.hour, dt.minute))
            except Exception:
                pass
        elif self._item.type == 3 and self._countdown:
            try:
                y, m, d = (int(x) for x in self._countdown.target_date.split("-"))
                self._target_edit.setDate(QDate(y, m, d))
            except (ValueError, AttributeError):
                pass
            self._repeat_yearly_chk.setChecked(bool(self._countdown.repeat_yearly))

    def _on_save(self) -> None:
        self._item.title = self._title_edit.text().strip()
        d = self._date_edit.date()
        self._item.date = f"{d.year():04d}-{d.month():02d}-{d.day():02d}"
        self._item.content = self._content_edit.toPlainText()
        self._item.is_completed = 1 if self._completed_chk.isChecked() else 0
        self._item.priority = self._priority_spin.value()
        self._item.type = self._type_combo.currentData()

        # 派生字段
        if self._item.type == 1:
            if self._repeat_rule is None:
                self._repeat_rule = RepeatRule(item_id=self._item.item_id)
            self._repeat_rule.freq = self._freq_combo.currentData()
            self._repeat_rule.interval_n = self._interval_spin.value()
            ud = self._until_edit.date()
            if ud == self._until_edit.minimumDate():
                self._repeat_rule.until_date = None
            else:
                self._repeat_rule.until_date = f"{ud.year():04d}-{ud.month():02d}-{ud.day():02d}"
        elif self._item.type == 2:
            if self._reminder is None:
                self._reminder = Reminder(item_id=self._item.item_id)
            dt = self._remind_dt.dateTime()
            self._reminder.remind_at = datetime(dt.year(), dt.month(), dt.day(),
                                                 dt.hour(), dt.minute())
            self._reminder.channel = {"系统通知": "system", "邮件": "email",
                                       "声音": "sound"}[self._channel_combo.currentText()]
        elif self._item.type == 3:
            if self._countdown is None:
                self._countdown = Countdown(item_id=self._item.item_id)
            td = self._target_edit.date()
            self._countdown.target_date = f"{td.year():04d}-{td.month():02d}-{td.day():02d}"
            self._countdown.repeat_yearly = 1 if self._repeat_yearly_chk.isChecked() else 0

        self.accept()

    @staticmethod
    def _make_id() -> str:
        return f"pycal-{uuid.uuid4().hex[:12]}"
