"""桌面挂件 — 贴在桌面层的小月历。

跟主窗的区别:
  * 格子不填底色 → 壁纸从字缝里直接透出来, 文字自带描边保证看得清
  * 窗口小得多, 默认贴在屏幕右下角, 拖标题栏能挪
  * 层级在**桌面层** (WindowStaysOnBottomHint) —— 别的窗口盖得住它,
    它不会跳出来抢你正在写的文档; 想浮在最上面, 菜单里能切
  * 字色跟着壁纸亮度自动反色 (亮壁纸深字, 暗壁纸浅字)

主窗和托盘菜单都通过 DesktopWidgetManager 开关它。
"""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import date
from typing import Optional

from PySide6.QtCore import Qt, QPoint, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QMenu, QToolButton, QVBoxLayout, QWidget,
)

from pycal.settings import get_setting, set_setting
from pycal.ui.fonts import fonts_for, scale_from_setting
from pycal.ui.month_view import CellColors, MonthView
from pycal.ui.outline_label import HaloLabel


# 挂件比主窗小一号 —— 角落里挂个小日历, 太大就挡桌面了
DEFAULT_WIDGET_SCALE = 0.85

# 一格里显示几条 —— 挂件的格子矮, 多了会溢出格子
WIDGET_MAX_ROWS = 2

# 挂件大小档位 (字号比例)
SIZE_PRESETS = (
    ("小", "0.70", "角落挂件, 别挡桌面"),
    ("中", "0.85", "默认"),
    ("大", "1.00", "条目看得更清楚"),
)

# CellColors 里能覆盖的字段名 — 配色表之外的键 (比如 chip_bg) 不往里传
_PALETTE_KEYS = {f.name for f in fields(CellColors)}

# 亮壁纸 → 深色字 + 白描边
DARK_SCHEME = {
    "num_fg": "#1A1A1A",
    "text_fg": "#1A1A1A",
    "muted_fg": "rgba(40, 40, 40, 150)",
    "done_fg": "rgba(70, 70, 70, 190)",
    "sub_fg": "rgba(35, 35, 35, 200)",
    "header_fg": "rgba(20, 20, 20, 225)",
    "border": "rgba(0, 0, 0, 55)",
    "today_border": "rgba(214, 138, 0, 230)",
    "today_bg": "rgba(255, 214, 90, 110)",
    "hover_bg": "rgba(255, 255, 255, 110)",
    "halo": "rgba(255, 255, 255, 205)",
    "chip_bg": "rgba(255, 255, 255, 150)",
}

# 暗壁纸 → 浅色字 + 黑描边
LIGHT_SCHEME = {
    "num_fg": "#FFFFFF",
    "text_fg": "#FFFFFF",
    "muted_fg": "rgba(255, 255, 255, 140)",
    "done_fg": "rgba(255, 255, 255, 150)",
    "sub_fg": "rgba(255, 255, 255, 200)",
    "header_fg": "rgba(255, 255, 255, 235)",
    "border": "rgba(255, 255, 255, 70)",
    "today_border": "rgba(255, 205, 60, 230)",
    "today_bg": "rgba(255, 205, 60, 60)",
    "hover_bg": "rgba(255, 255, 255, 70)",
    "halo": "rgba(0, 0, 0, 175)",
    "chip_bg": "rgba(0, 0, 0, 70)",
}


def _widget_fonts(scale: float):
    """挂件专用的字号表。

    主窗的「一格 5 条」是按大格子算的 —— 挂件的格子只有 50 来个像素高,
    照抄 5 条的话字直接溢出格子外面去。所以这里压到 2 条, 再把格子的
    最小高度按「2 行 + 「+N 更多」」重新算出来。
    """
    f = fonts_for(scale)
    need = 14 + f.row_h * WIDGET_MAX_ROWS + 12
    return replace(
        f,
        max_rows=WIDGET_MAX_ROWS,
        cell_min_h=max(f.cell_min_h, need),
    )


