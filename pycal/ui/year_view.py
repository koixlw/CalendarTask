"""年视图 — 一屏看全年 12 个月, 下面是选中月的详情。

排法跟月视图一致: 以月为单位, 星期从星期一开始 (一 二 三 四 五 六 日)。
小月只画日期号 —— 有条目的日子加粗变蓝, 今天是黄底圆圈, 非本月的淡灰。
点月份标题选中那个月, 点某一天直接跳到那天。

下面那块详情直接复用 MonthView —— 勾选、双击编辑、右键菜单全都一样,
不用再写一遍。

小月用 QPainter 画, 不堆小控件: 12 个月 × 50 个 QLabel 太重, 一个 widget
画完整个月省事得多。
"""

from __future__ import annotations
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta
from typing import Optional

from PySide6.QtCore import Qt, Signal, QEvent, QRect, QRectF, QPoint
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QToolButton, QToolTip, QSizePolicy,
)

from pycal.db.connection import open_default
from pycal.db.repository import list_items_in_range
from pycal.ui.fonts import Fonts, DEFAULT_SCALE, fonts_for
from pycal.ui.month_view import MonthView, CellColors, colors_for_skin


# 星期表头 — 从星期一开始 (和月视图一致)
_MINI_HEADERS = ("一", "二", "三", "四", "五", "六", "日")

_WEEKEND_FG = "#CC6666"      # 周末那两列
_ITEM_FG = "#2E6FD9"         # 有条目的日子


