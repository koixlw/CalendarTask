"""月视图 — 6×7 格子, 点击编辑, 图标类型化, 颜色条。

Phase 1 改进:
  - 鼠标 hover 高亮整格
  - 单击: 弹出新建 (空白处) 或直接编辑 (有内容处)
  - 双击: 强制进入编辑器
  - 类型图标 (重复/提醒/倒数日)
  - 颜色条: 高亮有彩色文字的条目
  - 农历文本 (简单支持: 显示"初一"等, 不算阳历)
  - 节假日文本标记 (周末+今日, 节假日后续接外部数据源)
"""

from __future__ import annotations
import re
from datetime import date, timedelta, datetime
from typing import Optional

from PySide6.QtCore import Qt, Signal, QDate, QTimer, QPoint
from PySide6.QtGui import QColor, QFont, QPainter, QIcon, QPixmap, QAction
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QMenu, QSizePolicy,
)

from pycal.db.connection import open_default
from pycal.db.repository import list_items_in_range, upsert_item, get_item
from pycal.db.models import CalendarItem
from pycal.ui.color_picker import color_to_hex


# ============================================================================
# 类型图标 (从 CALENDAR_ICON map 缓存到 QPixmap)
# ============================================================================
_TYPE_GLYPH = {
    1: "↻",   # 重复
    2: "⏰",  # 提醒
    3: "🎯",  # 倒数日 (U+1F3AF 不通用, fallback)
}


def _glyph_for_type(t: int) -> str:
    return _TYPE_GLYPH.get(t, "")


# ============================================================================
# 行预览 (匹配 [+] 完成标记)
# ============================================================================
_LINE_RE = re.compile(r"^\s*\[\s*\+\s*\]\s*(.*)$")


def _parse_lines(content: str) -> list[tuple[bool, str]]:
    """返回 [(completed, raw_text_after_marker_or_full_line), ...]"""
    if not content:
        return []
    out: list[tuple[bool, str]] = []
    for line in content.splitlines():
        m = _LINE_RE.match(line)
        if m:
            out.append((True, m.group(1).strip()))
        elif line.strip():
            out.append((False, line.strip()))
    return out


def _rich_text_lines(content: str, color_ref: int) -> str:
    """把 <font color="#XXX"> 转为 QTextEdit 用的 <span style='color:#'>。"""
    color = color_to_hex(color_ref)
    # 简单替换: <font color='#XXXXXX'> -> <span style='color:#XXXXXX'>; </font> -> </span>
    if not content:
        return ""
    text = re.sub(
        r"<\s*font\s+color\s*=\s*[\"']?#?([0-9A-Fa-f]{6})[\"']?\s*>",
        lambda m: f"<span style='color:#{m.group(1)}'>",
        content,
    )
    text = re.sub(r"</\s*font\s*>", "</span>", text)
    return text