class DesktopWidget(QWidget):
    """贴在桌面层的小月历。"""

    sig_request_main = Signal()      # 「打开主窗口」
    sig_quit_app = Signal()          # 「退出 PyCal」
    sig_hide_requested = Signal()    # 「隐藏挂件」

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._scale = scale_from_setting(
            get_setting("widget_scale", str(DEFAULT_WIDGET_SCALE)),
            DEFAULT_WIDGET_SCALE,
        )
        self._fonts = _widget_fonts(self._scale)
        self._scheme_mode = get_setting("widget_scheme", "auto")
        self._topmost = get_setting("widget_topmost", "0") == "1"
        self._today = date.today()
        self._drag_pos: Optional[QPoint] = None
        self._scheme: dict = dict(LIGHT_SCHEME)

        self.setWindowTitle("PyCal 桌面挂件")
        self._build()
        self._apply_flags()
        self._restore_geometry()
        self._apply_scheme()

        # 每分钟查一次 —— 跨零点要自动翻到今天
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(60_000)

    # ------------------------------------------------------------------
    # 搭界面
    # ------------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 4)
        lay.setSpacing(1)

        head = QWidget()
        head.setCursor(Qt.CursorShape.SizeAllCursor)
        # 标题栏右键 → 挂件菜单 (格子的右键是它自己的菜单, 两边不打架)
        head.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        head.customContextMenuRequested.connect(self._show_menu)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(2, 0, 2, 0)
        hl.setSpacing(2)

        self._title = HaloLabel("")
        tf = QFont()
        tf.setPointSize(self._fonts.month - 2)
        tf.setBold(True)
        self._title.setFont(tf)
        hl.addWidget(self._title)
        hl.addStretch()

        self._btn_today = self._small_btn("今天", "回到本月")
        self._btn_pin = self._small_btn("📌", "切换层级: 贴在桌面层 / 永远置顶")
        self._btn_menu = self._small_btn("⋯", "挂件设置")
        self._btn_hide = self._small_btn("✕", "隐藏挂件")
        for b in (self._btn_today, self._btn_pin, self._btn_menu, self._btn_hide):
            hl.addWidget(b)

        self._btn_today.clicked.connect(self._goto_today)
        self._btn_pin.clicked.connect(self._toggle_layer)
        self._btn_menu.clicked.connect(self._show_menu)
        self._btn_hide.clicked.connect(lambda: self.sig_hide_requested.emit())
        self._btn_pin.setToolTip(
            "现在浮在最上面 — 点一下回到桌面层"
            if self._topmost else
            "现在贴在桌面层 — 点一下浮到最上面"
        )
        lay.addWidget(head)
        self._head = head

        # 月视图 — 透明皮肤, 壁纸透出来
        self._month = MonthView(skin_name="desktop_widget", fonts=self._fonts)
        lay.addWidget(self._month, 1)

        self._update_title()

    def _small_btn(self, text: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    # ------------------------------------------------------------------
    # 层级 / 外观
    # ------------------------------------------------------------------
    def _apply_flags(self) -> None:
        """重建窗口标志 — 桌面层 / 置顶 两档。"""
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        if self._topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags |= Qt.WindowType.WindowStaysOnBottomHint
        was = self.isVisible()
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if was:
            self.show()

    def _set_layer(self, value: str) -> None:
        self._topmost = (value == "top")
        set_setting("widget_topmost", "1" if self._topmost else "0")
        self._apply_flags()
        self._btn_pin.setToolTip(
            "现在浮在最上面 — 点一下回到桌面层"
            if self._topmost else
            "现在贴在桌面层 — 点一下浮到最上面"
        )

    def _toggle_layer(self) -> None:
        self._set_layer("bottom" if self._topmost else "top")

    def _set_scheme(self, value: str) -> None:
        self._scheme_mode = value
        set_setting("widget_scheme", value)
        self._apply_scheme()

    def _apply_scheme(self) -> None:
        """按当前配色方案刷一遍月视图和标题。"""
        scheme = self._pick_scheme()
        self._scheme = scheme
        self._month.apply_palette_override(
            **{k: v for k, v in scheme.items() if k in _PALETTE_KEYS}
        )
        fg = scheme["num_fg"]
        self._title.setInk(fg)
        self._title.setHalo(scheme["halo"])
        qss = (
            "QToolButton { border: none;"
            f" background: {scheme['chip_bg']};"
            f" color: {fg}; border-radius: 3px; padding: 0px 5px; }}"
            "QToolButton:hover { background: rgba(128, 128, 128, 140); }"
        )
        for b in (self._btn_today, self._btn_pin, self._btn_menu, self._btn_hide):
            b.setStyleSheet(qss)

    def _pick_scheme(self) -> dict:
        """深色字还是浅色字 —— 自动模式下看挂件底下那块壁纸多亮。"""
        if self._scheme_mode == "dark":
            return dict(DARK_SCHEME)
        if self._scheme_mode == "light":
            return dict(LIGHT_SCHEME)
        return dict(DARK_SCHEME if self._wallpaper_brightness() > 0.55
                    else LIGHT_SCHEME)

    def _wallpaper_brightness(self) -> float:
        try:
            from pycal.desktop.wallpaper import sample_brightness
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen is None:
                return 0.5
            return sample_brightness(screen, self.geometry())
        except Exception:
            return 0.5

    def _set_scale(self, value: str) -> None:
        self._scale = scale_from_setting(value, DEFAULT_WIDGET_SCALE)
        set_setting("widget_scale", str(self._scale))
        self._fonts = _widget_fonts(self._scale)
        self._month.apply_fonts(self._fonts)
        tf = QFont(self._title.font())
        tf.setPointSize(self._fonts.month - 2)
        tf.setBold(True)
        self._title.setFont(tf)
        self._fit_size()

    # ------------------------------------------------------------------
    # 位置 / 拖动
    # ------------------------------------------------------------------
    def _restore_geometry(self) -> None:
        x = get_setting("widget_x", "")
        y = get_setting("widget_y", "")
        if x and y:
            try:
                self.move(int(x), int(y))
                self._fit_size()
                return
            except ValueError:
                pass
        self._move_to_corner()

    def _move_to_corner(self) -> None:
        """第一次打开 → 贴在主屏右下角。"""
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        if geo is None:
            self._fit_size()
            return
        self._fit_size()
        margin = 24
        self.move(geo.right() - self.width() - margin + 1,
                  geo.bottom() - self.height() - margin + 1)

    def _fit_size(self) -> None:
        """给窗口定尺寸 —— 格子里的内容有多高, 窗口就得有多高。

        光靠布局的 sizeHint 会差几像素: 格子的「最小高度」是按字号估的,
        实际内容 (日期号 + 两条目 + 「+N 更多」) 可能更高, 差出来的那截
        就是最后一行字被切掉。所以这里直接按内容算 —— 逐格量, 取最高的。
        """
        mv = self._month
        need = 0
        for cell in mv._cells.values():
            if cell.isHidden():
                continue
            h = cell._num_lbl.sizeHint().height() + 1        # 日期号那行
            for row in cell._rows:
                if not row.isHidden():
                    h += row.height()
            if not cell._more_lbl.isHidden():
                h += cell._more_lbl.sizeHint().height() + 1  # 「+N 更多」
            h += 8                                        # 边距 + 行间距
            need = max(need, h, cell.minimumSize().height())
        rows = max(1, mv._weeks)
        head_h = self._head.sizeHint().height()
        h = head_h + 1 + 15 + rows * (need + 2) + 10
        w = max(self.layout().sizeHint().width(), 320)
        self.resize(w, h)

    def _save_geometry(self) -> None:
        g = self.geometry()
        set_setting("widget_x", str(g.x()))
        set_setting("widget_y", str(g.y()))

    def mousePressEvent(self, ev):
        # 只有标题栏能拖 —— 格子上那点留给勾选和编辑
        if ev.button() == Qt.MouseButton.LeftButton and \
                self._head.geometry().contains(ev.position().toPoint()):
            self._drag_pos = (ev.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())
            try:
                self.grabMouse()
            except Exception:
                pass
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._drag_pos is not None and \
                (ev.buttons() & Qt.MouseButton.LeftButton):
            self.move(ev.globalPosition().toPoint() - self._drag_pos)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._drag_pos is not None:
            self._drag_pos = None
            try:
                self.releaseMouse()
            except Exception:
                pass
            self._save_geometry()
            # 挪到新地方了, 底下的壁纸亮度可能变了 — 字色跟着重算
            self._apply_scheme()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    # ------------------------------------------------------------------
    # 菜单
    # ------------------------------------------------------------------
    def _show_menu(self) -> None:
        self._build_menu().exec(self.cursor().pos())

    def _build_menu(self) -> QMenu:
        m = QMenu(self)
        m.setToolTipsVisible(True)

        act = m.addAction("打开 PyCal 主窗口")
        act.setToolTip("显示主窗口 —— 完整功能都在那边")
        act.triggered.connect(lambda: self.sig_request_main.emit())

        act = m.addAction("回到今天")
        act.setToolTip("把月历翻回本月")
        act.triggered.connect(self._goto_today)

        m.addSeparator()
        head = m.addAction("层级")
        head.setEnabled(False)
        for label, value, tip in [
            ("贴在桌面层 (默认)", "bottom",
             "别的窗口能盖住它 —— 它不会跳出来抢你正在写的文档"),
            ("永远置顶", "top", "一直浮在最上面, 什么时候都看得见"),
        ]:
            a = m.addAction(label)
            a.setToolTip(tip)
            a.setCheckable(True)
            a.setChecked(self._topmost == (value == "top"))
            a.triggered.connect(lambda _=False, v=value: self._set_layer(v))

        head = m.addAction("文字颜色")
        head.setEnabled(False)
        for label, value, tip in [
            ("自动 (跟壁纸亮度反色)", "auto",
             "亮壁纸用深字, 暗壁纸用浅字"),
            ("深色字", "dark", "浅色壁纸上更清楚"),
            ("浅色字", "light", "深色壁纸上更清楚"),
        ]:
            a = m.addAction(label)
            a.setToolTip(tip)
            a.setCheckable(True)
            a.setChecked(self._scheme_mode == value)
            a.triggered.connect(lambda _=False, v=value: self._set_scheme(v))

        head = m.addAction("大小")
        head.setEnabled(False)
        for label, value, tip in SIZE_PRESETS:
            a = m.addAction(label)
            a.setToolTip(tip)
            a.setCheckable(True)
            a.setChecked(abs(self._scale - float(value)) < 0.01)
            a.triggered.connect(lambda _=False, v=value: self._set_scale(v))

        m.addSeparator()
        a = m.addAction("隐藏挂件")
        a.setToolTip("收掉挂件 —— 主窗口「⋯ 更多」里能再打开")
        a.triggered.connect(lambda: self.sig_hide_requested.emit())
        a = m.addAction("退出 PyCal")
        a.setToolTip("结束整个程序")
        a.triggered.connect(lambda: self.sig_quit_app.emit())
        return m

    # ------------------------------------------------------------------
    # 日常
    # ------------------------------------------------------------------
    def _goto_today(self) -> None:
        self._month.goto_today()
        self._update_title()
        self._fit_size()

    def _update_title(self) -> None:
        y, m = self._month.current_month()
        self._title.setText(f"{y}年{m}月")
        today = date.today()
        try:
            from pycal.lunar import lunar_for
            lunar = lunar_for(today) or ""
        except Exception:
            lunar = ""
        wd = "一二三四五六日"[today.weekday()]
        self.setToolTip(
            f"PyCal 桌面挂件\n今天 {today:%Y-%m-%d} 星期{wd} {lunar}\n"
            "拖标题栏可以挪位置, 右键有更多设置"
        )

    def _tick(self) -> None:
        """每分钟跑一次 —— 跨零点翻页 + 顺手刷数据。"""
        now = date.today()
        if now != self._today:
            self._today = now
            self._month.goto_today()
            self._update_title()
            self._fit_size()
        self._month.refresh()
        self._apply_scheme()

    def showEvent(self, ev):
        super().showEvent(ev)
        # 刚露出来才算一次亮度 —— 之前位置还没定, 采样没意义
        QTimer.singleShot(0, self._apply_scheme)


class DesktopWidgetManager:
    """挂件的生命周期 —— 主窗菜单和托盘菜单都通过它开关。"""

    def __init__(self, main_window=None):
        self._main = main_window
        self._widget: Optional[DesktopWidget] = None

    @property
    def widget(self) -> Optional[DesktopWidget]:
        return self._widget

    def visible(self) -> bool:
        return self._widget is not None and self._widget.isVisible()

    def toggle(self) -> None:
        if self.visible():
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        if self._widget is None:
            self._widget = DesktopWidget()
            self._widget.sig_request_main.connect(self._open_main)
            self._widget.sig_quit_app.connect(self._quit_app)
            self._widget.sig_hide_requested.connect(self.hide)
        self._widget.show()
        set_setting("desktop_widget", "1")

    def hide(self) -> None:
        if self._widget is not None:
            self._widget.hide()
        set_setting("desktop_widget", "0")

    # ------------------------------------------------------------------
    def _open_main(self) -> None:
        if self._main is None:
            return
        self._main.showNormal()
        self._main.raise_()
        self._main.activateWindow()

    def _quit_app(self) -> None:
        if self._main is not None and hasattr(self._main, "_quit_app"):
            self._main._quit_app()
            return
        app = QApplication.instance()
        if app is not None:
            app.quit()


__all__ = ["DesktopWidget", "DesktopWidgetManager", "DEFAULT_WIDGET_SCALE"]