class MiniMonth(QWidget):
    """年视图里的一小块月 — QPainter 画的一个月。

    画出: 月份标题 + 星期表头 (一~日) + 6 行 × 7 列的日期号。
    """

    sig_day_clicked = Signal(str)          # day_iso
    sig_month_clicked = Signal(int, int)   # (year, month)

    def __init__(
        self,
        year: int,
        month: int,
        colors: Optional[CellColors] = None,
        fonts: Optional[Fonts] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("MiniMonth")
        self.year = year
        self.month = month
        self._colors = colors or CellColors()
        self._fonts = fonts or fonts_for()
        self._counts: dict[str, int] = {}      # day_iso → 条目数
        self._selected = False
        self._today = date.today().isoformat()
        self._days = self._build_days()
        # QFont 缓存 — 画一个月要 setFont 40 多次, 每次新建一个 QFont
        # 挺浪费的 (QFont 构造要查字体数据库)。按 (字号, 加粗) 缓存。
        self._font_cache: dict[tuple[int, bool], QFont] = {}

        self.setMinimumHeight(self._fonts.mini_h)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ------------------------------------------------------------------
    def _build_days(self) -> list[date]:
        """这个月占到的那些天 — 从当月 1 号所在那周的星期一开始。

        只留本月真正占到的周 (5 或 6 周): 整行都属于下个月的那行不画,
        跟月视图一个规矩。
        """
        from calendar import monthrange
        first = date(self.year, self.month, 1)
        start = first - timedelta(days=first.weekday())   # weekday(): Mon=0
        days_in_month = monthrange(self.year, self.month)[1]
        self._rows = (first.weekday() + days_in_month + 6) // 7
        return [start + timedelta(days=i) for i in range(self._rows * 7)]

    def _geometry(self) -> tuple[QRect, int, int, int, int]:
        """(整块矩形, 标题行高, 表头行高, 格宽, 格高) — 画和点击共用一套。"""
        r = self.rect().adjusted(3, 2, -3, -2)
        title_h = self._fonts.mini_title_h
        head_h = self._fonts.mini_head_h
        cell_w = max(1, r.width() // 7)
        cell_h = max(1, (r.height() - title_h - head_h) // max(1, self._rows))
        return r, title_h, head_h, cell_w, cell_h

    def _font(self, pt: int, bold: bool = False) -> QFont:
        """按 (字号, 加粗) 取字体 — 复用同一个 QFont, 别每次都新建。"""
        key = (pt, bold)
        f = self._font_cache.get(key)
        if f is None:
            f = QFont()
            f.setPointSize(pt)
            f.setBold(bold)
            self._font_cache[key] = f
        return f

    # ------------------------------------------------------------------
    # 画
    # ------------------------------------------------------------------
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        c, f = self._colors, self._fonts
        r, title_h, head_h, cell_w, cell_h = self._geometry()
        days_top = r.top() + title_h + head_h

        # 选中的那块垫一层浅色
        if self._selected:
            p.fillRect(r, QColor(c.hover_bg))

        # 月份标题
        p.setFont(self._font(f.mini_title, bold=True))
        p.setPen(QColor(c.today_border if self._selected else c.header_fg))
        p.drawText(QRect(r.left(), r.top(), r.width(), title_h),
                   Qt.AlignmentFlag.AlignCenter, f"{self.month}月")

        # 星期表头
        p.setFont(self._font(f.mini_head))
        p.setPen(QColor(c.sub_fg))
        for i, h in enumerate(_MINI_HEADERS):
            p.drawText(QRect(r.left() + i * cell_w, r.top() + title_h,
                             cell_w, head_h),
                       Qt.AlignmentFlag.AlignCenter, h)

        # 日期
        for i, d in enumerate(self._days):
            row, col = divmod(i, 7)
            cell = QRect(r.left() + col * cell_w, days_top + row * cell_h,
                         cell_w, cell_h)
            iso = d.isoformat()
            cnt = self._counts.get(iso, 0)
            in_month = d.month == self.month
            # 今天只在本月那格里画圈 — 相邻月份开头带出来的上个月尾巴不算
            is_today = iso == self._today and in_month

            if is_today:
                # 今天是正圆 — 直接拿 cell 拉会变成扁椭圆
                rad = min(cell.width(), cell.height()) * 0.42
                cc = cell.center()
                p.setBrush(QColor(c.today_bg))
                p.setPen(QPen(QColor(c.today_border), 1))
                p.drawEllipse(QRectF(cc.x() - rad, cc.y() - rad,
                                     rad * 2, rad * 2))
            elif in_month and cnt:
                p.setBrush(QColor(c.hover_bg))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(cell.adjusted(1, 1, -1, -1), 3, 3)

            if not in_month:
                # 不是本月的日子 → 不画数字 (「以月为单位」)
                continue
            if cnt:
                fg = _ITEM_FG
            elif d.weekday() >= 5:
                fg = _WEEKEND_FG
            else:
                fg = c.num_fg
            if is_today:
                fg = c.today_border
            p.setFont(self._font(f.mini_num, bold=bool(cnt) or is_today))
            p.setPen(QColor(fg))
            p.drawText(cell, Qt.AlignmentFlag.AlignCenter, str(d.day))

        # 外框 — 12 块挨在一起, 没边框会糊成一片
        frame = self.rect().adjusted(2, 2, -3, -3)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(c.today_border if self._selected else c.border),
                      2 if self._selected else 1))
        p.drawRoundedRect(frame, 4, 4)

        p.end()

    # ------------------------------------------------------------------
    # 鼠标
    # ------------------------------------------------------------------
    def _day_at(self, pos: QPoint) -> Optional[tuple[int, date]]:
        """坐标落在哪一天上 — 落在标题/表头区返回 None。"""
        r, title_h, head_h, cell_w, cell_h = self._geometry()
        days_top = r.top() + title_h + head_h
        if pos.y() < days_top:
            return None
        col = (pos.x() - r.left()) // cell_w
        row = (pos.y() - days_top) // cell_h
        if not (0 <= col < 7 and 0 <= row < self._rows):
            return None
        idx = row * 7 + col
        return idx, self._days[idx]

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(ev)
            return
        hit = self._day_at(ev.position().toPoint())
        if hit is None:
            # 点标题/表头 → 选中整个月
            self.sig_month_clicked.emit(self.year, self.month)
            return
        self.sig_day_clicked.emit(hit[1].isoformat())

    def event(self, ev) -> bool:
        """Tooltip 按天给 — 鼠标停在某天上时告诉用户那天有几件事。"""
        if ev.type() == QEvent.Type.ToolTip:
            hit = self._day_at(self.mapFromGlobal(ev.globalPos()))
            if hit is None:
                return True
            d = hit[1]
            cnt = self._counts.get(d.isoformat(), 0)
            tip = f"{d.month}月{d.day}日"
            if cnt:
                tip += f" · {cnt} 项"
            QToolTip.showText(ev.globalPos(), tip)
            return True
        return super().event(ev)

    # ------------------------------------------------------------------
    # 外部设置
    # ------------------------------------------------------------------
    def set_counts(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self.update()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def apply_skin(self, colors: CellColors) -> None:
        self._colors = colors
        self.update()

    def apply_fonts(self, fonts: Fonts) -> None:
        self._fonts = fonts
        self._font_cache.clear()     # 字号变了, 缓存里的字体作废
        self.setMinimumHeight(fonts.mini_h)
        self.update()


class YearView(QWidget):
    """年视图 — 上面 12 个小月 (3 行 × 4 列, 一行一个季度), 下面是详情。"""

    sig_data_changed = Signal()
    sig_month_changed = Signal(int, int)    # (year, month) — 焦点月份变了

    def __init__(self, skin_name: str = "light_default",
                 font_scale: float = DEFAULT_SCALE,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._skin_name = skin_name
        self._font_scale = font_scale
        self._colors = colors_for_skin(skin_name)
        self._fonts = fonts_for(font_scale)
        today = date.today()
        self._year = today.year
        self._month = today.month
        self._minis: list[MiniMonth] = []

        self._build()
        self._rebuild_minis()
        self._reload_counts()

    # ------------------------------------------------------------------
    # 搭界面
    # ------------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 4)
        lay.setSpacing(6)

        # 年份头: ‹ 2026 年 · 全年总览 ›
        head = QHBoxLayout()
        head.setSpacing(4)
        self._btn_prev_year = QToolButton()
        self._btn_prev_year.setText("‹")
        self._btn_prev_year.setToolTip("上一年")
        self._btn_prev_year.setAutoRaise(True)
        self._btn_next_year = QToolButton()
        self._btn_next_year.setText("›")
        self._btn_next_year.setToolTip("下一年")
        self._btn_next_year.setAutoRaise(True)
        self._year_lbl = QLabel()
        self._year_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._year_lbl.setMinimumWidth(150)

        head.addWidget(self._btn_prev_year)
        head.addWidget(self._year_lbl)
        head.addWidget(self._btn_next_year)
        head.addSpacing(14)
        self._hint_lbl = QLabel("点小月标题选中该月 · 点日期跳到那天")
        head.addWidget(self._hint_lbl)
        head.addStretch()
        self._detail_lbl = QLabel()
        head.addWidget(self._detail_lbl)
        lay.addLayout(head)

        # 12 个小月: 3 行 × 4 列
        self._grid = QGridLayout()
        self._grid.setSpacing(6)
        lay.addLayout(self._grid, 0)

        # 详情 — 直接复用月视图, 交互跟月视图一模一样
        self._detail = MonthView(skin_name=self._skin_name,
                                  fonts=self._detail_fonts())
        self._detail.sig_data_changed.connect(self._on_detail_changed)
        lay.addWidget(self._detail, 1)

        self._btn_prev_year.clicked.connect(lambda: self.shift_year(-1))
        self._btn_next_year.clicked.connect(lambda: self.shift_year(+1))
        self._style_header()

    def _detail_fonts(self) -> Fonts:
        """详情那块的字号表 — 格子高度压矮一点。

        年视图上下要摆 12 个小月, 详情还是整块 6 行月视图的话, 整个视图
        的最小高度会顶到 850px 以上, 1366×768 的屏上直接放不下。格子矮
        一些, 挤一点没关系 —— 小月里本来也只显示日期号。
        """
        return replace(self._fonts,
                       cell_min_h=max(40, int(self._fonts.cell_min_h * 0.6)))

    def _rebuild_minis(self) -> None:
        """换年份时重建 12 个小月 — 每个月是固定的一块, 换年份整块换掉。"""
        for m in self._minis:
            self._grid.removeWidget(m)
            m.deleteLater()
        self._minis.clear()
        for i in range(12):
            mm = MiniMonth(self._year, i + 1,
                           colors=self._colors, fonts=self._fonts)
            mm.sig_day_clicked.connect(self._on_day_clicked)
            mm.sig_month_clicked.connect(self._on_month_clicked)
            row, col = divmod(i, 4)
            self._grid.addWidget(mm, row, col)
            self._minis.append(mm)
        for c in range(4):
            self._grid.setColumnStretch(c, 1)

    def _style_header(self) -> None:
        f, c = self._fonts, self._colors
        self._year_lbl.setStyleSheet(
            f"font-size: {f.month}pt; font-weight: bold; color: {c.header_fg};"
        )
        self._hint_lbl.setStyleSheet(
            f"font-size: {f.status}pt; color: {c.sub_fg};"
        )
        self._detail_lbl.setStyleSheet(
            f"font-size: {f.status}pt; color: {c.header_fg};"
        )

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def _reload_counts(self) -> None:
        """把今年所有条目按天统计一次 — 小月上标出来。"""
        counts: Counter = Counter()
        try:
            con = open_default()
            rows = list_items_in_range(
                con, f"{self._year:04d}-01-01", f"{self._year:04d}-12-31",
            )
            for it in rows:
                if it.date:
                    counts[it.date] += 1
        except Exception as e:
            print(f"[YearView] 统计条目失败: {e}")
        for m in self._minis:
            m.set_counts(dict(counts))

    # ------------------------------------------------------------------
    # 导航
    # ------------------------------------------------------------------
    @property
    def detail_view(self) -> MonthView:
        """下方那块详情 — 主窗口拿它当「当前月视图」用。"""
        return self._detail

    def current_month(self) -> tuple[int, int]:
        return self._year, self._month

    def goto(self, y: int, m: int) -> None:
        """跳到某年某月 — 换年要重建小月, 换月只动详情。"""
        if y != self._year:
            self._year = y
            self._rebuild_minis()
        self._month = m
        self._detail.goto(y, m)
        # 小月上标的条目数要跟着刷新 — 换月/换年都可能带着新数据过来
        self._reload_counts()
        self._refresh_header()
        self._mark_selected()
        # 告诉外面焦点月份变了 — 工具栏标签/计数得跟着走
        self.sig_month_changed.emit(y, m)

    def goto_date(self, day_iso: str) -> None:
        """跳到某一天 — 先定位月份, 再让详情那块高亮这一天。"""
        try:
            y, m, _d = (int(x) for x in day_iso.split("-"))
        except ValueError:
            return
        self.goto(y, m)
        self._detail.goto_date(day_iso)

    def shift_month(self, delta: int) -> None:
        y, m = self._year, self._month + delta
        while m < 1:
            m += 12; y -= 1
        while m > 12:
            m -= 12; y += 1
        self.goto(y, m)

    def shift_year(self, delta: int) -> None:
        self.goto(self._year + delta, self._month)

    def refresh(self) -> None:
        """外部改了数据库 — 详情和小月的统计一起刷。"""
        self._detail.refresh()
        self._reload_counts()

    # ------------------------------------------------------------------
    # 皮肤 / 字号
    # ------------------------------------------------------------------
    def apply_skin(self, skin_name: str) -> None:
        self._skin_name = skin_name
        self._colors = colors_for_skin(skin_name)
        for m in self._minis:
            m.apply_skin(self._colors)
        self._detail.apply_skin(skin_name)
        self._style_header()

    def apply_font_scale(self, scale: float) -> None:
        self._font_scale = scale
        self._fonts = fonts_for(scale)
        for m in self._minis:
            m.apply_fonts(self._fonts)
        self._detail.apply_fonts(self._detail_fonts())
        self._style_header()

    # ------------------------------------------------------------------
    # 内部槽
    # ------------------------------------------------------------------
    def _on_month_clicked(self, y: int, m: int) -> None:
        self.goto(y, m)

    def _on_day_clicked(self, day_iso: str) -> None:
        self.goto_date(day_iso)

    def _on_detail_changed(self) -> None:
        """详情里改了东西 — 小月的统计也得跟着变。"""
        self._reload_counts()
        self.sig_data_changed.emit()

    def _mark_selected(self) -> None:
        for m in self._minis:
            m.set_selected(m.month == self._month)

    def _refresh_header(self) -> None:
        self._year_lbl.setText(f"{self._year} 年 · 全年总览")
        self._detail_lbl.setText(f"下方详情: {self._month} 月")


__all__ = ["MiniMonth", "YearView"]
