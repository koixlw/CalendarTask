"""月视图 — 6×7 格子, 点击编辑, 图标类型化, 颜色条。

交互:
  - 单击 ☐   勾选/取消勾选这一行 (立刻存库)
  - 双击 文字 打开该条目的编辑器
  - 右键      当天菜单 (新建 / 编辑 / 删除)
  - 空白处双击 新建

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
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPalette, QPainter
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QMenu, QSizePolicy,
)

from pycal.db.connection import open_default
from pycal.db.repository import list_items_in_range, upsert_item, get_item
from pycal.db.models import CalendarItem
from pycal.ui.color_picker import color_to_hex
from pycal.ui.fonts import Fonts, DEFAULT_SCALE, fonts_for
from pycal.ui.outline_label import HaloLabel


# ============================================================================
# 类型图标 (从 CALENDAR_ICON map 缓存到 QPixmap)
# ============================================================================
_TYPE_GLYPH = {
    1: "↻",   # 重复
    2: "⏰",  # 提醒
    3: "🎯",  # 倒数日 (U+1F3AF 不通用, fallback)
}

_HEADERS = ["一", "二", "三", "四", "五", "六", "日"]


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


def rows_for_display(
    items: list[CalendarItem],
) -> list[tuple[CalendarItem, str, bool, Optional[int]]]:
    """把当天的条目摊平成可勾选的行: (条目, 文字, 已完成, line_idx)。

    line_idx=None 表示这一行勾的是「整条完成」(is_completed 字段),
    用于有标题但没有多行内容的条目, 以及重复/提醒/倒数日这类整条型条目。

    月视图的格子和快捷待办面板都用这一份摊平逻辑 —— 两边看到的
    条目顺序、勾选方式必须一致, 不然面板里勾了格子那边没反应。
    """
    out: list[tuple[CalendarItem, str, bool, Optional[int]]] = []
    for it in items:
        if it.type != 0:
            title = it.title or (
                it.content.splitlines()[0] if it.content else "(无标题)"
            )
            out.append((it, f"{_glyph_for_type(it.type)} {title}",
                        bool(it.is_completed), None))
            continue
        lines = _parse_lines(it.content)
        if lines:
            for idx, (completed, text) in enumerate(lines):
                out.append((it, text, completed, idx))
        elif it.title:
            out.append((it, it.title, bool(it.is_completed), None))
    return out


# ============================================================================
# 配色 — 由皮肤派生 (深色皮肤下格子不再白底黑字)
# ============================================================================
@dataclass(frozen=True)
class CellColors:
    body_bg: str = "#FFFFFF"
    muted_bg: str = "#F5F5F5"      # 非本月的格子
    today_bg: str = "#FFF8D2"
    hover_bg: str = "#F0F8FF"
    border: str = "#BBBBBB"
    today_border: str = "#0066CC"
    num_fg: str = "#222222"
    muted_fg: str = "#AAAAAA"
    done_fg: str = "#9AA0A6"       # 已完成那行的字色
    text_fg: str = "#222222"
    sub_fg: str = "#888888"
    header_fg: str = "#555555"
    # ── 桌面挂件模式 ──
    transparent: bool = False   # 格子不填底色 (壁纸直接透出来)
    halo: str = ""              # 文字描边色 (空 = 不描边)


_DARK_SKIN_NAMES = {"dark_mode"}


def _qcolor(value: str) -> QColor:
    """颜色字符串 → QColor。

    QSS 认得 `rgba(r,g,b,a)`, 但 QColor 不认 —— 直接塞进去会得到一个
    「不透明的黑」(isValid() 仍然是 True, 特别坑)。桌面挂件那套配色用的
    就是 rgba(), 这里自己解析一遍。
    """
    s = (value or "").strip()
    low = s.lower()
    if low.startswith("rgba") or low.startswith("rgb"):
        inner = s[s.find("(") + 1:s.rfind(")")]
        parts = [x.strip() for x in inner.split(",") if x.strip()]
        try:
            r, g, b = (int(float(x)) for x in parts[:3])
            a = int(float(parts[3])) if len(parts) > 3 else 255
            return QColor(r, g, b, a)
        except (ValueError, IndexError):
            return QColor()
    return QColor(s)


def colors_for_skin(skin_name: str) -> CellColors:
    """按皮肤名返回格子配色 — dark_mode 和 desktop_widget 各一套。"""
    if skin_name in _DARK_SKIN_NAMES:
        return CellColors(
            body_bg="#2B2B30", muted_bg="#232327", today_bg="#4A4326",
            hover_bg="#33333A", border="#4A4A52", today_border="#4A90E2",
            num_fg="#ECEFF4", muted_fg="#6E6E76", done_fg="#75797F",
            text_fg="#ECEFF4", sub_fg="#9AA0A6", header_fg="#C8CDD6",
        )
    if skin_name == "desktop_widget":
        # 桌面挂件 — 格子全透明, 只有网格线 + 带描边的字
        return CellColors(
            body_bg="transparent", muted_bg="transparent",
            today_bg="rgba(255, 205, 60, 60)",
            hover_bg="rgba(255, 255, 255, 70)",
            border="rgba(255, 255, 255, 70)",
            today_border="rgba(255, 205, 60, 220)",
            num_fg="#FFFFFF", muted_fg="rgba(255, 255, 255, 140)",
            done_fg="rgba(255, 255, 255, 150)", text_fg="#FFFFFF",
            sub_fg="rgba(255, 255, 255, 200)",
            header_fg="rgba(255, 255, 255, 235)",
            transparent=True, halo="rgba(0, 0, 0, 175)",
        )
    return CellColors()


# ============================================================================
# 勾选写回 — 把 content 里第 N 条有效行的 [+] 标记加上/去掉
# ============================================================================
def _toggle_line_in_content(content: str, line_idx: int, mark_done: bool) -> str:
    """改写 content — line_idx 是「跳过空行后」的序号, 与 _parse_lines 对齐。

    空行原样保留, 行序不变, 只动目标行的 [+] 前缀。
    """
    out: list[str] = []
    seen = 0
    for raw in (content or "").splitlines():
        if not raw.strip():
            out.append(raw)
            continue
        if seen == line_idx:
            m = _LINE_RE.match(raw)
            body = m.group(1).strip() if m else raw.strip()
            out.append(f"[+] {body}" if mark_done else body)
        else:
            out.append(raw)
        seen += 1
    return "\n".join(out)


# ============================================================================
# LineRow — 一行内容 (☐ + 文字)
# ============================================================================
class LineRow(QWidget):
    """月视图里的一行内容。

    单击左侧 ☐ → 勾选/取消 (sig_toggled); 双击文字 → 打开条目 (sig_open)。

    line_idx 是该行在 item.content 里的序号 (跳过空行);
    line_idx 为 None 表示这是「整条完成」开关 — 写 is_completed 字段。
    """

    sig_toggled = Signal(str, object, bool)   # (item_id, line_idx|None, 新状态)
    sig_open = Signal(str, object)            # (item_id, line_idx|None)

    def __init__(self, parent: Optional[QWidget] = None,
                 fonts: Optional[Fonts] = None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(3)

        self._fonts = fonts or fonts_for()
        self._font = QFont()
        self._font.setPointSize(self._fonts.row)

        self._box = HaloLabel("☐")
        self._box.setFont(self._font)
        self._box.setFixedWidth(self._fonts.box_w)
        self._box.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._text = HaloLabel("")
        self._text.setFont(self._font)
        self._text.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        self._lay.addWidget(self._box, 0)
        self._lay.addWidget(self._text, 1)
        # 行高跟着字号走 — 只放大字不放开高, 字会被裁掉
        self.setFixedHeight(self._fonts.row_h)

        self._item_id = ""
        self._line_idx: Optional[int] = None
        self._label = ""
        self._done = False
        self._fg = "#222222"
        self._done_fg = "#9AA0A6"
        self._halo = ""

        self._box.setCursor(Qt.CursorShape.PointingHandCursor)
        self._box.setToolTip("单击打勾")
        # QFontMetrics 缓存 — 每行每次 resize 都新建一个太浪费:
        # 满屏 180 行 × 拖一次窗口 = 180 个 QFontMetrics。
        self._metrics: Optional[QFontMetrics] = None

    def apply_fonts(self, fonts: Fonts) -> None:
        """换字号 — 行高 / ☐ 宽度 / 截断宽度都要跟着重算。"""
        self._fonts = fonts
        self._font.setPointSize(fonts.row)
        self._box.setFont(self._font)
        self._box.setFixedWidth(fonts.box_w)
        self.setFixedHeight(fonts.row_h)
        self._metrics = None      # 字号变了, 旧的度量作废
        self._render()

    # ------------------------------------------------------------------
    def bind(self, item_id: str, text: str, done: bool,
             line_idx: Optional[int], fg: str, done_fg: str,
             halo: str = "") -> None:
        self._item_id = item_id
        self._line_idx = line_idx
        self._label = text
        self._done = done
        self._fg = fg
        self._done_fg = done_fg
        self._halo = halo
        self._label = text
        self._render()

    def _render(self) -> None:
        self._box.setText("☑" if self._done else "☐")
        fg = self._done_fg if self._done else self._fg
        # 字色走调色板 — setStyleSheet 要重解析样式表, 一格 5 行 × 42 格
        # = 400 多次, 翻一次月光这就得好几十毫秒。
        for lbl in (self._box, self._text):
            pal = lbl.palette()
            pal.setColor(QPalette.ColorRole.WindowText, _qcolor(fg))
            lbl.setPalette(pal)
            # 描边 — 挂件模式字直接压在壁纸上, 没描边就看不清
            lbl.setHalo(self._halo)
        # 已完成的行加删除线 — QSS 不支持 text-decoration, 走 QFont
        self._font.setStrikeOut(self._done)
        self._text.setFont(self._font)
        self._elide()

    def _elide(self) -> None:
        """一行放不下就截成省略号 — 完整内容双击进编辑器看。"""
        if self.width() <= 0:
            return    # 还没被布局定过尺寸 — 等 resizeEvent 再截
        avail = max(10, self.width() - self._box.width() - self._lay.spacing() - 2)
        if self._metrics is None:
            self._metrics = QFontMetrics(self._font)
        self._text.setText(self._metrics.elidedText(
            self._label, Qt.TextElideMode.ElideRight, avail))

    def resizeEvent(self, ev):
        self._elide()
        super().resizeEvent(ev)

    def set_done_visually(self, done: bool) -> None:
        """从外部同步状态 (不发射信号) — 存库失败时回滚用。"""
        self._done = done
        self._render()

    # ------------------------------------------------------------------
    def mousePressEvent(self, ev):
        # 只认左侧的 ☐ — 避免和「双击打开编辑器」互相打架
        if ev.button() == Qt.MouseButton.LeftButton and \
                ev.position().x() <= self._box.width() + 4:
            self._done = not self._done
            self._render()
            self.sig_toggled.emit(self._item_id, self._line_idx, self._done)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.sig_open.emit(self._item_id, self._line_idx)
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)


# ============================================================================
# DayCell — 单日格
# ============================================================================
class DayCell(QFrame):
    """单日格 widget — 显示日期号、类型标记、内容行 (每行可勾选)。"""

    sig_single_click = Signal(str)         # day_iso (左键单击空白处)
    sig_double_click = Signal(str)         # day_iso (左键双击 — 编辑/新建)
    sig_right_click = Signal(str)          # day_iso (右键 — 弹出菜单)
    sig_toggle_line = Signal(str, object, bool)   # (item_id, line_idx|None, 新状态)
    sig_open_item = Signal(str)            # (item_id) — 双击某一行

    def __init__(
        self,
        day_iso: str,
        day_num: int,
        in_month: bool = True,
        is_today: bool = False,
        is_weekend: bool = False,
        colors: Optional[CellColors] = None,
        fonts: Optional[Fonts] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        # ID 选择器 — 用类型选择器会连格子里的 QLabel 一起框上
        # (QLabel 是 QFrame 的子类), 整格会变成一层套一层的蓝格子
        self.setObjectName("DayCell")
        self.day_iso = day_iso
        self.day_num = day_num          # _build() 里要用
        self.in_month = in_month
        self.is_today = is_today
        self.is_weekend = is_weekend
        self._items: list[CalendarItem] = []
        self._colors = colors or CellColors()
        self._fonts = fonts or fonts_for()
        self._hovered = False
        self._rows: list[LineRow] = []

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(self._fonts.cell_min_w, self._fonts.cell_min_h)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._highlight = False
        self._bg = "#FFFFFF"
        self._border_color = "#BBBBBB"
        self._border_w = 1
        self._build()
        self._refresh_bg()

    def paintEvent(self, ev) -> None:
        """自己画边框。

        底色交给调色板 (autoFillBackground), 边框没有调色板角色, 只能自己画。
        原来这块是挂一句 QSS (`#DayCell { background; border }`), 每次换样式
        Qt 都要把整棵子树重新布局 (一格 5 行 × 2 个标签), 翻一个月几十个格子
        就是几百次重排 —— 实测一次 0.6ms, 光这一项翻一次月就 25ms。
        """
        p = QPainter(self)
        bg = _qcolor(self._bg)
        if bg.alpha() > 0:
            # 全透明 (挂件模式) 就不画底色 — 跟原来 QSS 的
            # `background: transparent` 一个意思, 让壁纸直接透出来
            p.fillRect(self.rect(), bg)
        w = self._border_w
        c = _qcolor(self._border_color)
        r = self.rect()
        p.fillRect(r.left(), r.top(), r.width(), w, c)
        p.fillRect(r.left(), r.bottom() - w + 1, r.width(), w, c)
        p.fillRect(r.left(), r.top(), w, r.height(), c)
        p.fillRect(r.right() - w + 1, r.top(), w, r.height(), c)
        p.end()

    def set_highlight(self, on: bool) -> None:
        """高亮某一天 (搜索跳转用) — 换底色和边框颜色。"""
        self._highlight = on
        self._refresh_bg()
        self.update()

    def _apply_subtitle(self, text: str, is_jieqi: bool = False) -> None:
        """显示农历/节气, 节气用红色突出。

        颜色走 QPalette、字形走 QFont, 不碰 QSS ——
        setStyleSheet 每调一次都要重新解析样式表 + 重算样式, 单次 0.2ms,
        翻月时几十个格子一起改就是几十毫秒。
        """
        if is_jieqi:
            self._set_sub_style("#C0392B", bold=True, italic=True)
            self._sub_lbl.setText(f"⚡ {text}")
        elif text in ("春节", "元宵", "端午", "中秋", "重阳"):
            self._set_sub_style("#C0392B", bold=True, italic=False)
            self._sub_lbl.setText(text)
        else:
            self._set_sub_style(self._colors.sub_fg, bold=False, italic=True)
            self._sub_lbl.setText(text)

    def _set_sub_style(self, color: str, bold: bool, italic: bool) -> None:
        """副标题的颜色和字形 — 调色板 + QFont。"""
        self._set_label_color(self._sub_lbl, color)
        f = QFont(self._sub_lbl.font())
        f.setBold(bold)
        f.setItalic(italic)
        f.setPointSize(self._fonts.sub)
        self._sub_lbl.setFont(f)

    def _set_label_color(self, label: QLabel, color: str) -> None:
        """给标签换字色 — 走调色板, 不用 QSS (快一个数量级)。

        挂件模式 (transparent) 下格子没有底色, 字直接压在壁纸上 ——
        顺手加一圈描边, 否则亮壁纸上浅色字就看不见了。
        """
        pal = label.palette()
        pal.setColor(QPalette.ColorRole.WindowText, _qcolor(color))
        label.setPalette(pal)
        label.setHalo(self._colors.halo if self._colors.transparent else "")

    def _build(self) -> None:
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(4, 2, 4, 4)
        self._lay.setSpacing(1)

        # 顶部: 日期号 + 类型小图标
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        # 日期号和农历之间留点缝 —— 挂件里格子窄, 贴一起就糊成一团
        top.setSpacing(3)
        self._num_lbl = HaloLabel(str(self.day_num) if self.in_month else "")
        top.addWidget(self._num_lbl)

        # 节假日/lunar 副标题
        self._sub_lbl = HaloLabel("")
        top.addWidget(self._sub_lbl)

        top.addStretch()
        self._type_lbl = HaloLabel("")
        top.addWidget(self._type_lbl)
        self._lay.addLayout(top)

        # 内容行 (每行一个可勾选的 LineRow)
        self._rows_lay = QVBoxLayout()
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(0)
        self._lay.addLayout(self._rows_lay, 1)

        # "+N 更多"
        self._more_lbl = HaloLabel("")
        self._more_lbl.setVisible(False)
        self._lay.addWidget(self._more_lbl)

        self._apply_fonts()

    # ------------------------------------------------------------------
    def _apply_fonts(self) -> None:
        """把 self._fonts 落到各个标签上 — 日期号粗细跟着 is_today 走。"""
        f = self._fonts
        self._update_num_font()

        # 副标题 / "+N 更多" 的字号和颜色都写在 _refresh_bg 的 QSS 里, 那边
        # QSS 一旦带了 font-size 就会盖掉 QFont, 所以只让它一处说了算。
        self._type_lbl.setStyleSheet(f"font-size: {f.type_icon}pt;")
        for row in self._rows:
            row.apply_fonts(f)
        self._refresh_bg()

    def _update_num_font(self) -> None:
        """日期号的字体 — 今天加粗并用大一号。"""
        f = self._fonts
        num_font = QFont()
        num_font.setBold(self.is_today)
        num_font.setPointSize(f.num_today if self.is_today else f.num)
        self._num_lbl.setFont(num_font)

    def rebind(self, day_iso: str, day_num: int, in_month: bool,
               is_today: bool, subtitle: str = "") -> None:
        """换一天 — 复用这个 widget, 不重建。

        翻月时整块 42 个格子全部销毁重建的话, 光建控件就要 ~90ms
        (每个格子还带 5 个 LineRow, 一行又是 3 个控件)。复用之后
        翻月几乎不花钱 —— 实测 95ms → 几 ms。
        """
        self.day_iso = day_iso
        self.day_num = day_num
        self.in_month = in_month
        self.is_today = is_today
        self._highlight = False       # 换了日子, 上一次的高亮就不算了
        self._num_lbl.setText(str(day_num) if in_month else "")
        self._update_num_font()
        self.set_items([])
        self._refresh_bg()
        # _refresh_bg 会重置副标题样式, 所以副标题放在它后面设 —
        # 节气的红色才会盖住默认灰色。
        self.set_subtitle(subtitle)

    def apply_fonts(self, fonts: Fonts) -> None:
        """外部调字号 — 字号大了, 每格能显示的条数也跟着变少。"""
        self._fonts = fonts
        self.setMinimumSize(fonts.cell_min_w, fonts.cell_min_h)
        self._apply_fonts()
        self._refresh()

    # ------------------------------------------------------------------
    # 公共
    # ------------------------------------------------------------------
    def set_items(self, items: list[CalendarItem]) -> None:
        self._items = items
        self._refresh()

    def set_palette(self, colors: CellColors) -> None:
        """换皮肤时刷新配色 — 已有行也要重刷一次字色。"""
        self._colors = colors
        self._refresh_bg()
        self._refresh()

    def set_subtitle(self, text: str) -> None:
        """外部调用入口 — 同时处理 jieqi 标记与节日颜色。"""
        if not self.in_month:
            # 不是本月的格子 → 不显示日期号, 也不显示农历/节气。
            # 「以月为单位」: 这一格是上/下个月的日子, 空着就行。
            self._sub_lbl.setText("")
            # 样式也得跟着复位 — 否则上一轮显示节气时留下的红色会留在这儿
            self._set_sub_style(self._colors.sub_fg, bold=False, italic=True)
            return
        # text 可能已被 _subtitle_for 加上 "⚡ " 前缀
        is_jieqi = text.startswith("⚡ ")
        clean = text[2:] if is_jieqi else text
        self._apply_subtitle(clean, is_jieqi=is_jieqi)

    def _refresh_bg(self) -> None:
        c = self._colors
        if self._highlight:
            bg, fg = "#FFE5B4", c.num_fg
            border_color, border_w = "#FF8C00", 2
        elif not self.in_month:
            bg, fg = c.muted_bg, c.muted_fg
            border_color, border_w = c.border, 1
        elif self.is_today:
            bg, fg = c.today_bg, c.num_fg
            border_color, border_w = c.today_border, 2
        else:
            bg, fg = c.body_bg, c.num_fg
            border_color, border_w = c.border, 1
        if self._hovered and not self.is_today and not self._highlight:
            bg, border_color, border_w = c.hover_bg, "#4A90E2", 1
        self._border_color = border_color
        self._border_w = border_w
        # 底色走调色板 — 调色板改动比 QSS 便宜一个数量级
        self._bg = bg
        self._set_label_color(self._num_lbl, fg)
        self._set_label_color(self._sub_lbl, c.sub_fg)
        self._set_label_color(self._more_lbl, c.sub_fg)
        self._set_label_color(self._type_lbl, c.text_fg)
        # 字号用 QFont 定住 — QSS 一旦带了 font-size 就会盖掉 QFont
        more_font = QFont(self._more_lbl.font())
        more_font.setPointSize(self._fonts.more)
        self._more_lbl.setFont(more_font)

    # ------------------------------------------------------------------
    def _rows_for_display(self) -> list[tuple[CalendarItem, str, bool, Optional[int]]]:
        """把当天的条目摊平成可勾选的行 —— 逻辑在模块级的 rows_for_display。"""
        return rows_for_display(self._items)

    def _refresh(self) -> None:
        # 类型图标: 第一个非格子条目的类型
        types_in_cell = sorted({it.type for it in self._items if it.type != 0})
        self._type_lbl.setText(
            _glyph_for_type(types_in_cell[0]) if types_in_cell else ""
        )

        rows = self._rows_for_display()
        # 字号越大, 一格塞得下的条数越少 — fonts_for 已经算好
        cap = self._fonts.max_rows
        shown, overflow = rows[:cap], len(rows) - cap

        # 复用已有的 LineRow — 避免每次刷新重建 42×max_rows 个控件
        while len(self._rows) < len(shown):
            row = LineRow(self, fonts=self._fonts)
            row.sig_toggled.connect(self._on_row_toggled)
            row.sig_open.connect(self._on_row_open)
            self._rows_lay.addWidget(row)
            self._rows.append(row)

        for i, row in enumerate(self._rows):
            if i < len(shown):
                it, text, done, idx = shown[i]
                fg = color_to_hex(it.color_ref) if it.color_ref \
                    else self._colors.text_fg
                row.bind(it.item_id, text, done, idx, fg, self._colors.done_fg,
                         self._colors.halo)
                row.setVisible(True)
            else:
                row.setVisible(False)

        self._more_lbl.setVisible(overflow > 0)
        if overflow > 0:
            self._more_lbl.setText(f"+{overflow} 更多")

    def _on_row_toggled(self, item_id: str, line_idx, done: bool) -> None:
        self.sig_toggle_line.emit(item_id, line_idx, done)

    def _on_row_open(self, item_id: str, line_idx) -> None:
        self.sig_open_item.emit(item_id)

    # ------------------------------------------------------------------
    # 鼠标事件
    # ------------------------------------------------------------------
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.RightButton:
            self.sig_right_click.emit(self.day_iso)
        elif ev.button() == Qt.MouseButton.LeftButton:
            self.sig_single_click.emit(self.day_iso)
        # 吃掉这次按下 — 别让它冒到父窗口去: 主窗那边会拿边缘 resize 判定,
        # 桌面挂件那边会当成拖动挂件。点格子就是点格子。
        ev.accept()

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.sig_double_click.emit(self.day_iso)
            ev.accept()

    def enterEvent(self, ev):
        self._hovered = True
        self._refresh_bg()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hovered = False
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
    sig_data_changed = Signal()            # 勾选/增删后 — 主窗据此刷新计数

    def __init__(self, parent: Optional[QWidget] = None,
                 skin_name: str = "light_default",
                 font_scale: float = DEFAULT_SCALE,
                 fonts: Optional[Fonts] = None):
        super().__init__(parent)
        self._cur = date.today().replace(day=1)
        # 外层: 星期表头一行 + 格子网格
        # 表头单独一层, 格子网格固定 6 行 × 7 列 —— 翻月只改格子里���内容,
        # 布局结构一动不动。反复 removeWidget/addWidget 重排格子既慢,
        # 又会把 QGridLayout 的内部索引搞乱 (能在退出时炸掉)。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        self._head_grid = QGridLayout()
        self._head_grid.setSpacing(2)
        outer.addLayout(self._head_grid)
        self._grid = QGridLayout()
        self._grid.setSpacing(2)
        outer.addLayout(self._grid, 1)
        self._cells: dict[str, DayCell] = {}
        # 格子池 — 所有月份的格子都从这里取, 翻月时复用不重建
        self._pool: list[DayCell] = []
        self._weeks = 6
        self._header_lbls: list[QLabel] = []
        self._colors = colors_for_skin(skin_name)
        # fonts 直接传进来的, 就别再按比例重算 — 年视图里详情那块要塞得下,
        # 用的就是一份改过格子高度的字号表
        self._fonts = fonts or fonts_for(font_scale)

        # 双击直接进编辑 (默认双击编辑该日第一条或新建)
        self.sig_edit_day.connect(self._handle_double_click)
        self.sig_new_item.connect(self._handle_new_request)
        self.sig_edit_item.connect(self._handle_edit_item)
        self.sig_request_menu.connect(self._show_context_menu)

        self._build_grid()
        self._render()

    def _build_grid(self) -> None:
        """星期表头 + 6×7 的格子 — 一次建好, 之后翻月只改内容。

        以前这里是「每个月都把 42 个格子销毁重建」—— 满月的月份光建控件
        就要 90ms (每个格子还带 5 个 LineRow)。建一次, 后面全靠复用。
        """
        for col, h in enumerate(_HEADERS):
            lbl = HaloLabel(h)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._head_grid.addWidget(lbl, 0, col)
            self._header_lbls.append(lbl)
        self._style_headers()

        for row in range(6):
            for col in range(7):
                cell = DayCell("", 0, colors=self._colors, fonts=self._fonts)
                cell.sig_double_click.connect(self.sig_edit_day)
                cell.sig_single_click.connect(self._handle_single_click)
                cell.sig_right_click.connect(self._handle_right_click)
                cell.sig_toggle_line.connect(self._handle_toggle)
                cell.sig_open_item.connect(self.sig_edit_item)
                self._grid.addWidget(cell, row, col)
                self._pool.append(cell)

    # ------------------------------------------------------------------
    # 皮肤 / 字号
    # ------------------------------------------------------------------
    def _style_headers(self) -> None:
        """星期表头的颜色 + 字号 — QSS 里带 font-size, 会盖掉 QFont。"""
        qss = (
            f"font-weight: bold; color: {self._colors.header_fg};"
            f" font-size: {self._fonts.row}pt; padding: 2px;"
        )
        for lbl in self._header_lbls:
            lbl.setStyleSheet(qss)
            # 挂件模式表头也是压在壁纸上的字 — 一样要描边
            lbl.setHalo(self._colors.halo if self._colors.transparent else "")

    def apply_skin(self, skin_name: str) -> None:
        """换皮肤时把新配色推给所有格子 (含星期表头)。"""
        self._colors = colors_for_skin(skin_name)
        self._style_headers()
        for cell in self._pool:
            cell.set_palette(self._colors)

    def apply_palette_override(self, **overrides) -> None:
        """只改配色里的某几项, 推给所有格子。

        桌面挂件用它按壁纸亮度换字色 —— 亮壁纸用深字, 暗壁纸用浅字,
        不然总有一半的壁纸上看不清。
        """
        from dataclasses import replace
        self._colors = replace(self._colors, **overrides)
        self._style_headers()
        for cell in self._pool:
            cell.set_palette(self._colors)

    def apply_font_scale(self, scale: float) -> None:
        """调字号 — 传比例即可, 具体的 pt / 行高由 fonts_for 算。"""
        self.apply_fonts(fonts_for(scale))

    def apply_fonts(self, fonts: Fonts) -> None:
        """直接用一份算好的字号表 — 年视图里详情那块用压缩过的格子高度。"""
        self._fonts = fonts
        self._style_headers()
        for cell in self._pool:
            cell.apply_fonts(self._fonts)

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
            cell.set_highlight(True)

    def refresh(self) -> None:
        """外部更新数据库后, 重新拉数据并刷新 UI。"""
        self._refresh_data()

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _render(self) -> None:
        first_weekday = self._cur.weekday()  # 0=Mon
        start = self._cur - timedelta(days=first_weekday)
        today_iso = date.today().isoformat()

        # 只画这个月真正占到的周数 — 固定 6 行的话, 有 5 周的月份底下
        # 会空出一整行下个月的日子 (10 月就是这样: 最后一行全是 11 月)。
        from calendar import monthrange
        days_in_month = monthrange(self._cur.year, self._cur.month)[1]
        weeks = (first_weekday + days_in_month + 6) // 7   # 向上取整
        self._weeks = weeks
        need = weeks * 7

        self._cells.clear()
        for i in range(need):
            d = start + timedelta(days=i)
            cell = self._pool[i]
            cell.rebind(
                d.isoformat(), d.day,
                in_month=d.month == self._cur.month,
                is_today=d.isoformat() == today_iso,
                subtitle=self._subtitle_for(d),
            )
            cell.setVisible(True)
            self._cells[d.isoformat()] = cell

        # 用不上的格子 (上个月 6 周 → 这个月 5 周) 收起来, 不占地方
        for cell in self._pool[need:]:
            cell.setVisible(False)
            cell.set_items([])

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
            # 只取这个月真正用到的那几周 — 5 周的月份不用多扫一周
            last = first + timedelta(days=self._weeks * 7 - 1)
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
        self.sig_data_changed.emit()

    def _handle_toggle(self, item_id: str, line_idx, done: bool) -> None:
        """勾选写库 — line_idx=None 改 is_completed, 否则改 content 里的 [+]。

        存库失败时把格子里的勾回滚, 免得 UI 和数据库对不上。
        """
        con = open_default()
        it = get_item(con, item_id)
        if it is None:
            print(f"[MonthView] 勾选失败: 找不到条目 {item_id}")
            self._refresh_data()
            return
        try:
            if line_idx is None:
                it.is_completed = 1 if done else 0
            else:
                it.content = _toggle_line_in_content(it.content, line_idx, done)
            upsert_item(con, it)
        except Exception as e:
            print(f"[MonthView] 勾选存库失败: {e}")
            self._refresh_data()      # 回滚 UI
            return
        # 只刷受影响的那一格 —— 整月重刷要重新拉库 + 重绑 42 个格子,
        # 满月的月份点一下勾要 16ms, 其实只有一格变了。
        cell = self._cells.get(it.date) if it.date else None
        if cell is None:
            self._refresh_data()
        else:
            for c_it in cell._items:
                if c_it.item_id == item_id:
                    c_it.is_completed = it.is_completed
                    c_it.content = it.content
                    break
            cell.set_items(cell._items)
        self.sig_data_changed.emit()

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
            self.sig_data_changed.emit()

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
            self.sig_data_changed.emit()
