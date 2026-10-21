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
import sys
from datetime import date
from typing import Optional

from PySide6.QtCore import (
    Qt, QPoint, Signal, QSize, QPropertyAnimation, QEasingCurve,
    QParallelAnimationGroup, QAbstractAnimation, QRect, QTimer,
)
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPixmap, QPainter
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

        self._btn_prev = _tool_btn("◀")
        self._month_label = QLabel()
        self._month_label.setProperty("role", "month_label")
        self._month_label.setMinimumWidth(140)
        self._month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._btn_next = _tool_btn("▶")
        self._btn_today = _tool_btn("今天")
        for b in (self._btn_prev, self._month_label, self._btn_next, self._btn_today):
            bar_layout.addWidget(b)
        bar_layout.addSpacing(8)

        bar_layout.addStretch()
        self._btn_anim = _tool_btn("🎬")  # 月份切换动画开关
        self._btn_skin = _tool_btn("🎨")
        self._btn_top = _tool_btn("📌")
        self._btn_refresh = _tool_btn("🔄")
        self._btn_sync = _tool_btn("☁")     # 云同步
        self._btn_search = _tool_btn("🔍")
        self._btn_notes = _tool_btn("📝")
        self._btn_wall = _tool_btn("🌄")
        self._btn_import = _tool_btn("📥")
        self._btn_min = _tool_btn("—")
        self._btn_exit = _tool_btn("×", danger=True)

        for b in (self._btn_anim, self._btn_skin, self._btn_top, self._btn_refresh,
                  self._btn_sync, self._btn_search, self._btn_notes,
                  self._btn_wall, self._btn_import, self._btn_min, self._btn_exit):
            bar_layout.addWidget(b)

        self._top_layout.addWidget(bar)
        # 把整个工具栏作为拖动区域
        self.set_drag_area(bar, height=36)

        # 月视图 (放在一个 QStackedWidget 里方便做切换动画)
        from PySide6.QtWidgets import QStackedWidget
        self._stack = QStackedWidget()
        self._month_left = MonthView()
        self._month_right = MonthView()
        # 两层用于 slide-in / slide-out
        self._stack.addWidget(self._month_left)
        self._stack.addWidget(self._month_right)
        self._stack.setCurrentIndex(0)
        # 当前月份初始设定相同
        self._active_month_view = self._month_left
        self._idle_month_view = self._month_right
        self._top_layout.addWidget(self._stack, 1)

        # 状态栏
        status = QStatusBar()
        self._count_label = QLabel("0 项")
        self._opacity_label = QLabel("100%")
        self._anim_label = QLabel("动画 开")
        status.addPermanentWidget(self._count_label)
        status.addPermanentWidget(self._anim_label)
        status.addPermanentWidget(self._opacity_label)
        self.setStatusBar(status)
        self._status_bar = status

        # 信号
        self._btn_prev.clicked.connect(lambda: self.shift_month(-1))
        self._btn_next.clicked.connect(lambda: self.shift_month(+1))
        self._btn_today.clicked.connect(self.goto_today)
        self._btn_refresh.clicked.connect(self.refresh_view)
        self._btn_sync.clicked.connect(self._open_sync)
        self._btn_search.clicked.connect(self._open_search)
        self._btn_import.clicked.connect(_import_dialog)
        self._btn_notes.clicked.connect(self._toggle_notes)
        self._btn_wall.clicked.connect(lambda: _set_wallpaper(self))
        self._btn_skin.clicked.connect(self._show_skin_menu)
        self._btn_anim.clicked.connect(self._toggle_animation)
        self._btn_top.clicked.connect(self._toggle_topmost)
        self._btn_min.clicked.connect(self.hide)
        self._btn_exit.clicked.connect(self._quit_app)

        # 初始化
        self._update_month_label()
        self._update_count()
        self._apply_topmost()
        self._apply_opacity()

        # 提示
        for b, tip in [
            (self._btn_anim, "月份切换动画 (开 / 关)"),
            (self._btn_skin, "切换皮肤"),
            (self._btn_top, "窗口置顶"),
            (self._btn_sync, "云同步 (Google / iCloud / Outlook)"),
            (self._btn_min, "最小化到托盘"),
        ]:
            b.setToolTip(tip)

        # 云同步: 后台定时任务 (每 15 分钟, 仅当有账号)
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._auto_sync_tick)
        self._sync_timer.start(15 * 60 * 1000)
        QTimer.singleShot(10_000, self._auto_sync_tick)   # 启动 10s 后首跑

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

    def _show_skin_menu(self) -> None:
        menu = QMenu(self)
        for name, skin in SKINS.items():
            label = {
                "light_default": "浅色 — 默认",
                "warm_paper": "暖色 — 米黄",
                "dark_mode": "深色 — 夜间",
                "glass_blur": "玻璃 — 磨砂",
            }[name]
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._skin.name == name)
            act.triggered.connect(
                lambda _=False, n=name: self._set_skin(n)
            )
        # 不透明度滑块
        menu.addSeparator()
        op_menu = menu.addMenu("不透明度")
        for pct in (60, 75, 90, 100):
            act = op_menu.addAction(f"{pct}%")
            act.setCheckable(True)
            act.setChecked(
                abs(self._opacity * 100 - pct) < 1
            )
            act.triggered.connect(
                lambda _=False, p=pct: self._set_opacity(p / 100)
            )
        menu.exec(self.mapToGlobal(QPoint(self._btn_skin.x(),
                                          self._btn_skin.y() + self._btn_skin.height())))

    def _set_skin(self, name: str) -> None:
        self._skin = current_skin(name)
        _save_setting("skin", name)
        self._apply_skin()

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
        self.setWindowFlags(flags)
        self.show()  # 重新设置 flags 后需要 show()
        self._btn_top.setStyleSheet(
            "QToolButton { background: #FFF8B0; border: 2px solid #EFA020; }"
            if self._topmost else
            "QToolButton { background: white; }"
        )

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
        if hasattr(self, "_tray") and self._tray is not None:
            self._tray.hide()
        self._app.quit()

    # ------------------------------------------------------------------
    # 关闭: 隐藏到托盘
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        if hasattr(self, "_tray") and self._tray is not None:
            event.ignore()
            self.hide()


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


def _tool_btn(text: str, danger: bool = False) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    base_qss = (
        "QToolButton { border: 1px solid #BBB; border-radius: 4px;"
        " background: white; padding: 4px 8px; }"
        "QToolButton:hover { background: #EAF3FF; border-color: #4A90E2; }"
        "QToolButton:pressed { background: #CCE0FF; }"
    )
    if danger:
        base_qss = base_qss.replace(
            "background: white;",
            "background: #FFE5E5; color: #C0392B;",
        )
    b.setStyleSheet(base_qss)
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


def _build_tray(win: QMainWindow, app: QApplication) -> QSystemTrayIcon:
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return QSystemTrayIcon(win)
    app.setWindowIcon(_make_app_icon())
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


def _build_reminder_worker(tray: QSystemTrayIcon):
    from pycal.notifier import ReminderWorker
    worker = ReminderWorker()
    if QSystemTrayIcon.isSystemTrayAvailable():
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
