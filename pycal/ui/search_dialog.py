"""完整搜索面板 — 复刻 DesktopCal history_search.lua 的 5 个下拉 + 22 色调色板。

复刻对照 (PyCal ↔ Lua):
  search_keyword          ↔ document.getElementById("search_keyword")
  search_completion       ↔ "search_completion"  (0=全部/1=已完/2=未完)
  search_type             ↔ "search_type"        (0/1=格子/2=重复/3=提醒/4=倒数日)
  search_text_color       ↔ "search_text_color"  (22 色 + 自定义 + null)
  search_time_range       ↔ "search_time_range"  (0=全部/1=本月/3/12/-1=自定义)
  search_time_start/end   ↔ "search_time_start"/"search_time_end"

行为:
  任一筛选条件变化即 try_auto_search() -> 即时重搜
  双击结果跳转到日历中对应日期 + 打开编辑器
"""

from __future__ import annotations
from datetime import date, datetime, timedelta
from typing import Optional

from PySide6.QtCore import Qt, Signal, QDate, QPoint
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLineEdit, QComboBox, QPushButton, QDateEdit, QToolButton,
    QLabel, QFrame, QWidget, QDialogButtonBox, QScrollArea, QMessageBox,
    QListWidget, QListWidgetItem,
)

from pycal.db.connection import open_default
from pycal.db.repository import get_item
from pycal.search.engine import SearchEngine, SearchOptions, SearchHit
from pycal.ui.color_picker import ColorPickerButton, PALETTE


# ── 文案 (与 DesktopCal IDS_* 一致) ───────────────────────────────
_LABELS = {
    "completion": ["全部", "已完成", "未完成"],
    "type": ["全部", "普通任务 (格子)", "重复任务", "提醒", "倒数日"],
    "time_range": ["全部", "本月内", "近 3 个月", "近 12 个月", "自定义起止"],
    "results_count": "共 %d 项搜索记录",
    "no_results": "无搜索结果",
}