# ============================================================================
# DayCell — 单日格
# ============================================================================
class DayCell(QFrame):
    """单日格 widget — 显示日期号、类型标记、内容预览。"""

    sig_single_click = Signal(str)         # day_iso (左键单击 — 当前仅选中)
    sig_double_click = Signal(str)         # day_iso (左键双击 — 编辑/新建)
    sig_right_click = Signal(str)          # day_iso (右键 — 弹出菜单)
    sig_toggle_complete = Signal(str, int, int)  # (day_iso, item_index_in_cell, new_state)

    def __init__(
        self,
        day_iso: str,
        day_num: int,
        in_month: bool = True,
        is_today: bool = False,
        is_weekend: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.day_iso = day_iso
        self.in_month = in_month
        self.is_today = is_today
        self._items: list[CalendarItem] = []

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumSize(110, 80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build()
        self._refresh_bg()

    def _apply_subtitle(self, text: str, is_jieqi: bool = False) -> None:
        """显示农历/节气, 节气用红色突出。"""
        if is_jieqi:
            self._sub_lbl.setStyleSheet(
                "color: #C0392B; font-style: italic; font-weight: bold;"
            )
            self._sub_lbl.setText(f"⚡ {text}")
        elif text in ("春节", "元宵", "端午", "中秋", "重阳"):
            self._sub_lbl.setStyleSheet(
                "color: #C0392B; font-weight: bold;"
            )
            self._sub_lbl.setText(text)
        else:
            self._sub_lbl.setStyleSheet("color: #888; font-style: italic;")
            self._sub_lbl.setText(text)

    def _build(self) -> None:
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(4, 2, 4, 4)
        self._lay.setSpacing(1)

        # 顶部: 日期号 + 类型小图标
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self._num_lbl = QLabel(str(self.day_num))
        font = QFont()
        font.setBold(self.is_today)
        font.setPointSize(11 if self.is_today else 10)
        self._num_lbl.setFont(font)
        top.addWidget(self._num_lbl)

        # 节假日/lunar 副标题
        self._sub_lbl = QLabel("")
        sub_font = QFont()
        sub_font.setPointSize(7)
        sub_font.setItalic(True)
        self._sub_lbl.setFont(sub_font)
        self._sub_lbl.setStyleSheet("color: #888;")
        top.addWidget(self._sub_lbl)

        top.addStretch()
        self._type_lbl = QLabel("")
        self._type_lbl.setStyleSheet("font-size: 11pt;")
        top.addWidget(self._type_lbl)
        self._lay.addLayout(top)

        # 内容预览
        self._body = QLabel("")
        self._body.setWordWrap(True)
        self._body.setStyleSheet("font-size: 9pt;")
        self._body.setTextFormat(Qt.TextFormat.RichText)
        self._lay.addWidget(self._body, 1)

    # ------------------------------------------------------------------
    # 公共
    # ------------------------------------------------------------------
    def set_items(self, items: list[CalendarItem]) -> None:
        self._items = items
        self._refresh()

    def set_subtitle(self, text: str) -> None:
        """外部调用入口 — 同时处理 jieqi 标记与节日颜色。"""
        # text 可能已被 _subtitle_for 加上 "⚡ " 前缀
        is_jieqi = text.startswith("⚡ ")
        clean = text[2:] if is_jieqi else text
        self._apply_subtitle(clean, is_jieqi=is_jieqi)

    def _refresh_bg(self) -> None:
        if not self.in_month:
            bg = "#F5F5F5"
            fg = "#AAA"
        elif self.is_today:
            bg = "#FFF8D2"
            fg = "#222"
        else:
            bg = "white"
            fg = "#222"
        if not self.in_month:
            # 周末做小色差: 仍 in_month=True 时
            pass
        border_color = "#0066CC" if self.is_today else "#BBB"
        border_w = 2 if self.is_today else 1
        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border: {border_w}px solid {border_color}; }}"
        )
        self._num_lbl.setStyleSheet(f"color: {fg};")

    def _refresh(self) -> None:
        # 类型图标: 第一个非格子条目的类型
        types_in_cell = sorted({it.type for it in self._items if it.type != 0})
        if types_in_cell:
            self._type_lbl.setText(_glyph_for_type(types_in_cell[0]))
        else:
            self._type_lbl.setText("")

        # 渲染内容预览
        rendered: list[str] = []
        for it in self._items:
            color_tag = f"<span style='color:{color_to_hex(it.color_ref)}'>"
            if it.type != 0:
                title = it.title or (it.content.splitlines()[0] if it.content else "(无标题)")
                rendered.append(
                    f"{color_tag}{_glyph_for_type(it.type)} {title}</span>"
                )
                continue
            for completed, text in _parse_lines(it.content):
                prefix = "☑" if completed else "☐"
                line = f"{color_tag}{prefix} {text}</span>" if it.color_ref \
                    else f"{prefix} {text}"
                rendered.append(line)
        body = "<br/>".join(rendered[:5])
        if len(rendered) > 5:
            body += f"<br/><span style='color:#888'>+{len(rendered) - 5} 更多</span>"
        self._body.setText(body)

    # ------------------------------------------------------------------
    # 鼠标事件
    # ------------------------------------------------------------------
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.RightButton:
            self.sig_right_click.emit(self.day_iso)
        elif ev.button() == Qt.MouseButton.LeftButton:
            self.sig_single_click.emit(self.day_iso)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.sig_double_click.emit(self.day_iso)

    def enterEvent(self, ev):
        if not self.is_today:
            self.setStyleSheet(
                "QFrame { background: #F0F8FF; border: 1px solid #4A90E2; }"
            )
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._refresh_bg()
        super().leaveEvent(ev)

    @property
    def items(self) -> list[CalendarItem]:
        return self._items


