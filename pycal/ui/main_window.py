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
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QPixmap, QPainter, QShortcut, QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QLabel,
    QVBoxLayout, QHBoxLayout, QPushButton, QFrame, QStatusBar,
    QMenu, QMessageBox, QToolButton, QSystemTrayIcon, QSlider,
    QButtonGroup,
)

from pycal.db.connection import open_default
from pycal.db.models import CalendarItem, RepeatRule, Reminder, Countdown
from pycal.db.repository import upsert_item, list_items_by_date, get_item
from pycal.ui.month_view import MonthView
from pycal.ui.skin import Skin, current_skin, stylesheet_for, SKINS
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


def run(fullscreen: bool = True) -> int:
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

    def __init__(self, app: QApplication, fullscreen: bool = True):
        super().__init__()
        self._app = app
        self._fullscreen_on_start = fullscreen
        self._skin: Skin = current_skin(
            _load_setting("skin", "light_default")
        )
        self._topmost: bool = _load_setting("topmost", "0") == "1"
        self._opacity: float = float(_load_setting("opacity", "1.0") or "1.0")

        self.setWindowTitle("PyCal")
        if fullscreen:
            screen = app.primaryScreen().geometry()
            self.setGeometry(screen)
        else:
            self.resize(1000, 720)

        # Frameless + transparent (FramelessHint 已由 FloatingWindow 处理)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnBottomHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 应用皮肤 + 不透明度
        self._apply_skin()

        # 构造 UI
        self._build_ui()

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
        bar.setFixedHeight(36)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(4, 0, 4, 0)
        bar_layout.setSpacing(4)

        title = QLabel("📅 PyCal")
        title.setProperty("role", "title")
        bar_layout.addWidget(title)
        bar_layout.addSpacing(20)

        self._btn_prev = _tool_btn("◀", self._skin)
        self._month_label = QLabel()
        self._month_label.setProperty("role", "month_label")
        self._month_label.setMinimumWidth(140)
        self._month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._btn_next = _tool_btn("▶", self._skin)
        self._btn_today = _tool_btn("今天", self._skin)
        for b in (self._btn_prev, self._month_label, self._btn_next, self._btn_today):
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
            self._btn_today: (False, False),
            self._btn_report: (True, False), self._btn_ai: (True, False),
            self._btn_search: (False, False), self._btn_more: (False, False),
            self._btn_help: (False, False),
            self._btn_min: (False, False), self._btn_max: (False, False),
            self._btn_exit: (False, True),
        }
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
        self.set_drag_area(bar, height=36)

        # 月视图 (放在一个 QStackedWidget 里方便做切换动画)
        from PySide6.QtWidgets import QStackedWidget
        self._stack = QStackedWidget()
        self._month_left = MonthView(skin_name=self._skin.name)
        self._month_right = MonthView(skin_name=self._skin.name)
        # 两层用于 slide-in / slide-out
        self._stack.addWidget(self._month_left)
        self._stack.addWidget(self._month_right)
        self._stack.setCurrentIndex(0)
        # 当前月份初始设定相同
        self._active_month_view = self._month_left
        self._idle_month_view = self._month_right
        # 勾选/增删后刷新状态栏计数
        for mv in (self._month_left, self._month_right):
            mv.sig_data_changed.connect(self._update_count)
        self._top_layout.addWidget(self._stack, 1)

        # 状态栏
        status = QStatusBar()
        self._count_label = QLabel("0 项")
        self._opacity_label = QLabel("100%")
        self._anim_label = QLabel("动画 开")
        self._hint_label = QLabel("单击 ☐ 打勾 · 双击文字编辑 · 右键更多")
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
        # 动画开关的初始状态 — 状态栏文案也要跟着设置走, 别写死「开」
        anim_on = _load_setting("anim", "1") == "1"
        self._anim_label.setText(f"动画 {'开' if anim_on else '关'}")

        # 提示 — 每个按钮都有, 别再靠猜
        for b, tip in [
            (self._btn_prev, "上一月"),
            (self._btn_next, "下一月"),
            (self._btn_today, "回到今天"),
            (self._btn_report, "生成日报 / 周报\n预览、复制、导出 MD / HTML / PDF"),
            (self._btn_ai, "用 Claude 把日报 / 周报改写得更通顺\n(需要 ANTHROPIC_API_KEY)"),
            (self._btn_search, "搜索全部条目\n快捷键 Ctrl+F"),
            (self._btn_more, "全部功能菜单\n云同步 / 便签 / 导入 / 外观 / 壁纸 …"),
            (self._btn_help, "快捷键与功能说明\n快捷键 F1"),
            (self._btn_min, "收起窗口到托盘\n程序继续在后台运行, 提醒照常弹出\n(想彻底退出请点「× 退出」)"),
            (self._btn_max, "最大化 / 还原窗口\n也可双击工具栏空白处"),
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

    # ------------------------------------------------------------------
    # 月份切换 — 带动画
    # ------------------------------------------------------------------
    def shift_month(self, delta: int) -> None:
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

        # 准备 idle 月视图到目标月
        self._idle_month_view.goto(y, m)
        self._idle_month_view.refresh()

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

    def goto_today(self) -> None:
        self._active_month_view.goto_today()
        self._idle_month_view.goto_today()    # 保持同步
        self._update_month_label()
        self._update_count()

    def refresh_view(self) -> None:
        self._active_month_view.refresh()
        self._update_count()

    # ------------------------------------------------------------------
    # 标签/计数
    # ------------------------------------------------------------------
    def _update_month_label(self):
        y, m = self._active_month_view.current_month()
        self._month_label.setText(f"{y} 年 {m:02d} 月")

    def _update_count(self):
        try:
            con = open_default()
            y, m = self._active_month_view.current_month()
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
    # 皮肤 / 不透明度 / 置顶 / 动画开关
    # ------------------------------------------------------------------
    def _apply_skin(self) -> None:
        self.setStyleSheet(stylesheet_for(self._skin))
        # 状态栏用单独样式
        if hasattr(self, "_status_bar") and self._status_bar is not None:
            self._status_bar.setStyleSheet(
                f"QStatusBar {{ color: {self._skin.status_fg}; }}"
                f"QStatusBar > QLabel {{ color: {self._skin.status_fg}; }}"
            )
        # 月视图的格子配色是内联样式, 不会跟着主窗 QSS 走 — 得手动推
        for mv in (getattr(self, "_month_left", None),
                   getattr(self, "_month_right", None)):
            if mv is not None:
                mv.apply_skin(self._skin.name)
        # 工具栏按钮同理 — 各自有内联 QSS, 换皮肤得逐个重刷
        for btn, (accent, danger) in getattr(self, "_tool_meta", {}).items():
            btn.setStyleSheet(_tool_qss(self._skin, accent=accent, danger=danger))

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
        item("搜索全部条目…", "在所有日期的所有条目里找关键词",
             self._open_search, "Ctrl+F")
        item("生成日报 / 周报…", "预览、复制, 或导出成 MD / HTML / PDF",
             self._open_report, "Ctrl+R")
        item("用 AI 润色日报…", "调用 Claude 把日报改写得更通顺 "
                              "(需配置 ANTHROPIC_API_KEY)",
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

        section("帮助")
        item("快捷键与功能说明…", "所有功能和快捷键一览", self._open_help, "F1")
        item("关于 PyCal", "版本信息与数据存放位置", self._show_about)

        section("窗口")
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
        """AI 润色日报 — 没配 key 就直接给配置指引, 而不是开一个灰按钮的窗。"""
        from pycal.report.ai_polish import has_api_key, api_key_hint
        if not has_api_key():
            QMessageBox.information(
                self, "AI 日报 — 还差一步",
                "AI 润色需要一个 Anthropic API key。\n\n"
                + api_key_hint()
                + "\n\n设好之后重启 PyCal, 工具栏的「✨ AI 日报」就能用了。"
                "\n不配也不影响其它功能 —— 日报/周报照常能生成。",
            )
            return
        self._open_report(start_ai=True)

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
        """后台自动同步 — 无账号时静默跳过, 失败不打扰用户。"""
        try:
            from pycal.sync.accounts import list_accounts
            accs = [a for a in list_accounts() if a.enabled]
            if not accs:
                return
            from pycal.sync.engine import sync_all
            results = sync_all()
            total = sum(r.pulled + r.pushed for r in results)
            if total:
                self._count_label.setText(
                    f"{self._count_label.text()}  ☁ 同步 {total} 条"
                )
                self.refresh_view()
        except Exception:
            pass    # 后台任务不弹窗

    def _toggle_notes(self) -> None:
        from pycal.notes import NotesManager
        if not hasattr(self, "_notes_mgr") or self._notes_mgr is None:
            self._notes_mgr = NotesManager()
            self._notes_mgr.load()
        self._notes_mgr.create()

    def _quit_app(self) -> None:
        if getattr(self, "_tray", None) is not None:
            self._tray.hide()
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
        self.hide()
        if announce and self._has_tray():
            self._tray.showMessage(
                "PyCal 已收起到后台",
                "提醒和自动同步仍在运行。\n"
                "双击右下角托盘图标可以重新打开, 点托盘图标右键菜单的"
                "「退出 PyCal」可彻底结束。",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
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
# 工具函数
# ============================================================================
def _load_setting(key: str, default: str = "") -> str:
    try:
        cur = open_default().execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if cur is None:
            return default
        return cur["value"] or default
    except Exception:
        return default


def _save_setting(key: str, value: str) -> None:
    try:
        open_default().execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
            (key, value),
        )
    except Exception as e:
        print(f"[setting] save failed: {e}")


def _db_location() -> str:
    """当前数据库文件路径 — 「关于」窗口里给用户看的。"""
    try:
        from pycal.db.connection import default_db_path
        return str(default_db_path())
    except Exception:
        return "(未知)"


def _tool_qss(skin: Skin, danger: bool = False, accent: bool = False) -> str:
    """工具栏按钮的 QSS。

    普通按钮跟着皮肤走 (深色皮肤下不再是一排白药丸); accent/danger 是
    固定的强调色, 两种皮肤下都读得清。
    """
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
    return (
        f"QToolButton {{ border: 1px solid {border}; border-radius: 4px;"
        f" background: {normal}; {fg_rule} padding: 4px 8px; }}"
        f"QToolButton:hover {{ background: {hover}; border-color: #4A90E2; }}"
        f"QToolButton:pressed {{ background: {pressed}; }}"
        # 开关型按钮 (置顶/动画) 的选中态 — 由 setProperty("on", ...) 驱动
        f'QToolButton[on="true"] {{ background: #FFF3B0;'
        f" border: 1px solid #EFA020; }}"
    )


def _tool_btn(text: str, skin: Optional[Skin] = None,
              danger: bool = False, accent: bool = False) -> QToolButton:
    """工具栏按钮 — accent 给主功能上蓝底加粗, danger 给关闭上红底。"""
    b = QToolButton()
    b.setText(text)
    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    b.setStyleSheet(
        _tool_qss(skin or current_skin(), accent=accent, danger=danger)
    )
    b.setProperty("on", "false")
    return b


def _import_dialog() -> None:
    from pycal.importer import run_import
    import os
    default_src = (
        os.environ.get("APPDATA", "") + r"\CalendarTask\Db\calendar.db"
    )
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
    tray.setToolTip("PyCal — 桌面日历")
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
    act_search = QAction("搜索…", tray)
    act_search.triggered.connect(lambda: _search_dialog(win))
    menu.addAction(act_search)
    menu.addSeparator()
    act_quit = QAction("退出 PyCal", tray)
    def _quit():
        tray.hide()
        app.quit()
    act_quit.triggered.connect(_quit)
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: _show_hide()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
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