class SearchDialog(QDialog):
    """桌面级搜索面板 — 5 个下拉 + 关键字 + 颜色 + 自定义区间。"""

    sig_jump_to_item = Signal(str, str)  # (item_id, day_iso) — 跳转日历

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("PyCal — 搜索")
        self.resize(720, 580)
        self._engine = SearchEngine(open_default())
        self._build()
        self._wire()

    # ------------------------------------------------------------------
    # 布局构造
    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)

        # ── 关键字 ──
        kw_row = QHBoxLayout()
        kw_row.addWidget(QLabel("关键字:"))
        self._kw_edit = QLineEdit()
        self._kw_edit.setPlaceholderText("输入关键字 (大小写不敏感, [+] / <font color=> 等富文本也能匹配)…")
        kw_row.addWidget(self._kw_edit, 1)
        root.addLayout(kw_row)

        # ── 5 个筛选下拉 ──
        filt = QGridLayout()
        filt.setColumnStretch(1, 1)
        filt.setColumnStretch(3, 1)
        filt.setColumnStretch(5, 1)

        # 完成度
        self._completion_combo = QComboBox()
        for i, label in enumerate(_LABELS["completion"]):
            self._completion_combo.addItem(label, i)
        filt.addWidget(QLabel("完成度:"), 0, 0)
        filt.addWidget(self._completion_combo, 0, 1)

        # 类型
        self._type_combo = QComboBox()
        for i, label in enumerate(_LABELS["type"]):
            self._type_combo.addItem(label, i)
        filt.addWidget(QLabel("类型:"), 0, 2)
        filt.addWidget(self._type_combo, 0, 3)

        # 颜色
        self._color_btn = ColorPickerButton(0)
        self._color_btn.set_color(0)
        filt.addWidget(QLabel("文字颜色:"), 0, 4)
        filt.addWidget(self._color_btn, 0, 5)

        # 时间范围 (跨一行)
        self._range_combo = QComboBox()
        for i, label in enumerate(_LABELS["time_range"]):
            self._range_combo.addItem(label, i)
        # 用索引 0..4 对应 range_type 0/1/3/12/-1
        self._RANGE_TYPE = [0, 1, 3, 12, -1]
        filt.addWidget(QLabel("时间范围:"), 1, 0)
        filt.addWidget(self._range_combo, 1, 1)

        # 自定义日期 (默认隐藏)
        self._custom_box = QWidget()
        cb_lay = QHBoxLayout(self._custom_box)
        cb_lay.setContentsMargins(0, 0, 0, 0)
        cb_lay.addWidget(QLabel("起:"))
        self._start_edit = QDateEdit()
        self._start_edit.setCalendarPopup(True)
        self._start_edit.setDisplayFormat("yyyy-MM-dd")
        self._start_edit.setDate(QDate.currentDate().addYears(-1))
        cb_lay.addWidget(self._start_edit)
        cb_lay.addSpacing(12)
        cb_lay.addWidget(QLabel("止:"))
        self._end_edit = QDateEdit()
        self._end_edit.setCalendarPopup(True)
        self._end_edit.setDisplayFormat("yyyy-MM-dd")
        self._end_edit.setDate(QDate.currentDate())
        cb_lay.addWidget(self._end_edit)
        cb_lay.addStretch()
        self._custom_box.setVisible(False)
        filt.addWidget(self._custom_box, 1, 2, 1, 4)

        root.addLayout(filt)

        # ── 结果区 ──
        result_row = QHBoxLayout()
        self._count_lbl = QLabel(_LABELS["no_results"])
        self._count_lbl.setStyleSheet("font-weight: bold; color: #555;")
        result_row.addWidget(self._count_lbl)
        result_row.addStretch()
        self._hint_lbl = QLabel("双击结果跳转")
        self._hint_lbl.setStyleSheet("color: #888; font-size: 9pt;")
        result_row.addWidget(self._hint_lbl)
        root.addLayout(result_row)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { background: white; border: 1px solid #CCC; }"
            "QListWidget::item { padding: 6px; border-bottom: 1px solid #EEE; }"
            "QListWidget::item:hover { background: #F0F8FF; }"
            "QListWidget::item:selected { background: #CCE5FF; }"
        )
        root.addWidget(self._list, 1)

        # ── 底部按钮 ──
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        root.addWidget(btns)

    def _wire(self) -> None:
        # 任何一个变化都触发即时重搜
        self._kw_edit.textChanged.connect(self._auto_search)
        self._completion_combo.currentIndexChanged.connect(self._auto_search)
        self._type_combo.currentIndexChanged.connect(self._auto_search)
        self._color_btn.color_changed.connect(
            lambda _c: self._auto_search())
        self._range_combo.currentIndexChanged.connect(self._on_range_changed)
        self._start_edit.dateChanged.connect(self._auto_search)
        self._end_edit.dateChanged.connect(self._auto_search)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        # 启动时自动 focus 关键字栏
        self._kw_edit.setFocus()

    # ------------------------------------------------------------------
    # 即时重搜
    # ------------------------------------------------------------------
    def _on_range_changed(self, idx: int) -> None:
        is_custom = (idx == 4)        # "自定义起止"
        self._custom_box.setVisible(is_custom)
        self._auto_search()

    def _auto_search(self) -> None:
        # 关键字空且无任何筛选时, 不搜
        if not self._is_condition_ready():
            self._list.clear()
            self._count_lbl.setText(_LABELS["no_results"])
            return

        kw = self._kw_edit.text().strip()
        completion = self._completion_combo.currentData()
        type_filter = self._type_combo.currentData()
        text_color = self._color_btn.color() or 0

        range_idx = self._range_combo.currentIndex()
        range_months = self._RANGE_TYPE[range_idx]
        range_start = self._start_edit.date().toString("yyyy-MM-dd") \
            if range_months == -1 else None
        range_end = self._end_edit.date().toString("yyyy-MM-dd") \
            if range_months == -1 else None

        opts = SearchOptions(
            keyword=kw,
            completion=completion,
            type_filter=type_filter,
            text_color=text_color,
            range_months=range_months if range_months > 0 or range_months == -1 else 0,
            range_start=range_start,
            range_end=range_end,
        )

        # 0=全部: 走 None 分支 (不过滤)
        if range_months == 0:
            opts.range_months = 0

        try:
            hits = self._engine.search(opts)
        except Exception as e:
            self._list.clear()
            self._count_lbl.setText(f"搜索出错: {e}")
            return

        self._render(hits)

    def _is_condition_ready(self) -> bool:
        if self._kw_edit.text().strip():
            return True
        if self._completion_combo.currentData():
            return True
        if self._type_combo.currentData():
            return True
        if self._color_btn.color():
            return True
        if self._range_combo.currentIndex() > 0:
            return True
        return False

    # ------------------------------------------------------------------
    # 渲染结果
    # ------------------------------------------------------------------
    def _render(self, hits: list[SearchHit]) -> None:
        self._list.clear()
        n = len(hits)
        if n == 0:
            self._count_lbl.setText(_LABELS["no_results"])
            return
        self._count_lbl.setText(_LABELS["results_count"] % n)
        for h in hits[:200]:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, (h.item_id, h.date or ""))
            item.setText(self._format_hit(h))
            item.setToolTip(self._format_hit_tooltip(h))
            self._list.addItem(item)

    @staticmethod
    def _format_hit(h: SearchHit) -> str:
        """格式: [日期] 标题 → 命中行 / 类型。"""
        title = (h.title or "").strip()
        if not title and h.content:
            first_line = h.content.splitlines()[0]
            title = first_line[:40]
        type_name = {
            0: "格子", 1: "重复", 2: "提醒", 3: "倒数日", 4: "课表",
        }.get(h.type, "?")
        date_part = h.date or "(无日期)"
        head = f"[{date_part}] {title or '(无标题)'}  <{type_name}>"
        if h.matched_line:
            head += f"\n   ↪ {h.matched_line[:80]}"
        return head

    @staticmethod
    def _format_hit_tooltip(h: SearchHit) -> str:
        if h.matched_line:
            return f"命中行: {h.matched_line}"
        return h.content[:300] if h.content else ""

    # ------------------------------------------------------------------
    # 双击: 跳转 + 打开编辑器
    # ------------------------------------------------------------------
    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        item_id, day_iso = item.data(Qt.ItemDataRole.UserRole)
        self.sig_jump_to_item.emit(item_id, day_iso)
        # 关闭对话框让 MainWindow 承接跳转
        self.accept()

    # ------------------------------------------------------------------
    # Public helper — 让 main_window 知道是否需要重开 / 关闭
    # ------------------------------------------------------------------
    def exec_with_jump(self) -> Optional[tuple[str, str]]:
        """阻塞执行对话框, 返回 (item_id, day_iso) 或 None。"""
        if self.exec() == QDialog.DialogCode.Accepted:
            cur = self._list.currentItem()
            if cur:
                return cur.data(Qt.ItemDataRole.UserRole)
        return None


__all__ = ["SearchDialog"]
