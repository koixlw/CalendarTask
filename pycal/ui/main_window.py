"""PyCal 主窗口 — Frameless + 可拖动 + 可 resize + 皮肤 + 月份切换动画。

Phase 3 增强:
  - 主窗可拖动 (工具栏区)
  - 八向边缘 resize
  - 双击标题栏最大化 / 还原
  - 4 种皮肤 (light_default / warm_paper / dark_mode / glass_blur)
  - 不透明度调节
  - 字体缩放 (设置在 settings 表里)
  - 月份切换 slide 动画 (450ms)
  - 「置顶 / 隐藏到托盘」 开关
"""

from __future__ import annotations
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    Qt, QPoint, Signal, QSize, QPropertyAnimation, QEasingCurve,
    QParallelAnimationGroup, QAbstractAnimation, QRect, QTimer,
    QThread, Slot, QObject,
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QPixmap, QPainter, QShortcut, QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QLabel,
    QVBoxLayout, QHBoxLayout, QPushButton, QFrame, QStatusBar,
    QMenu, QMessageBox, QToolButton, QSystemTrayIcon, QSlider,
    QButtonGroup, QScrollArea, QSizePolicy,
)

from pycal.db.connection import open_default
from pycal.db.models import CalendarItem, RepeatRule, Reminder, Countdown
from pycal.db.repository import upsert_item, list_items_by_date, get_item
from pycal.ui.month_view import MonthView
from pycal.ui.year_view import YearView
from pycal.ui.skin import Skin, current_skin, stylesheet_for, SKINS
from pycal.ui.fonts import (
    SCALES, DEFAULT_SCALE, Fonts, fonts_for, label_for_scale,
    scale_from_setting,
)
from pycal.ui.floating_window import FloatingWindow


def _save_item(
    item: CalendarItem,
    repeat_rule: Optional[RepeatRule] = None,
    reminder: Optional[Reminder] = None,
    countdown: Optional[Countdown] = None,
) -> None:
    """统一的保存入口。"""
    con = open_default()
    upsert_item(con, item)
    if item.type == 1 and repeat_rule:
        c = open_default()
        c.execute("DELETE FROM repeat_rules WHERE item_id = ?", (item.item_id,))
        c.execute(
            """INSERT INTO repeat_rules(item_id, freq, interval_n, byweekday, until_date)
               VALUES (?,?,?,?,?)""",
            (
                item.item_id, repeat_rule.freq, repeat_rule.interval_n,
                repeat_rule.byweekday, repeat_rule.until_date,
            ),
        )
    elif item.type == 2 and reminder:
        c = open_default()
        c.execute("DELETE FROM reminders WHERE item_id = ?", (item.item_id,))
        from datetime import datetime
        c.execute(
            """INSERT INTO reminders(item_id, remind_at, channel)
               VALUES (?,?,?)""",
            (
                item.item_id,
                reminder.remind_at.isoformat()
                if isinstance(reminder.remind_at, datetime)
                else str(reminder.remind_at),
                reminder.channel,
            ),
        )
    elif item.type == 3 and countdown:
        c = open_default()
        c.execute("DELETE FROM countdowns WHERE item_id = ?", (item.item_id,))
        c.execute(
            """INSERT INTO countdowns(item_id, target_date, repeat_yearly)
               VALUES (?,?,?)""",
            (item.item_id, countdown.target_date, countdown.repeat_yearly),
        )


def run(fullscreen: bool = False) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    win = PyCalMainWindow(app, fullscreen=fullscreen)

    # 系统托盘 + 提醒 worker
    tray = _build_tray(win, app)
    worker = _build_reminder_worker(tray)
    worker.fired.connect(
        lambda item_id, title: _on_reminder_fired(win, item_id, title)
    )
    win._tray = tray
    win._worker = worker

    win.show()

    return app.exec()