# ============================================================================
# MonthView — 6×7 月视图
# ============================================================================
class MonthView(QWidget):
    """月视图 7×6 grid。"""

    sig_edit_item = Signal(str)            # item_id (双击 grid 内某项)
    sig_new_item = Signal(str, int)        # (day_iso, type)
    sig_edit_day = Signal(str)             # (day_iso)  — 双击空白处时
    sig_request_menu = Signal(str, object) # (day_iso, cell) — 右键

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._cur = date.today().replace(day=1)
        self._grid = QGridLayout(self)
        self._grid.setSpacing(2)
        self._cells: dict[str, DayCell] = {}

        # 双击直接进编辑 (默认双击编辑该日第一条或新建)
        self.sig_edit_day.connect(self._handle_double_click)
        self.sig_new_item.connect(self._handle_new_request)
        self.sig_edit_item.connect(self._handle_edit_item)
        self.sig_request_menu.connect(self._show_context_menu)

        self._render()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def current_month(self) -> tuple[int, int]:
        return self._cur.year, self._cur.month

    def shift_month(self, delta: int) -> None:
        y, m = self._cur.year, self._cur.month + delta
        while m < 1:
            m += 12; y -= 1
        while m > 12:
            m -= 12; y += 1
        self._cur = date(y, m, 1)
        self._render()

    def goto_today(self) -> None:
        self._cur = date.today().replace(day=1)
        self._render()

    def goto(self, y: int, m: int) -> None:
        self._cur = date(y, m, 1)
        self._render()

    def goto_date(self, day_iso: str) -> None:
        """跳转到指定日期所在月份, 并把该日 cell 高亮。"""
        try:
            y, m, d = (int(x) for x in day_iso.split("-"))
        except ValueError:
            return
        self._cur = date(y, m, 1)
        self._render()
        # 高亮目标 cell
        cell = self._cells.get(day_iso)
        if cell:
            cell.setStyleSheet(
                "QFrame { background: #FFE5B4; border: 2px solid #FF8C00; }"
            )
            cell.repaint()

    def refresh(self) -> None:
        """外部更新数据库后, 重新拉数据并刷新 UI。"""
        self._refresh_data()

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _render(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cells.clear()

        # 星期标题 (一~日)
        headers = ["一", "二", "三", "四", "五", "六", "日"]
        for col, h in enumerate(headers):
            lbl = QLabel(h)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-weight: bold; color: #555; padding: 2px;")
            self._grid.addWidget(lbl, 0, col)

        first_weekday = self._cur.weekday()  # 0=Mon
        start = self._cur - timedelta(days=first_weekday)
        today_iso = date.today().isoformat()

        for week in range(6):
            for col in range(7):
                d = start + timedelta(days=week * 7 + col)
                in_month = d.month == self._cur.month
                is_today = d.isoformat() == today_iso
                is_weekend = col >= 5
                cell = DayCell(
                    d.isoformat(), d.day,
                    in_month=in_month,
                    is_today=is_today,
                    is_weekend=is_weekend,
                )
                cell.set_subtitle(self._subtitle_for(d))
                cell.sig_double_click.connect(self.sig_edit_day)
                cell.sig_single_click.connect(self._handle_single_click)
                cell.sig_right_click.connect(self._handle_right_click)
                self._grid.addWidget(cell, week + 1, col)
                self._cells[d.isoformat()] = cell

        self._refresh_data()

    @staticmethod
    def _subtitle_for(d: date) -> str:
        """副标题: 农历月日 / 节气 / 节日。\n        返回带不带 '⚡ ' 前缀, 让 set_subtitle 区分显示。"""
        from pycal.lunar import lunar_for, is_24jieqi
        text = lunar_for(d) or ""
        if is_24jieqi(d):
            return f"⚡ {text}"
        return text

    def _refresh_data(self) -> None:
        try:
            con = open_default()
            first = self._cur - timedelta(days=self._cur.weekday())
            last = first + timedelta(days=41)
            items = list_items_in_range(
                con, first.isoformat(), last.isoformat(),
            )
            # 按日聚合
            by_day: dict[str, list[CalendarItem]] = {}
            for it in items:
                if not it.date:
                    continue
                by_day.setdefault(it.date, []).append(it)
            # 排序: type asc, item_id asc
            for iso, arr in by_day.items():
                arr.sort(key=lambda x: (x.type, x.item_id))
            for iso, cell in self._cells.items():
                cell.set_items(by_day.get(iso, []))
        except Exception as e:
            print(f"[MonthView] refresh failed: {e}")

    # ------------------------------------------------------------------
    # 双击处理
    # ------------------------------------------------------------------
    def _handle_double_click(self, day_iso: str) -> None:
        # 该日已有条目: 编辑第一条; 否则新建格子
        cell = self._cells.get(day_iso)
        if cell and cell.items:
            first_item = cell.items[0]
            self.sig_edit_item.emit(first_item.item_id)
        else:
            self.sig_new_item.emit(day_iso, 0)

    def _handle_single_click(self, day_iso: str) -> None:
        """左键单击: 仅做视觉选中 (背景已由 enterEvent 处理)。

        编辑操作留给双击 / 右键。
        """
        pass  # 选中效果由 stylesheet hover 处理

    def _handle_right_click(self, day_iso: str) -> None:
        """右键: 弹上下文菜单 (新建 / 编辑现有 / 删除全部)。"""
        cell = self._cells.get(day_iso)
        if cell is None:
            return
        # 菜单在 cell 的本地坐标上弹出
        global_pos = cell.mapToGlobal(QPoint(cell.width() // 2, cell.height() // 2))
        self.sig_request_menu.emit(day_iso, global_pos)

    def _show_context_menu(self, day_iso: str, global_pos) -> None:
        cell = self._cells.get(day_iso)
        items = cell.items if cell else []
        menu = QMenu(self)
        new_menu = menu.addMenu("新建")
        new_menu.addAction("格子 (普通日程)", lambda: self.sig_new_item.emit(day_iso, 0))
        new_menu.addAction("重复任务", lambda: self.sig_new_item.emit(day_iso, 1))
        new_menu.addAction("提醒", lambda: self.sig_new_item.emit(day_iso, 2))
        new_menu.addAction("倒数日", lambda: self.sig_new_item.emit(day_iso, 3))

        if items:
            menu.addSeparator()
            menu.addAction("编辑第一条", lambda: self.sig_edit_item.emit(items[0].item_id))
            if len(items) > 1:
                sub = menu.addMenu(f"编辑其它条目 ({len(items) - 1} 条)")
                for it in items[1:6]:
                    title = it.title or it.item_id
                    sub.addAction(
                        title,
                        lambda iid=it.item_id: self.sig_edit_item.emit(iid),
                    )
            menu.addSeparator()
            menu.addAction("删除全部此日条目", lambda: self._delete_day(day_iso))
        menu.exec(global_pos if isinstance(global_pos, QPoint) else QPoint(*global_pos))

    def _delete_day(self, day_iso: str) -> None:
        con = open_default()
        con.execute("DELETE FROM calendar_items WHERE date = ?", (day_iso,))
        self._refresh_data()

    def _handle_new_request(self, day_iso: str, type: int) -> None:
        from pycal.ui.editor import ItemEditorDialog
        from pycal.ui.main_window import _save_item
        d = date.fromisoformat(day_iso)
        dlg = ItemEditorDialog(
            initial_date=d, initial_type=type,
        )
        if dlg.exec():
            _save_item(
                dlg.result_item(),
                repeat_rule=dlg.result_repeat_rule(),
                reminder=dlg.result_reminder(),
                countdown=dlg.result_countdown(),
            )
            self._refresh_data()

    def _handle_edit_item(self, item_id: str) -> None:
        from pycal.ui.editor import ItemEditorDialog
        from pycal.ui.main_window import _save_item
        con = open_default()
        it = get_item(con, item_id)
        if not it:
            return
        dlg = ItemEditorDialog(item=it)
        if dlg.exec():
            _save_item(
                dlg.result_item(),
                repeat_rule=dlg.result_repeat_rule(),
                reminder=dlg.result_reminder(),
                countdown=dlg.result_countdown(),
            )
            self._refresh_data()