# ============================================================================
# 主窗口
# ============================================================================
class PyCalMainWindow(FloatingWindow):
    """PyCal 主窗 — 继承 FloatingWindow (可拖动 + resize)。"""

    # 窗口窄于这个宽度 → 工具栏按钮切成纯图标
    _COMPACT_UNDER = 1460

    # 「收成窗口」时的默认大小 —— 启动弹出来的窗口就用这个尺寸。
    # 不铺满整屏: 一开就占满一整块屏幕太霸道, 挡着别的窗口用。
    COLLAPSED_W = 1100
    COLLAPSED_H = 760

    def __init__(self, app: QApplication, fullscreen: bool = False):
        super().__init__()
        self._app = app
        self._skin: Skin = current_skin(
            _load_setting("skin", "light_default")
        )
        self._topmost: bool = _load_setting("topmost", "0") == "1"
        self._opacity: float = float(_load_setting("opacity", "1.0") or "1.0")
        # 字号比例存在 settings 表 — 重启后还是用户调的那个大小
        self._font_scale: float = scale_from_setting(
            _load_setting("font_scale", str(DEFAULT_SCALE))
        )
        self._fonts: Fonts = fonts_for(self._font_scale)
        # 当前显示的是月视图还是年视图 — 年视图下面还带一块月视图详情
        self._view_mode = "month"
        # 年视图 / 动画用的第二个月视图 — 都按需构建 (见 _year_view / _ensure_idle_month_view)
        self._year_view_obj: Optional[YearView] = None
        self._idle_month_view: Optional[MonthView] = None
        # 对话框 (编辑器 / 搜索 / 帮助) 用的都是系统默认字号, 这里记下基准值,
        # 调字号时好按比例一起放大 — 不然大窗配小字会很割裂。
        # 基准只取一次: 每次都拿当前值再乘会把字号滚雪球。
        self._base_app_pt = app.font().pointSize() if app.font().pointSize() > 0 else 0

        self.setWindowTitle("PyCal")
        # 铺满屏幕 / 普通窗口 两种状态 — 默认启动就是「收成窗口」那个尺寸,
        # 想当整面桌面的日历用再按 F11 / 点「□ 铺满屏幕」。
        self._full = fullscreen
        self._normal_rect: Optional[QRect] = None
        if fullscreen:
            self._intended_rect = app.primaryScreen().geometry()
        else:
            # 上次关窗时窗口在哪儿、拉多大 —— 照原样摆回来。
            # 第一次用 (还没存过) 就用居中的默认尺寸。
            saved = self._load_normal_rect()
            self._intended_rect = (
                saved if saved is not None else self._collapsed_rect())
        self.setGeometry(self._intended_rect)

        # Frameless + transparent (FramelessHint 已由 FloatingWindow 处理)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnBottomHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 快捷待办面板 — 点托盘图标弹出来的小窗口。
        # 得在 _build_ui 之前建 —— 工具栏菜单里要挂它的开关项。
        from pycal.ui.quick_panel import QuickPanelManager
        self._quick_mgr = QuickPanelManager(self)

        # 应用皮肤 + 不透明度
        self._apply_skin()

        # 构造 UI
        self._build_ui()

        # 再摆回原位一次 —— 布局刚建好时, 带文字的工具栏会把窗口撑到它的
        # 最小宽度 (1448), 摆回原位就恢复正常大小了。
        self.setGeometry(self._intended_rect)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # 中心 widget (QFrame 才能拿到 objectName, 用作 QSS 选择器)
        central = QFrame()
        central.setObjectName("Central")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.setCentralWidget(central)
        self._central = central
        self._top_layout = layout

        # 工具栏
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(self._fonts.bar_h)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(4, 0, 4, 0)
        bar_layout.setSpacing(4)

        title = QLabel("📅 PyCal")
        title.setProperty("role", "title")
        self._title_lbl = title
        bar_layout.addWidget(title)
        bar_layout.addSpacing(20)

        self._btn_prev = _tool_btn("◀", self._skin)
        self._month_label = QLabel()
        self._month_label.setProperty("role", "month_label")
        self._month_label.setMinimumWidth(140)
        self._month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._btn_next = _tool_btn("▶", self._skin)
        self._btn_today = _tool_btn("今天", self._skin)
        # 年/月视图切换 — 年视图是一屏 12 个小月, 下面是选中月的详情
        self._btn_year = _tool_btn("📆 年视图", self._skin)
        self._btn_year.setCheckable(True)
        for b in (self._btn_prev, self._month_label, self._btn_next,
                  self._btn_today, self._btn_year):
            bar_layout.addWidget(b)
        bar_layout.addSpacing(8)

        bar_layout.addStretch()
        # ── 右区 ──
        # 常用功能直接写中文名; 低频功能收进「⋯ 更多」菜单 (菜单项也带文字 +
        # 一句话说明)。之前 12 个纯 emoji 图标谁也认不出来。
        self._btn_report = _tool_btn("📑 日报", self._skin, accent=True)
        self._btn_ai = _tool_btn("✨ AI 日报", self._skin, accent=True)
        self._btn_search = _tool_btn("🔍 搜索", self._skin)
        self._btn_more = _tool_btn("⋯ 更多", self._skin)
        self._btn_help = _tool_btn("？ 帮助", self._skin)
        bar_layout.addSpacing(10)
        # 窗口控制 — 文案写清楚点了会发生什么, 而不是光一个「—」
        self._btn_min = _tool_btn("— 收起", self._skin)
        self._btn_max = _tool_btn("□ 最大化", self._skin)
        self._btn_exit = _tool_btn("× 退出", self._skin, danger=True)

        # (按钮, 是否 accent, 是否 danger) — 换皮肤时照这张表重刷
        self._tool_meta = {
            self._btn_prev: (False, False), self._btn_next: (False, False),
            self._btn_today: (False, False), self._btn_year: (False, False),
            self._btn_report: (True, False), self._btn_ai: (True, False),
            self._btn_search: (False, False), self._btn_more: (False, False),
            self._btn_help: (False, False),
            self._btn_min: (False, False), self._btn_max: (False, False),
            self._btn_exit: (False, True),
        }
        # (宽时的字, 窄时的字) — 窗口一小, 按钮就只留图标,
        # 不然光工具栏就要 1400px, 窗口根本缩不下去
        self._tool_labels = {
            self._btn_prev: ("◀", "◀"),
            self._btn_next: ("▶", "▶"),
            self._btn_today: ("今天", "今"),
            self._btn_year: ("📆 年视图", "📆"),
            self._btn_report: ("📑 日报", "📑"),
            self._btn_ai: ("✨ AI 日报", "✨"),
            self._btn_search: ("🔍 搜索", "🔍"),
            self._btn_more: ("⋯ 更多", "⋯"),
            self._btn_help: ("？ 帮助", "？"),
            self._btn_min: ("— 收起", "—"),
            self._btn_max: ("□ 最大化", "□"),
            self._btn_exit: ("× 退出", "×"),
        }
        self._compact = False
        # ⚠ 只把「右区」这些加进布局。_tool_meta 里还包含 prev/next/today,
        #   它们上面已经 addWidget 过了 —— 对已入布局的 widget 再 addWidget,
        #   Qt 会把它**移走**重新插到末尾, 月份导航会莫名其妙跑到工具栏右边。
        for b in (self._btn_report, self._btn_ai, self._btn_search,
                  self._btn_more, self._btn_help,
                  self._btn_min, self._btn_max, self._btn_exit):
            bar_layout.addWidget(b)

        # 「⋯ 更多」的下拉菜单
        self._btn_more.setMenu(self._build_more_menu())
        self._btn_more.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)

        self._top_layout.addWidget(bar)
        # 把整个工具栏作为拖动区域
        self._top_bar = bar
        self.set_drag_area(bar, height=self._fonts.bar_h)

        # 月视图 (放在一个 QStackedWidget 里方便做切换动画)
        from PySide6.QtWidgets import QStackedWidget
        self._stack = QStackedWidget()
        self._month_left = MonthView(skin_name=self._skin.name,
                                      font_scale=self._font_scale)
        self._stack.addWidget(self._month_left)
        self._stack.setCurrentIndex(0)
        # 当前月份初始设定相同
        self._active_month_view = self._month_left
        # 第二层 (动画的 slide-in 目标) 第一次翻月时才建 —— 见
        # _ensure_idle_month_view。白建一遍要多花 ~20ms 启动时间。
        # 勾选/增删后刷新状态栏计数
        self._active_month_view.sig_data_changed.connect(self._update_count)

        # 年视图 — 另一层: 上面 12 个小月 (一~日), 下面是选中月的详情。
        # 按需构建 —— 见 _year_view 属性。
        # 套一层滚动区 — 屏太矮时 (1366×768) 年视图放不下, 至少能滚,
        # 不会被布局硬撑出屏幕外
        self._year_scroll = QScrollArea()
        self._year_scroll.setWidgetResizable(True)
        self._year_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._year_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 外层栈: 0 = 月视图, 1 = 年视图
        self._view_stack = QStackedWidget()
        self._view_stack.addWidget(self._stack)
        self._view_stack.addWidget(self._year_scroll)
        self._view_stack.setCurrentIndex(0)
        self._top_layout.addWidget(self._view_stack, 1)

        # 状态栏
        status = QStatusBar()
        self._count_label = QLabel("0 项")
        self._opacity_label = QLabel("100%")
        self._anim_label = QLabel("动画 开")
        self._hint_label = QLabel(
            "单击 ☐ 打勾 · 双击文字编辑 · 右键更多 · "
            "拖四边自由缩放 · F11 铺满屏幕")
        # 提示文字不参与算窗口的最小宽度 —— 否则这行字越长, 窗口就缩不窄
        self._hint_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status.addWidget(self._hint_label, 1)
        status.addPermanentWidget(self._count_label)
        status.addPermanentWidget(self._anim_label)
        status.addPermanentWidget(self._opacity_label)
        self.setStatusBar(status)
        self._status_bar = status

        # 信号
        self._btn_prev.clicked.connect(lambda: self.shift_month(-1))
        self._btn_next.clicked.connect(lambda: self.shift_month(+1))
        self._btn_today.clicked.connect(self.goto_today)
        self._btn_year.clicked.connect(self._toggle_year_view)
        self._btn_report.clicked.connect(self._open_report)
        self._btn_ai.clicked.connect(self._open_ai)
        self._btn_search.clicked.connect(self._open_search)
        self._btn_help.clicked.connect(self._open_help)
        # 注意 clicked 会带一个 bool 参数, 直接 connect(self._collapse) 会让它
        # 当成 announce 收到 False。这里显式写清楚: 主动点「收起」就安静收,
        # 只有 Alt+F4 这种"意图不明"的关窗才弹气泡。
        self._btn_min.clicked.connect(lambda: self._collapse(announce=False))
        self._btn_max.clicked.connect(self.toggle_maximized)
        self._btn_exit.clicked.connect(self._quit_app)

        # 初始化
        self._update_month_label()
        self._update_count()
        self._apply_topmost()
        self._apply_opacity()
        # 工具栏按钮建的时候用的是默认字号, 这里按存的缩放比例补刷一遍
        self._apply_fonts()
        # 动画开关的初始状态 — 状态栏文案也要跟着设置走, 别写死「开」
        anim_on = _load_setting("anim", "1") == "1"
        self._anim_label.setText(f"动画 {'开' if anim_on else '关'}")

        # 提示 — 每个按钮都有, 别再靠猜
        for b, tip in [
            (self._btn_prev, "上一月"),
            (self._btn_next, "下一月"),
            (self._btn_today, "回到今天"),
            (self._btn_year, "切换到年视图 / 回到月视图\n"
                             "年视图: 一屏 12 个月的小格子 (周一~周日), "
                             "下面是选中月的详情"),
            (self._btn_report, "生成日报 / 周报\n预览、复制、导出 MD / HTML / PDF"),
            (self._btn_ai, "用 Claude 把日报 / 周报改写得更通顺\n"
                            "(没配 key? 点「⋯ 更多 → 🔑 AI 润色 API key」)"),
            (self._btn_search, "搜索全部条目\n快捷键 Ctrl+F"),
            (self._btn_more, "全部功能菜单\n云同步 / 便签 / 导入 / 外观 / 壁纸 …"),
            (self._btn_help, "快捷键与功能说明\n快捷键 F1"),
            (self._btn_min, "收起窗口到托盘\n程序继续在后台运行, 提醒照常弹出\n(想彻底退出请点「× 退出」)"),
            (self._btn_max, "铺满屏幕 / 收成窗口\n"
                            "收成窗口之后拖四边和四角就能自由缩放\n"
                            "铺满模式下不能拖边缩放 —— 那时拖的是屏幕边缘\n"
                            "快捷键 F11, 也可双击工具栏空白处"),
            (self._btn_exit, "彻底退出 PyCal\n后台提醒和自动同步都会停止"),
        ]:
            b.setToolTip(tip)

        # 快捷键
        for key, slot in [
            ("Ctrl+R", self._open_report),
            ("Ctrl+Shift+R", self._open_ai),
            ("Ctrl+F", self._open_search),
            ("Ctrl+T", self.goto_today),
            ("Ctrl+N", self._new_item_today),
            ("F5", self.refresh_view),
            ("F1", self._open_help),
            ("Ctrl+S", self._open_sync),
            ("Y", self._toggle_year_view_from_menu),
            ("Ctrl+,", self._open_settings),
            ("F11", self.toggle_maximized),
        ]:
            QShortcut(QKeySequence(key), self, activated=slot)

        # 云同步: 后台定时任务 (每 15 分钟, 仅当有账号)
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._auto_sync_tick)
        self._sync_timer.start(15 * 60 * 1000)
        QTimer.singleShot(10_000, self._auto_sync_tick)   # 启动 10s 后首跑

        # 报告自动生成: 每 30 分钟检查一次是否到点
        self._report_timer = QTimer(self)
        self._report_timer.timeout.connect(self._auto_report_tick)
        self._report_timer.start(30 * 60 * 1000)
        QTimer.singleShot(60_000, self._auto_report_tick)   # 启动 1min 后首检

        # 窄屏 (比如 1366 的本) 一开始就该是紧凑工具栏 — 等第一次 resize
        # 才切的话, 窗口会先被撑到 1448 宽, 超出屏幕。
        # 这里按「本来想要的宽度」判断, 不是按当前宽度 —— 布局刚建好时
        # 窗口已经被撑到工具栏的最小宽度了, 那时再判断就永远判不出来。
        self._apply_toolbar_compact(self._intended_rect.width())

    # ------------------------------------------------------------------
    # 月 / 年视图切换
    # ------------------------------------------------------------------
    @property
    def _year_view(self) -> YearView:
        """年视图 — 第一次用到时才建。

        年视图自带 12 个小月 + 一整块月视图 (详情), 光 widget 就 1800 多个,
        启动时白建一遍要 ~70ms + 一大堆内存。真正常用的人其实很少切过去,
        所以按需构建 —— 用到之前不花这个钱。
        """
        yv = self._year_view_obj
        if yv is None:
            yv = YearView(skin_name=self._skin.name,
                          font_scale=self._font_scale)
            yv.sig_data_changed.connect(self._update_count)
            yv.sig_month_changed.connect(self._on_year_month_changed)
            self._year_scroll.setWidget(yv)
            self._year_view_obj = yv
        return yv

    def _ensure_idle_month_view(self) -> MonthView:
        """翻月动画要用的那块月视图 — 第一次翻月时才建。

        它跟当前那块长得一模一样, 启动时先建一份纯属白花 ~20ms。
        """
        mv = self._idle_month_view
        if mv is None:
            mv = MonthView(skin_name=self._skin.name,
                           font_scale=self._font_scale)
            mv.sig_data_changed.connect(self._update_count)
            self._stack.addWidget(mv)
            self._idle_month_view = mv
        return mv

    def _month_views(self) -> list[MonthView]:
        """当前存在的月视图 (动画那块可能还没建)。"""
        out = []
        for mv in (getattr(self, "_month_left", None), self._idle_month_view):
            if mv is not None:
                out.append(mv)
        return out

    def _is_year_view(self) -> bool:
        return getattr(self, "_view_mode", "month") == "year"

    def _current_view(self) -> MonthView:
        """当前「说了算」的那块月视图 — 年视图模式下是下方详情。"""
        if self._is_year_view():
            return self._year_view.detail_view
        return self._active_month_view

    def _toggle_year_view(self, checked: bool = True) -> None:
        """工具栏那个勾选按钮 — checked 是按钮自己的勾选状态。"""
        self._set_view_mode("year" if checked else "month")

    def _toggle_year_view_from_menu(self) -> None:
        """「⋯ 更多」里那项 — 菜单没有勾选态, 就直接翻面。"""
        self._set_view_mode("month" if self._is_year_view() else "year")

    def _set_view_mode(self, mode: str) -> None:
        if mode == self._view_mode:
            return
        self._view_mode = mode
        if mode == "year":
            y, m = self._active_month_view.current_month()
            self._year_view.goto(y, m)
            self._view_stack.setCurrentIndex(1)
        else:
            # 回到月视图 — 停在哪个月就停在哪个月
            y, m = self._year_view.current_month()
            self._active_month_view.goto(y, m)
            if self._idle_month_view is not None:
                self._idle_month_view.goto(y, m)
            self._view_stack.setCurrentIndex(0)
        self._sync_year_btn()
        self._update_month_label()
        self._update_count()

    def _sync_year_btn(self) -> None:
        """年视图按钮的勾选态 + 高亮 — 换皮肤后要重新 polish 才生效。"""
        btn = getattr(self, "_btn_year", None)
        if btn is None:
            return
        on = self._is_year_view()
        btn.setChecked(on)
        btn.setProperty("on", "true" if on else "false")
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _apply_toolbar_compact(self, width: Optional[int] = None) -> None:
        """窗口窄了 → 工具栏按钮只留图标。

        12 个带字的按钮最少要 1400px, 不切的话窗口的下限就被工具栏钉死,
        「自由缩放」就成了空话。tooltip 里始终写着完整名字, 认不出来可以
        把鼠标停上去看。

        width 是「打算摆多宽」—— 不能一律用 self.width(): 布局的最小宽度
        会先把窗口撑到 1448, 那时再看宽度就永远看不出「该切紧凑了」。
        """
        w = self.width() if width is None else width
        compact = w < self._COMPACT_UNDER
        if compact != self._compact:
            self._compact = compact
            for btn, (full, short) in self._tool_labels.items():
                btn.setText(short if compact else full)
                if compact:
                    btn.setFixedWidth(32)
                else:
                    btn.setMinimumWidth(0)
                    btn.setMaximumWidth(16777215)
            self._title_lbl.setText("📅" if compact else "📅 PyCal")
            self._month_label.setMinimumWidth(100 if compact else 140)
            self._restyle_tools()
            self._style_month_label()
            # 布局的最小宽度跟着变了 (带文字的工具栏要 1100 多 → 纯图标
            # 只要 600 出头), 但窗口上「显式设过的最小宽度」不会跟着降。
            # 不清掉的话, 窗口一旦进过宽模式就再也缩不回去了 ——
            # 明明工具栏已经是图标了, 还是卡在 1100 多收不动。
            self.setMinimumSize(0, 0)
        self._update_max_btn()
        self._update_month_label()

    def _apply_resize_geometry(self, rect: QRect) -> None:
        """拖边框缩放时摆窗口 —— 先按目标宽度决定工具栏要不要切紧凑。

        顺序很要紧: 不先切, 布局算出来的最小宽度 (带文字的工具栏要 1448)
        会把窗口按在那儿 —— 往里拖边框根本收不动, 窗口永远卡在 1448 宽。
        """
        self._apply_toolbar_compact(rect.width())
        super()._apply_resize_geometry(rect)

    def _update_max_btn(self) -> None:
        """「铺满 / 收成」按钮的文案跟着当前状态走。"""
        _full, short = self._tool_labels[self._btn_max]
        if self._compact:
            self._btn_max.setText(short)
        else:
            self._btn_max.setText("□ 收成窗口" if self._full else "□ 铺满屏幕")

    def _style_month_label(self) -> None:
        """月份标签的字号 — 紧凑模式下小一号, 否则放不下。"""
        if getattr(self, "_compact", False):
            self._month_label.setStyleSheet(
                f"font-size: {max(9, self._fonts.month - 3)}pt;"
                f" font-weight: bold;"
            )
        else:
            self._month_label.setStyleSheet("")

    def _restyle_tools(self) -> None:
        """工具栏按钮重刷样式 — 换皮肤 / 调字号 / 切紧凑模式都走这儿。"""
        for btn, (accent, danger) in getattr(self, "_tool_meta", {}).items():
            btn.setStyleSheet(_tool_qss(
                self._skin, accent=accent, danger=danger,
                fonts=self._fonts, compact=getattr(self, "_compact", False),
            ))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._apply_toolbar_compact()

    def _on_year_month_changed(self, y: int, m: int) -> None:
        """年视图里换了月份 — 工具栏标签/计数跟上, 藏着的月视图也同步。

        点小月、点日期、翻月都会走到这儿。回到月视图时正好停在这个月。
        """
        self._active_month_view.goto(y, m)
        if self._idle_month_view is not None:
            self._idle_month_view.goto(y, m)
        self._update_month_label()
        self._update_count()

    # ------------------------------------------------------------------
    # 月份切换 — 带动画
    # ------------------------------------------------------------------
    def shift_month(self, delta: int) -> None:
        if self._is_year_view():
            # 年视图下 ◀ ▶ 走的是详情里的月, 跨年就跟着换年。
            # 换月会触发 sig_month_changed — 标签/计数/藏着的月视图那边自己跟上
            self._year_view.shift_month(delta)
            return
        y, m = self._active_month_view.current_month()
        m += delta
        while m < 1:
            m += 12; y -= 1
        while m > 12:
            m -= 12; y += 1

        anim_on = _load_setting("anim", "1") == "1"
        if not anim_on or not self.isVisible():
            self._active_month_view.goto(y, m)
            self._update_month_label()
            self._update_count()
            return

        # 准备 idle 月视图到目标月 (第一次翻月时才建这块)
        idle = self._ensure_idle_month_view()
        idle.goto(y, m)

        # slide 方向
        width = self._stack.width()
        height = self._stack.height()
        if delta > 0:
            # 下一月: 新视图从右边滑入
            self._idle_month_view.move(width, 0)
            self._stack.setCurrentIndex(self._stack.indexOf(self._idle_month_view))
        else:
            # 上一月: 新视图从左边滑入
            self._idle_month_view.move(-width, 0)
            self._stack.setCurrentIndex(self._stack.indexOf(self._idle_month_view))

        # 动画: active 滑出, idle 滑入
        anim_active = QPropertyAnimation(self._active_month_view, b"pos")
        anim_active.setDuration(280)
        anim_active.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim_active.setStartValue(QPoint(0, 0))
        anim_active.setEndValue(QPoint(-delta * width, 0))

        anim_idle = QPropertyAnimation(self._idle_month_view, b"pos")
        anim_idle.setDuration(280)
        anim_idle.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim_idle.setStartValue(QPoint(delta * width, 0))
        anim_idle.setEndValue(QPoint(0, 0))

        self._anim_group = QParallelAnimationGroup()
        self._anim_group.addAnimation(anim_active)
        self._anim_group.addAnimation(anim_idle)
        self._anim_group.finished.connect(self._on_month_anim_finished)
        self._anim_group.start()

    def _on_month_anim_finished(self) -> None:
        # 交换 active/idle
        self._active_month_view, self._idle_month_view = (
            self._idle_month_view, self._active_month_view,
        )
        # 保留 idle 在右 (作为后续 slide 的 target)
        self._stack.setCurrentIndex(self._stack.indexOf(self._active_month_view))
        self._idle_month_view.move(0, 0)
        self._update_month_label()
        self._update_count()

    # ------------------------------------------------------------------
    # 铺满屏幕 / 普通窗口
    # ------------------------------------------------------------------
    def _collapsed_rect(self) -> QRect:
        """「收成窗口」时的矩形 — 居中, 大小不超过屏幕。

        启动弹出来的窗口就用这个尺寸 —— 不铺满整屏。
        """
        scr = QApplication.primaryScreen().availableGeometry()
        w = min(self.COLLAPSED_W, scr.width() - 80)
        h = min(self.COLLAPSED_H, scr.height() - 80)
        return QRect(
            scr.x() + (scr.width() - w) // 2,
            scr.y() + (scr.height() - h) // 2,
            w, h,
        )

    def _load_normal_rect(self) -> Optional[QRect]:
        """上次关窗时那个普通窗口的位置和大小 — 读出来照原样摆回去。

        存的是「收起时」的矩形: 用户上次把它拖到哪、拉多大, 下次启动
        就还是那个样子, 不用每次都弹一个默认尺寸。
        """
        raw = _load_setting("normal_rect", "")
        parts = raw.split(",")
        if len(parts) != 4:
            return None
        try:
            x, y, w, h = (int(p) for p in parts)
        except ValueError:
            return None
        if w < 200 or h < 150:
            return None        # 存坏了 / 太小, 用默认尺寸
        rect = QRect(x, y, w, h)
        # 换过显示器、拔过投影仪 → 存的位置可能已经不在任何屏幕上了。
        # 那种情况宁可用默认位置, 也别让窗口弹到看不见的地方。
        for scr in QApplication.screens():
            if scr.availableGeometry().intersects(rect):
                return rect
        return None

    def _save_normal_rect(self) -> None:
        """把当前普通窗口的矩形存下来 — 下次启动照原样摆回来。"""
        if getattr(self, "_full", False):
            return              # 铺满时的矩形不是用户想要的那个
        r = self.geometry()
        if r.width() < 200 or r.height() < 150:
            return
        _save_setting(
            "normal_rect", f"{r.x()},{r.y()},{r.width()},{r.height()}")

    def toggle_maximized(self) -> None:
        """在「铺满屏幕」和「普通窗口」之间切换。

        不用系统的 showMaximized: 满屏的桌面日历还原回来还是满屏, 用户
        就没法把窗口缩小成一块小挂历了。这里自己管状态, 还原时给一个
        居中的普通窗口, 之后拖边就能自由缩放。
        """
        if self._full:
            self._full = False
            # 还原成上次那个普通窗口 (没有就退回到居中的默认尺寸)
            rect = self._normal_rect or self._load_normal_rect()
            rect = rect if rect is not None else self._collapsed_rect()
            # 先按目标宽度切工具栏, 再摆 —— 摆的时候布局算出来的最小宽度
            # 才会是对的那个, 不然窗口会被钉在 1448 宽收不回来
            self._apply_toolbar_compact(rect.width())
            self.setGeometry(rect)
        else:
            self._normal_rect = self.geometry()
            self._save_normal_rect()
            self._full = True
            scr = QApplication.primaryScreen().geometry()
            self.setGeometry(scr)
        self.showNormal()
        self._apply_toolbar_compact()
        # 菜单里的勾选态跟着变 (F11 也能按, 所以菜单要刷新)
        QTimer.singleShot(0, self._refresh_more_menu)

    def _hit_test_edge(self, pos):
        """铺满屏幕时不能拖边缩放 — 那是屏幕边缘, 不是窗口边缘。"""
        if getattr(self, "_full", False):
            return None
        return super()._hit_test_edge(pos)

    def goto_today(self) -> None:
        if self._is_year_view():
            today = date.today()
            self._year_view.goto(today.year, today.month)
        else:
            self._active_month_view.goto_today()
            if self._idle_month_view is not None:
                self._idle_month_view.goto_today()    # 保持同步
        self._update_month_label()
        self._update_count()

    def refresh_view(self) -> None:
        self._active_month_view.refresh()
        yv = self._year_view_obj
        if yv is not None:
            yv.refresh()
        self._update_count()
        # 快捷待办面板开着的话也刷一遍 —— 两边看的是同一份数据,
        # 在格子那边勾了, 面板里也得跟着变
        qm = getattr(self, "_quick_mgr", None)
        if qm is not None and qm.visible():
            qm.panel.refresh()

    # ------------------------------------------------------------------
    # 标签/计数
    # ------------------------------------------------------------------
    def _update_month_label(self):
        y, m = self._current_view().current_month()
        # 年视图下 ◀ ▶ 走的是详情里的月 — 标签跟着动, 按了才有反馈。
        # 「现在是年视图」由那个高亮的切换按钮 + 年视图自己的标题说明。
        if getattr(self, "_compact", False):
            # 窄窗口里 "2026 年 10 月" 要 209px, 标签只有 120 多 → 用短写法
            self._month_label.setText(f"{y % 100:02d}年{m:02d}月")
        else:
            self._month_label.setText(f"{y} 年 {m:02d} 月")

    def _update_count(self):
        try:
            con = open_default()
            y, m = self._current_view().current_month()
            if self._is_year_view():
                rows = con.execute(
                    "SELECT COUNT(*) FROM calendar_items WHERE date BETWEEN ? AND ?",
                    (f"{y:04d}-01-01", f"{y:04d}-12-31"),
                ).fetchone()[0]
                self._count_label.setText(f"{y} 年  共 {rows} 项")
                return
            from calendar import monthrange
            days = monthrange(y, m)[1]
            start = f"{y:04d}-{m:02d}-01"
            end = f"{y:04d}-{m:02d}-{days:02d}"
            rows = con.execute(
                "SELECT COUNT(*) FROM calendar_items WHERE date BETWEEN ? AND ?",
                (start, end),
            ).fetchone()[0]
            self._count_label.setText(f"{y}-{m:02d}  共 {rows} 项")
        except Exception:
            self._count_label.setText("(数据加载失败)")

    # ------------------------------------------------------------------
    # 皮肤 / 不透明度 / 置顶 / 动画开关 / 字号
    # ------------------------------------------------------------------
    def _apply_skin(self) -> None:
        self.setStyleSheet(stylesheet_for(self._skin, self._fonts))
        # 状态栏用单独样式
        if hasattr(self, "_status_bar") and self._status_bar is not None:
            self._status_bar.setStyleSheet(
                f"QStatusBar {{ color: {self._skin.status_fg};"
                f" font-size: {self._fonts.status}pt; }}"
                f"QStatusBar > QLabel {{ color: {self._skin.status_fg};"
                f" font-size: {self._fonts.status}pt; }}"
            )
        # 月视图的格子配色是内联样式, 不会跟着主窗 QSS 走 — 得手动推
        for mv in self._month_views():
            mv.apply_skin(self._skin.name)
        # 年视图同理 — 小月和下面那块详情都得重刷
        yv = self._year_view_obj
        if yv is not None:
            yv.apply_skin(self._skin.name)
        # 工具栏按钮同理 — 各自有内联 QSS, 换皮肤得逐个重刷
        self._restyle_tools()

    def _apply_fonts(self) -> None:
        """把当前字号比例落到整个主窗。

        字号不只是 QSS: 格子里的行高 / ☐ 宽度 / 每格能显示几条都是跟着走的,
        所以月视图得单独推一遍 (和换皮肤走的是两套路径)。
        """
        self.setStyleSheet(stylesheet_for(self._skin, self._fonts))
        self._restyle_tools()
        self._style_month_label()
        bar = getattr(self, "_top_bar", None)
        if bar is not None:
            bar.setFixedHeight(self._fonts.bar_h)
            self.set_drag_area(bar, height=self._fonts.bar_h)
        status = getattr(self, "_status_bar", None)
        if status is not None:
            status.setStyleSheet(
                f"QStatusBar {{ color: {self._skin.status_fg};"
                f" font-size: {self._fonts.status}pt; }}"
                f"QStatusBar > QLabel {{ color: {self._skin.status_fg};"
                f" font-size: {self._fonts.status}pt; }}"
            )
        for mv in self._month_views():
            mv.apply_font_scale(self._font_scale)
        yv = self._year_view_obj
        if yv is not None:
            yv.apply_font_scale(self._font_scale)
        # 工具栏按钮重刷了样式, 年视图按钮的高亮要重新 polish 才认
        self._sync_year_btn()
        # 顺带把对话框那些「没写死字号」的控件也带上一遍
        if self._base_app_pt > 0:
            app_font = self._app.font()
            app_font.setPointSize(
                max(7, int(round(self._base_app_pt * self._font_scale)))
            )
            self._app.setFont(app_font)

    # ------------------------------------------------------------------
    # 「⋯ 更多」菜单 — 低频功能全在这儿, 每项都带中文名 + 一句话说明
    # ------------------------------------------------------------------
    def _build_more_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)

        def section(title: str) -> None:
            # 不用 addSection() —— Windows 原生菜单样式下分组标题不显示,
            # 改用「禁用的 action」当标题, 各平台都稳。
            head = menu.addAction(title)
            head.setEnabled(False)

        def item(text: str, desc: str, slot, shortcut: str = "") -> QAction:
            act = menu.addAction(text)
            act.setToolTip(desc)
            if shortcut:
                act.setShortcut(shortcut)
            act.triggered.connect(slot)
            return act

        section("常用")
        item("年视图 / 月视图", "一屏看全年 12 个月, 下面是选中月的详情",
             self._toggle_year_view_from_menu, "Y")
        quick_act = item("快捷待办小窗…",
                         "弹一个小窗口出来, 随手记一条待办\n"
                         "整个面板能随便拖, 位置会记住\n"
                         "(托盘图标单击一下也是它)",
                         self._toggle_quick_panel)
        quick_act.setCheckable(True)
        quick_act.setChecked(
            self._quick_mgr is not None and self._quick_mgr.visible())
        item("搜索全部条目…", "在所有日期的所有条目里找关键词",
             self._open_search, "Ctrl+F")
        item("生成日报 / 周报…", "预览、复制, 或导出成 MD / HTML / PDF",
             self._open_report, "Ctrl+R")
        item("用 AI 润色日报…", "调用 Claude 把日报改写得更通顺 "
                              "(需配置 API key)",
             self._open_ai, "Ctrl+Shift+R")

        section("数据")
        item("云同步 (CalDAV)…",
             "连接 Google / iCloud / Outlook, 双向同步日程", self._open_sync)
        item("便签…", "开一个随手记的小便签窗口", self._toggle_notes)
        item("从 DesktopCal 导入…", "把 DesktopCal 里的历史条目搬过来",
             lambda: _import_dialog())
        item("重新拉取数据", "从数据库刷新本月显示 (F5)", self.refresh_view)

        section("外观")
        menu.addMenu(self._build_skin_menu())
        menu.addMenu(self._build_font_menu())
        top_act = item("窗口置顶", "让 PyCal 始终浮在其它窗口之上",
                       self._toggle_topmost)
        top_act.setCheckable(True)
        top_act.setChecked(self._topmost)
        anim_act = item("月份切换动画", "翻月时左右滑动, 可以关掉",
                        self._toggle_animation)
        anim_act.setCheckable(True)
        anim_act.setChecked(_load_setting("anim", "1") == "1")
        item("生成月历壁纸…", "把当月月视图导成图片并设为桌面壁纸",
             lambda: _set_wallpaper(self))

        section("设置")
        item("🔑 AI 润色 API key…",
             "填 Anthropic API key, 界面上就能用 AI 润色日报/周报 "
             "(不用再改环境变量)",
             self._open_settings, "Ctrl+,")

        section("帮助")
        item("快捷键与功能说明…", "所有功能和快捷键一览", self._open_help, "F1")
        item("关于 PyCal", "版本信息与数据存放位置", self._show_about)

        section("窗口")
        full_act = item("铺满屏幕 / 收成窗口",
                        "铺满屏幕 = 当整面桌面的日历用\n"
                        "收成窗口 = 自由拖四边缩放, 挪到哪都行 (快捷键 F11)",
                        self.toggle_maximized)
        full_act.setCheckable(True)
        full_act.setChecked(self._full)
        start_act = item("启动时铺满屏幕",
                         "打开 PyCal 就铺满整面屏幕 (桌面日历)\n"
                         "默认是关的 —— 启动弹一个普通窗口出来, "
                         "能拖着挪、能自由缩放, 大小跟上次一样",
                         self._toggle_startup_full)
        start_act.setCheckable(True)
        start_act.setChecked(
            _load_setting("startup_mode", "window") == "full")
        close_menu = menu.addMenu("关闭窗口时")
        for label, value, tip in [
            ("收起到托盘 (后台继续运行)", "tray",
             "关窗后程序留在后台, 提醒和自动同步照常"),
            ("直接退出程序", "exit", "关窗即结束程序"),
        ]:
            act = close_menu.addAction(label)
            act.setToolTip(tip)
            act.setCheckable(True)
            act.setChecked(_load_setting("close_behavior", "tray") == value)
            act.triggered.connect(
                lambda _=False, v=value: _save_setting("close_behavior", v)
            )
        item("收起窗口到托盘", "窗口藏起来, 程序继续在后台运行", self._collapse)
        item("彻底退出 PyCal", "结束程序, 停止后台提醒和同步", self._quit_app)
        return menu

    def _build_skin_menu(self) -> QMenu:
        """皮肤 + 不透明度 — 作为子菜单塞进「更多」里。"""
        skin_menu = QMenu("皮肤 / 不透明度", self)
        for name, label in [
            ("light_default", "浅色 — 默认"),
            ("warm_paper", "暖色 — 米黄"),
            ("dark_mode", "深色 — 夜间"),
            ("glass_blur", "玻璃 — 磨砂"),
        ]:
            act = skin_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._skin.name == name)
            act.triggered.connect(lambda _=False, n=name: self._set_skin(n))
        skin_menu.addSeparator()
        op_menu = skin_menu.addMenu("不透明度")
        for pct in (60, 75, 90, 100):
            act = op_menu.addAction(f"{pct}%")
            act.setCheckable(True)
            act.setChecked(abs(self._opacity * 100 - pct) < 1)
            act.triggered.connect(
                lambda _=False, p=pct: self._set_opacity(p / 100)
            )
        return skin_menu

    def _build_font_menu(self) -> QMenu:
        """字体大小 — 整个主窗 (工具栏 / 月视图 / 状态栏) 一起缩。"""
        font_menu = QMenu("字体大小", self)
        font_menu.setToolTipsVisible(True)

        step = font_menu.addMenu("放大 / 缩小一档")
        step.addAction("A+ 放大一点").triggered.connect(
            lambda: self._step_font_scale(+0.05))
        step.addAction("A− 缩小一点").triggered.connect(
            lambda: self._step_font_scale(-0.05))
        step.addSeparator()
        # 微调到档位之间时下面的预设就都没勾了 — 这里显示当前值, 免得
        # 用户连点几下之后不知道自己在第几档
        self._font_scale_hint = step.addAction(label_for_scale(self._font_scale))
        self._font_scale_hint.setEnabled(False)

        font_menu.addSeparator()
        # 勾选态要能原地更新 —— 用户正开着菜单连点 A+ 时重建菜单会把手
        # 里的子菜单一起销毁, 菜单当场就没了。所以留一份 action 引用。
        self._font_scale_actions = {}
        for value, label in SCALES:
            act = font_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(abs(self._font_scale - value) < 0.005)
            act.setToolTip("主窗各处字号一起放大或缩小")
            act.triggered.connect(lambda _=False, v=value: self._set_font_scale(v))
            self._font_scale_actions[value] = act

        font_menu.addSeparator()
        font_menu.addAction(
            f"恢复默认 ({label_for_scale(DEFAULT_SCALE)})"
        ).triggered.connect(lambda: self._set_font_scale(DEFAULT_SCALE))
        return font_menu

    # ------------------------------------------------------------------
    def _set_font_scale(self, scale: float) -> None:
        self._font_scale = scale_from_setting(scale)
        self._fonts = fonts_for(self._font_scale)
        _save_setting("font_scale", str(self._font_scale))
        self._apply_fonts()
        # 菜单不重建, 只更新勾选 — 菜单还开着, 用户可以接着连点
        for value, act in getattr(self, "_font_scale_actions", {}).items():
            act.setChecked(abs(self._font_scale - value) < 0.005)
        hint = getattr(self, "_font_scale_hint", None)
        if hint is not None:
            hint.setText(label_for_scale(self._font_scale))

    def _step_font_scale(self, delta: float) -> None:
        """按档微调 — 菜单打开着就能连点, 每点一次立刻见效。"""
        self._set_font_scale(round(self._font_scale + delta, 2))

    def _open_help(self) -> None:
        from pycal.ui.help_dialog import HelpDialog
        HelpDialog(self).exec()

    def _show_about(self) -> None:
        try:
            from pycal import __version__
        except Exception:
            __version__ = "?"
        db = _db_location()
        QMessageBox.about(
            self, "关于 PyCal",
            f"<b>PyCal {__version__}</b><br><br>"
            "桌面月历本 — 记事 / 提醒 / 倒数日 / 日报周报 / CalDAV 同步<br><br>"
            f"数据文件:<br><code>{db}</code><br><br>"
            "快捷键: <b>F1</b> 查看完整说明",
        )

    def _set_skin(self, name: str) -> None:
        self._skin = current_skin(name)
        _save_setting("skin", name)
        self._apply_skin()
        # 「更多」菜单里的皮肤勾选态跟着变
        if getattr(self, "_btn_more", None) is not None:
            self._btn_more.setMenu(self._build_more_menu())

    def _set_opacity(self, p: float) -> None:
        self._opacity = p
        _save_setting("opacity", str(p))
        self._apply_opacity()

    def _apply_opacity(self) -> None:
        self.setWindowOpacity(self._opacity)
        self._opacity_label.setText(f"{int(self._opacity * 100)}%")

    def _toggle_animation(self) -> None:
        cur = _load_setting("anim", "1")
        new = "0" if cur == "1" else "1"
        _save_setting("anim", new)
        self._anim_label.setText(f"动画 {'开' if new == '1' else '关'}")
        self._refresh_more_menu()

    def _toggle_topmost(self) -> None:
        self._topmost = not self._topmost
        _save_setting("topmost", "1" if self._topmost else "0")
        self._apply_topmost()

    def _toggle_startup_full(self) -> None:
        """启动时铺满屏幕 还是 启动就是普通窗口。

        默认是普通窗口 —— 一弹出就铺满整屏太霸道。打开这个开关的人
        是真拿它当整面桌面的日历用的, 那下次启动就还铺满。
        """
        full = _load_setting("startup_mode", "window") != "full"
        _save_setting("startup_mode", "window" if full else "full")
        QTimer.singleShot(0, self._refresh_more_menu)

    def _toggle_quick_panel(self) -> None:
        """开关快捷待办小窗 —— 跟点托盘图标一个效果。"""
        if self._quick_mgr is None:
            return
        self._quick_mgr.toggle()
        QTimer.singleShot(0, self._refresh_more_menu)

    def _apply_topmost(self) -> None:
        flags = self.windowFlags()
        if self._topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
            flags &= ~Qt.WindowType.WindowStaysOnBottomHint
        else:
            flags |= Qt.WindowType.WindowStaysOnBottomHint
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()  # 重新设置 flags 后需要 show()

    def _open_search(self) -> None:
        _search_dialog(self)

    # ------------------------------------------------------------------
    # 云同步
    # ------------------------------------------------------------------
    def _open_sync(self) -> None:
        from pycal.ui.sync_dialog import SyncDialog
        dlg = SyncDialog(self)
        dlg.exec()
        self.refresh_view()      # 同步后刷新月视图

    # ------------------------------------------------------------------
    # 日报 / 周报
    # ------------------------------------------------------------------
    def _open_report(self, start_ai: bool = False) -> None:
        from pycal.ui.report_dialog import ReportDialog
        ReportDialog(self, start_ai=start_ai).exec()

    def _open_ai(self) -> None:
        """AI 润色日报 — 没配 key 就直接把配置窗口摆出来, 而不是让人去改环境变量。"""
        from pycal.report.ai_polish import has_api_key, api_key_hint
        if not has_api_key():
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("AI 日报 — 还差一步")
            box.setText("AI 润色需要一个 Anthropic API key。")
            box.setInformativeText(
                api_key_hint()
                + "\n\n不配也不影响其它功能 —— 日报/周报照常能生成。"
            )
            btn_cfg = box.addButton("🔑 现在配置",
                                    QMessageBox.ButtonRole.AcceptRole)
            box.addButton("稍后再说", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            # 配好了就直接继续 —— 别让人再点一次「AI 日报」
            if box.clickedButton() is not btn_cfg:
                return
            if not self._open_settings():
                return
        self._open_report(start_ai=True)

    def _open_settings(self) -> bool:
        """设置对话框 (API key)。返回关掉之后有没有可用的 key。"""
        from pycal.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        dlg.exec()
        return dlg.key_configured()

    def _new_item_today(self) -> None:
        """Ctrl+N — 在今天新建一条格子条目。"""
        self._active_month_view.goto_today()
        self._update_month_label()
        self._active_month_view.sig_new_item.emit(date.today().isoformat(), 0)

    def _auto_report_tick(self) -> None:
        """到点自动生成日报 (18:00 后) / 周报 (周五 17:00 后), 每天最多一次。"""
        if _load_setting("auto_report", "1") != "1":
            return
        try:
            from datetime import datetime, timedelta
            from pycal.report import (
                render_daily_markdown, render_weekly_markdown,
            )
            now = datetime.now()
            out_dir = Path(
                _load_setting("report_dir", "")
                or (Path(os.environ.get("APPDATA", "")) / "PyCal" / "reports")
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            today = now.date()

            # 日报
            daily_key = today.isoformat()
            if now.hour >= 18 and \
                    _load_setting("last_daily", "") != daily_key:
                md = render_daily_markdown(today)
                (out_dir / f"pycal_daily_{today:%Y%m%d}.md").write_text(
                    md, encoding="utf-8")
                _save_setting("last_daily", daily_key)
                print(f"[report] 日报已生成: {out_dir}")

            # 周报 (周五 17:00)
            weekly_key = f"{today.isocalendar()[0]}W{today.isocalendar()[1]}"
            if now.weekday() == 4 and now.hour >= 17 and \
                    _load_setting("last_weekly", "") != weekly_key:
                md = render_weekly_markdown(today)
                (out_dir / f"pycal_weekly_{weekly_key}.md").write_text(
                    md, encoding="utf-8")
                _save_setting("last_weekly", weekly_key)
                print(f"[report] 周报已生成: {out_dir}")
        except Exception as e:
            print(f"[report] 自动生成失败: {e}")

    def _auto_sync_tick(self) -> None:
        """后台自动同步 — 无账号时静默跳过, 失败不打扰用户。

        同步要跑网络 (CalDAV 往返), 一次几秒到几十秒 —— 放主线程里
        界面会整个冻住, 拖不动也点不了。所以丢到后台线程去, 跑完再
        通过信号回主线程刷新。
        """
        try:
            from pycal.sync.accounts import list_accounts
            accs = [a for a in list_accounts() if a.enabled]
            if not accs:
                return
        except Exception:
            return
        if getattr(self, "_sync_running", False):
            return      # 上一轮还没跑完, 别叠上来
        self._sync_running = True

        runner = _SyncRunner()
        thread = QThread(self)
        runner.moveToThread(thread)
        thread.started.connect(runner.run)
        runner.finished.connect(self._on_sync_finished)
        runner.finished.connect(thread.quit)
        thread.finished.connect(runner.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # 留着引用 — 不然 QThread 可能被提前回收掉
        self._sync_runner = runner
        self._sync_thread = thread
        thread.start()

    def _on_sync_finished(self, results) -> None:
        """同步跑完 (在主线程) — 刷新界面。"""
        self._sync_running = False
        try:
            total = sum(r.pulled + r.pushed for r in results or [])
        except Exception:
            total = 0
        if total:
            self.refresh_view()          # 先刷新 (会把状态栏文字重置)
            self._count_label.setText(
                f"{self._count_label.text()}  ☁ 同步 {total} 条"
            )

    def _toggle_notes(self) -> None:
        from pycal.notes import NotesManager
        if not hasattr(self, "_notes_mgr") or self._notes_mgr is None:
            self._notes_mgr = NotesManager()
            self._notes_mgr.load()
        self._notes_mgr.create()

    def _quit_app(self) -> None:
        if getattr(self, "_tray", None) is not None:
            self._tray.hide()
        self._save_normal_rect()   # 窗口多大 — 下次启动照原样弹出来
        self.hide()          # 立刻收掉窗口, 别让它比进程多活一会儿
        self._app.quit()

    def _refresh_more_menu(self) -> None:
        """「更多」菜单里的勾选态 (置顶/动画/关窗行为) 改完要重建菜单。"""
        btn = getattr(self, "_btn_more", None)
        if btn is not None:
            btn.setMenu(self._build_more_menu())

    # ------------------------------------------------------------------
    # 收起 / 关闭
    #
    # 之前 Alt+F4 / 任务栏 ✕ 会无声无息地把窗口藏起来, 用户以为程序死了,
    # 也不知道怎么真正退出。现在第一次关窗先问清楚, 之后按选择静默执行,
    # 并弹一次托盘气泡告诉用户「还在后台 + 怎么唤回」。
    # ------------------------------------------------------------------
    def _has_tray(self) -> bool:
        return getattr(self, "_tray", None) is not None

    def _collapse(self, announce: bool = True) -> None:
        """收起窗口 — 程序继续在后台跑, 提醒和自动同步不受影响。"""
        # 记下窗口现在多大 — 下次启动照原样弹出来
        self._save_normal_rect()
        self.hide()
        # 终端里也喊一声 —— 很多人是 `python -m pycal gui` 起的,
        # 窗口一消失就以为程序死了, 白白去杀进程。
        print("[PyCal] 已收起到后台, 程序还在跑 (提醒和自动同步照常)。\n"
              "        找回窗口: 双击右下角托盘图标 | 彻底退出: "
              "托盘图标右键 → 退出 PyCal (或在这个终端按 Ctrl+C)")
        if announce and self._has_tray():
            self._tray.showMessage(
                "PyCal 已收起到后台",
                "程序还在跑, 提醒和自动同步照常。\n"
                "双击右下角托盘图标能弹「快捷待办」小窗, "
                "双击重新打开主窗口, 点托盘图标右键菜单的"
                "「退出 PyCal」可彻底结束。",
                QSystemTrayIcon.MessageIcon.Information,
                6000,
            )

    def _ask_close_behavior(self) -> None:
        """第一次关窗 — 让用户选「收起到托盘」还是「直接退出」。"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("关闭 PyCal")
        box.setText("要现在关闭 PyCal 吗?")
        box.setInformativeText(
            "<b>收起到托盘</b><br>"
            "窗口藏到右下角托盘，程序继续在后台运行，"
            "提醒和自动同步照常工作。<br>"
            "随时双击托盘图标可以重新打开。<br><br>"
            "<b>直接退出</b><br>"
            "彻底结束程序，后台的提醒和自动同步都会停止。<br><br>"
            "这个选择会记住，以后关窗不再打扰。"
            "随时能在「⋯ 更多 → 关闭窗口时」里改。"
        )
        tray_btn = box.addButton("收起到托盘",
                                 QMessageBox.ButtonRole.AcceptRole)
        quit_btn = box.addButton("直接退出",
                                 QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(tray_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is cancel_btn:
            return          # 取消 = 什么都不做, 窗口留着
        _save_setting("close_hint_shown", "1")
        if clicked is quit_btn:
            _save_setting("close_behavior", "exit")
            self._quit_app()
        else:
            _save_setting("close_behavior", "tray")
            self._collapse()

    def _on_escape(self) -> None:
        """Esc 走和关窗一样的流程, 别再无声无息地消失。"""
        self.close()

    def closeEvent(self, event):
        event.ignore()          # 先一律不真关, 由下面决定去留
        # 窗口现在多大 —— 下次启动照原样弹出来
        self._save_normal_rect()
        if not self._has_tray():
            # 没有托盘可躲 — 收起来用户就再也找不回来了, 只能真退出
            self._quit_app()
            return
        if _load_setting("close_behavior", "tray") == "exit":
            self._quit_app()
            return
        if _load_setting("close_hint_shown", "0") == "1":
            self._collapse()
            return
        self._ask_close_behavior()


# ============================================================================
# 后台同步 worker
# ============================================================================
class _SyncRunner(QObject):
    """在后台线程里跑云同步。

    信号 finished 在工作线程里发出去, 主窗口那边是排队连接, 自动切回
    主线程执行 —— 界面不会被网络 IO 卡住。
    """

    finished = Signal(list)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)

    @Slot()
    def run(self) -> None:
        results: list = []
        try:
            from pycal.sync.engine import sync_all
            results = sync_all()
        except Exception as e:
            print(f"[sync] 后台同步失败: {e}")
        self.finished.emit(results)


# ============================================================================
# 工具函数
# ============================================================================
def _load_setting(key: str, default: str = "") -> str:
    from pycal.settings import get_setting
    return get_setting(key, default)


def _save_setting(key: str, value: str) -> None:
    from pycal.settings import set_setting
    set_setting(key, value)


def _db_location() -> str:
    """当前数据库文件路径 — 「关于」窗口里给用户看的。"""
    try:
        from pycal.db.connection import default_db_path
        return str(default_db_path())
    except Exception:
        return "(未知)"


def _tool_qss(skin: Skin, danger: bool = False, accent: bool = False,
              fonts: Optional[Fonts] = None, compact: bool = False) -> str:
    """工具栏按钮的 QSS。

    普通按钮跟着皮肤走 (深色皮肤下不再是一排白药丸); accent/danger 是
    固定的强调色, 两种皮肤下都读得清。字号由 fonts 决定。

    compact=True 是窗口窄了之后的版本: 只留图标, 边距也收紧 ——
    不然 12 个按钮光最小宽度就 684px, 窗口缩不下去。
    """
    f = fonts or fonts_for()
    if danger:
        normal, fg = "#FFE5E5", "#C0392B"
        hover, pressed, border = "#FFD5D5", "#FFC0C0", "#E8A0A0"
    elif accent:
        normal, fg = "#EBF5FB", "#1B4F72"
        hover, pressed, border = "#D6EAF8", "#C6E2F5", "#85C1E9"
    else:
        normal, fg = skin.btn_bg, skin.btn_fg
        hover, pressed, border = skin.btn_hover, skin.btn_pressed, skin.btn_border

    fg_rule = f"color: {fg};" if fg else ""
    pad = "2px 4px" if compact else "4px 8px"
    radius = 3 if compact else 4
    return (
        f"QToolButton {{ border: 1px solid {border}; border-radius: {radius}px;"
        f" background: {normal}; {fg_rule} padding: {pad};"
        f" font-size: {f.toolbar}pt; }}"
        f"QToolButton:hover {{ background: {hover}; border-color: #4A90E2; }}"
        f"QToolButton:pressed {{ background: {pressed}; }}"
        # 开关型按钮 (置顶/动画) 的选中态 — 由 setProperty("on", ...) 驱动
        f'QToolButton[on="true"] {{ background: #FFF3B0;'
        f" border: 1px solid #EFA020; }}"
    )


def _tool_btn(text: str, skin: Optional[Skin] = None,
              danger: bool = False, accent: bool = False,
              fonts: Optional[Fonts] = None) -> QToolButton:
    """工具栏按钮 — accent 给主功能上蓝底加粗, danger 给关闭上红底。"""
    b = QToolButton()
    b.setText(text)
    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    b.setStyleSheet(
        _tool_qss(skin or current_skin(), accent=accent, danger=danger,
                  fonts=fonts)
    )
    b.setProperty("on", "false")
    return b


def _import_dialog() -> None:
    from pycal.db.connection import desktopcal_db_path
    from pycal.importer import run_import
    default_src = str(desktopcal_db_path())
    n = run_import(default_src)
    QMessageBox.information(
        None, "导入完成",
        f"已从\n{default_src}\n导入 {n} 条记录到 PyCal。",
    )


def _search_dialog(parent: QWidget) -> None:
    from pycal.ui.search_dialog import SearchDialog
    dlg = SearchDialog(parent)
    def _jump(item_id: str, day_iso: str):
        con = open_default()
        it = get_item(con, item_id)
        if not it:
            QMessageBox.warning(parent, "未找到", f"条目 {item_id} 已不存在。")
            return
        from pycal.ui.editor import ItemEditorDialog
        ed = ItemEditorDialog(item=it)
        ed.exec()
    dlg.sig_jump_to_item.connect(_jump)
    dlg.exec()


def _set_wallpaper(parent: QWidget) -> None:
    try:
        from pycal.desktop.wallpaper import make_and_set_wallpaper
        today = date.today()
        path = make_and_set_wallpaper(today.year, today.month)
        QMessageBox.information(parent, "壁纸生成",
                                f"已生成并设为壁纸:\n{path}")
    except Exception as e:
        QMessageBox.warning(parent, "壁纸生成失败", str(e))


# ============================================================================
# 系统托盘 + 通知
# ============================================================================
def _make_app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(245, 245, 245))
    p.setPen(QColor(60, 60, 60))
    p.drawRoundedRect(4, 4, 56, 56, 8, 8)
    p.setBrush(QColor(220, 70, 70))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(4, 4, 56, 16, 8, 8)
    p.setPen(QColor(40, 40, 40))
    f = QFont("Segoe UI", 22, QFont.Weight.Bold)
    p.setFont(f)
    p.drawText(pix.rect().adjusted(0, 12, 0, 0), Qt.AlignmentFlag.AlignCenter, "📅")
    p.end()
    return QIcon(pix)


def _build_tray(win: QMainWindow, app: QApplication) -> Optional[QSystemTrayIcon]:
    app.setWindowIcon(_make_app_icon())
    if not QSystemTrayIcon.isSystemTrayAvailable():
        # 系统没有托盘 (精简版 Windows / 某些服务器环境) —— 必须返回 None,
        # 否则 closeEvent 会把窗口藏起来, 用户再也找不回来。
        return None
    tray = QSystemTrayIcon(_make_app_icon(), win)
    tray.setToolTip("PyCal — 桌面日历\n单击: 快捷待办\n双击: 打开主窗口")
    menu = QMenu()
    act_show = QAction("显示 / 隐藏主窗", tray)
    def _show_hide():
        if win.isVisible():
            win.hide()
        else:
            win.showNormal()
            win.raise_()
            win.activateWindow()
    act_show.triggered.connect(_show_hide)
    menu.addAction(act_show)
    # 快捷待办 — 单击托盘图标弹出来的小窗口, 随手记一条
    act_quick = QAction("快捷待办", tray)
    act_quick.setCheckable(True)
    act_quick.setToolTip("弹一个小窗口出来, 随手记一条待办")
    def _toggle_quick():
        win._quick_mgr.toggle()
        act_quick.setChecked(win._quick_mgr.visible())
    act_quick.triggered.connect(_toggle_quick)
    menu.addAction(act_quick)
    act_search = QAction("搜索…", tray)
    act_search.triggered.connect(lambda: _search_dialog(win))
    menu.addAction(act_search)
    # 菜单每次弹出前对一下勾 —— 面板可能是从别处关掉的,
    # 勾留在那儿不动就骗人了
    menu.aboutToShow.connect(
        lambda: act_quick.setChecked(win._quick_mgr.visible()))
    menu.addSeparator()
    act_quit = QAction("退出 PyCal", tray)
    def _quit():
        tray.hide()
        app.quit()
    act_quit.triggered.connect(_quit)
    menu.addAction(act_quit)
    tray.setContextMenu(menu)

    def _on_activated(reason):
        # 单击 → 快捷待办小窗; 双击 → 主窗口 (帮助里就是这么写的)。
        # 双击时 Windows 会先发一次单击, 所以双击时把小窗收掉, 别两个都杵着。
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            win._quick_mgr.toggle()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            win._quick_mgr.hide()
            win.showNormal()
            win.raise_()
            win.activateWindow()

    tray.activated.connect(_on_activated)
    tray.show()
    return tray


def _build_reminder_worker(tray: Optional[QSystemTrayIcon]):
    from pycal.notifier import ReminderWorker
    worker = ReminderWorker()
    if tray is not None:
        worker.attach_tray(tray)
    return worker


def _on_reminder_fired(win: QMainWindow, item_id: str, title: str) -> None:
    print(f"[reminder] item_id={item_id}  title={title!r}")
    if win.isVisible():
        ret = QMessageBox.question(
            win, "提醒",
            f"计划已到期:\n\n  {title}\n\n是否立刻打开该条目?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            from pycal.ui.editor import ItemEditorDialog
            con = open_default()
            it = get_item(con, item_id)
            if it:
                ItemEditorDialog(item=it).exec()
