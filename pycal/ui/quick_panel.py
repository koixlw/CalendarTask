"""快捷待办面板 — 点托盘图标弹出来的小窗口。

整个面板只干一件事: 随手记一条待办, 再看看今天还剩几件事没做。

跟主窗 / 桌面挂件的区别:
  * 窗口小 —— 就一张卡片那么大, 从托盘图标直接叫出来, 不用先开主窗
  * 整块卡片随便拖 —— 按住标题栏或者空白处都能挪, 位置记在 settings 里
  * 一直浮在最上面 (WindowStaysOnTopHint), 但不进任务栏 (Tool)
  * 写进去的是 type=0 的普通条目, 和月视图格子里的待办是**同一份数据** ——
    面板里勾了, 月视图那边也跟着打勾

面板一弹出来输入框就带焦点, 回车直接存 —— 随手记讲究的是不用鼠标。
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QToolButton, QVBoxLayout, QWidget,
)

from pycal.db.connection import open_default
from pycal.db.models import CalendarItem
from pycal.db.repository import get_item, list_items_by_date, upsert_item
from pycal.settings import get_setting, set_setting
from pycal.ui.fonts import clamp_scale, fonts_for, scale_from_setting
from pycal.ui.month_view import (
    LineRow, _toggle_line_in_content, rows_for_display,
)


# 卡片宽度 (px) —— 一张便签那么宽
PANEL_W = 320

# 待办列表最多占多高, 超出就滚动
MAX_LIST_H = 240

# 面板里的字号比主窗大一点 —— 窗口小, 字照主窗那样小就看不见了
PANEL_FONT_BOOST = 1.25


_QSS = """
#Card {
    background: rgba(252, 252, 253, 247);
    border: 1px solid rgba(0, 0, 0, 55);
    border-radius: 10px;
}
QLabel#Title {
    color: #2C3E50;
    font-size: 13px;
    font-weight: bold;
}
QLabel#Hint {
    color: #8A9099;
    font-size: 11px;
}
QLineEdit {
    background: #FFFFFF;
    border: 1px solid #D3D8E0;
    border-radius: 6px;
    padding: 5px 8px;
    color: #222222;
    font-size: 13px;
    selection-background-color: #CCE0FF;
}
QLineEdit:focus { border: 1px solid #0066CC; }
QPushButton#AddBtn {
    background: #0066CC;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 12px;
}
QPushButton#AddBtn:hover { background: #0055AA; }
QPushButton#AddBtn:pressed { background: #004488; }
QPushButton#LinkBtn {
    background: transparent;
    border: none;
    color: #0066CC;
    font-size: 12px;
    padding: 2px 4px;
}
QPushButton#LinkBtn:hover { color: #004488; }
QToolButton {
    background: transparent;
    border: none;
    color: #6B7280;
    font-size: 13px;
    padding: 1px 5px;
    border-radius: 4px;
}
QToolButton:hover { background: rgba(0, 0, 0, 25); color: #222222; }
"""


class QuickPanel(QWidget):
    """托盘弹出来的快捷待办小窗。"""

    sig_changed = Signal()        # 数据变了 → 主窗跟着刷
    sig_request_main = Signal()   # 「打开主窗口」

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._fonts = fonts_for(clamp_scale(
            scale_from_setting(get_setting("font_scale", "1.0"))
            * PANEL_FONT_BOOST
        ))
        self._drag_pos: Optional[QPoint] = None
        self._rows: list[LineRow] = []
        self._items: list[CalendarItem] = []
        # 刚记的几条 —— 排在列表最上面 (别让人翻半天找刚写的那条)
        self._recent: list[str] = []

        self.setWindowTitle("PyCal 快捷待办")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(PANEL_W)

        self._build()
        self.refresh()
        self._restore_geometry()
        self._install_shortcuts()

    # ------------------------------------------------------------------
    # 搭界面
    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("Card")
        card.setStyleSheet(_QSS)
        # 卡片本身不吃鼠标事件 —— 按在空白处的按下直接落到面板上, 就能拖窗口
        card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(8)

        # ── 标题栏 ──
        head = QHBoxLayout()
        head.setSpacing(4)
        self._title = QLabel("")
        self._title.setObjectName("Title")
        self._title.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        head.addWidget(self._title, 1)

        self._btn_all = self._tiny_btn("✓", "把今天没做完的都勾上")
        self._btn_close = self._tiny_btn("✕", "收起这个小窗 (Esc)")
        head.addWidget(self._btn_all)
        head.addWidget(self._btn_close)
        self._btn_all.clicked.connect(self._complete_all)
        self._btn_close.clicked.connect(self.hide)
        lay.addLayout(head)

        # ── 输入行 ──
        row = QHBoxLayout()
        row.setSpacing(6)
        self._input = QLineEdit()
        self._input.setPlaceholderText("记一条待办, 回车直接存")
        self._input.returnPressed.connect(self._add_todo)
        self._btn_add = QPushButton("＋ 添加")
        self._btn_add.setObjectName("AddBtn")
        self._btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add.clicked.connect(self._add_todo)
        row.addWidget(self._input, 1)
        row.addWidget(self._btn_add)
        lay.addLayout(row)

        # ── 今天的待办 ──
        self._empty = QLabel("今天还没记待办 —— 上面写一条吧")
        self._empty.setObjectName("Hint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self._empty)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_host = QWidget()
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(2)
        self._scroll.setWidget(self._list_host)
        # 列表的空白处也不吃鼠标 —— 照样能拖窗口
        self._scroll.viewport().setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self._scroll)

        # ── 底栏 ──
        foot = QHBoxLayout()
        foot.setSpacing(6)
        self._count = QLabel("")
        self._count.setObjectName("Hint")
        self._count.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._btn_main = QPushButton("打开主窗口")
        self._btn_main.setObjectName("LinkBtn")
        self._btn_main.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_main.clicked.connect(self.sig_request_main.emit)
        foot.addWidget(self._count, 1)
        foot.addWidget(self._btn_main)
        lay.addLayout(foot)

    def _tiny_btn(self, text: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """重新拉今天的数据, 重建行。"""
        try:
            items = list_items_by_date(open_default(),
                                       date.today().isoformat())
        except Exception as e:
            print(f"[QuickPanel] 读数据失败: {e}")
            items = []
        self._items = items

        # 刚记的那几条排最上面, 其余保持原来的顺序
        alive = {it.item_id for it in items}
        self._recent = [i for i in self._recent if i in alive]
        order = {iid: k for k, iid in enumerate(self._recent)}
        items = sorted(items, key=lambda it: order.get(it.item_id, len(order)))

        rows = rows_for_display(items)
        for i, (it, text, done, idx) in enumerate(rows):
            if i >= len(self._rows):
                row = LineRow(self._list_host, fonts=self._fonts)
                row.sig_toggled.connect(self._on_toggled)
                row.sig_open.connect(self._on_open)
                self._rows.append(row)
                self._list_lay.insertWidget(i, row)
            else:
                row = self._rows[i]
            row.bind(it.item_id, text, done, idx, "#222222", "#9AA0A6")
            row.setVisible(True)
        for row in self._rows[len(rows):]:
            row.setVisible(False)

        self._empty.setVisible(not rows)
        self._scroll.setVisible(bool(rows))
        self._fit_size(len(rows))
        self._update_title()
        self._update_count(rows)

    def _add_todo(self) -> None:
        """输入框里的字存成今天的一条待办。"""
        text = self._input.text().strip()
        if not text:
            return
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return
        item = CalendarItem(
            item_id=f"pycal-{uuid.uuid4().hex[:12]}",
            date=date.today().isoformat(),
            type=0,
            title="",
            content="\n".join(lines),
        )
        try:
            upsert_item(open_default(), item)
        except Exception as e:
            print(f"[QuickPanel] 保存失败: {e}")
            return
        self._recent.insert(0, item.item_id)
        self._input.clear()
        self.refresh()
        self.sig_changed.emit()
        self._input.setFocus()      # 接着写下一条

    def _on_toggled(self, item_id: str, line_idx, done: bool) -> None:
        """勾选写库 — line_idx=None 改 is_completed, 否则改 content 里的 [+]。"""
        con = open_default()
        it = get_item(con, item_id)
        if it is None:
            self.refresh()
            return
        try:
            if line_idx is None:
                it.is_completed = 1 if done else 0
            else:
                it.content = _toggle_line_in_content(it.content, line_idx, done)
            upsert_item(con, it)
        except Exception as e:
            print(f"[QuickPanel] 勾选存库失败: {e}")
            self.refresh()          # 回滚 UI
            return
        self._update_count()
        self.sig_changed.emit()

    def _on_open(self, item_id: str, line_idx) -> None:
        """双击某一行 → 打开条目编辑器。"""
        from pycal.ui.editor import ItemEditorDialog
        from pycal.ui.main_window import _save_item
        it = get_item(open_default(), item_id)
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
            self.refresh()
            self.sig_changed.emit()

    def _complete_all(self) -> None:
        """把今天没做完的都勾上。"""
        con = open_default()
        changed = False
        for it, _text, done, idx in rows_for_display(self._items):
            if done:
                continue
            cur = get_item(con, it.item_id)
            if cur is None:
                continue
            if idx is None:
                cur.is_completed = 1
            else:
                cur.content = _toggle_line_in_content(cur.content, idx, True)
            upsert_item(con, cur)
            changed = True
        if not changed:
            return
        self.refresh()
        self.sig_changed.emit()

    # ------------------------------------------------------------------
    # 标题 / 计数
    # ------------------------------------------------------------------
    def _update_title(self) -> None:
        today = date.today()
        wd = "一二三四五六日"[today.weekday()]
        text = f"{today.year}年{today.month}月{today.day}日 星期{wd}"
        try:
            from pycal.lunar import lunar_for
            lunar = lunar_for(today) or ""
        except Exception:
            lunar = ""
        if lunar:
            text += f" {lunar}"
        self._title.setText(text)

    def _update_count(self, rows=None) -> None:
        if rows is None:
            rows = rows_for_display(self._items)
        total = len(rows)
        pending = sum(1 for r in rows if not r[2])
        if total == 0:
            self._count.setText("今天还没记待办")
        elif pending == 0:
            self._count.setText(f"今天 {total} 项都做完了")
        else:
            self._count.setText(f"还剩 {pending} 项待办")

    def _fit_size(self, row_count: int = 0) -> None:
        """窗口高度跟着内容走 —— 列表再长也不超过 MAX_LIST_H。

        行高按字号表算, 不能问控件要 —— 面板还没显示的时候
        isVisible() 一律是 False, 拿它算高度会算出 0, 列表就剩一条缝。
        """
        need = row_count * self._fonts.row_h
        need += self._list_lay.spacing() * max(0, row_count - 1)
        self._scroll.setFixedHeight(min(MAX_LIST_H, max(0, need)))
        self.setFixedHeight(self.layout().sizeHint().height())

    # ------------------------------------------------------------------
    # 位置 / 拖动
    # ------------------------------------------------------------------
    def _restore_geometry(self) -> None:
        x = get_setting("quick_panel_x", "")
        y = get_setting("quick_panel_y", "")
        if x and y:
            try:
                self.move(int(x), int(y))
                self._keep_on_screen()
                return
            except ValueError:
                pass
        self._move_to_corner()

    def _move_to_corner(self) -> None:
        """第一次打开 → 贴在主屏右下角 (托盘图标上面)。"""
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        if geo is None:
            return
        margin = 16
        self.move(geo.right() - self.width() - margin + 1,
                  geo.bottom() - self.height() - margin + 1)

    def _keep_on_screen(self) -> None:
        """别把面板拖到屏幕外面找不着了 —— 至少让标题栏留在屏幕上。

        故意留一半在屏幕外是可以的 (有人就喜欢把挂件贴在边上),
        但标题栏要是整个跑到屏幕外面, 就再也抓不回来了。
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        if geo is None:
            return
        x = min(max(self.x(), geo.left()), max(geo.left(), geo.right() - 80))
        y = min(max(self.y(), geo.top()), max(geo.top(), geo.bottom() - 30))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _save_geometry(self) -> None:
        g = self.geometry()
        set_setting("quick_panel_x", str(g.x()))
        set_setting("quick_panel_y", str(g.y()))

    def mousePressEvent(self, ev):
        # 卡片空白处的按下会落到面板上 —— 从这儿开始拖窗口
        if ev.button() == Qt.MouseButton.LeftButton:
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
            self._keep_on_screen()    # 别拖到屏幕外面找不着了
            self._save_geometry()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    # ------------------------------------------------------------------
    # 杂项
    # ------------------------------------------------------------------
    def _install_shortcuts(self) -> None:
        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc.activated.connect(self._on_escape)

    def _on_escape(self) -> None:
        """Esc 先清空输入框, 输入框空了才收起面板。"""
        if self._input.text():
            self._input.clear()
            self._input.setFocus()
            return
        self.hide()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._keep_on_screen()
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)


class QuickPanelManager:
    """快捷面板的生命周期 —— 托盘图标和主窗菜单都通过它开关。"""

    def __init__(self, main_window=None):
        self._main = main_window
        self._panel: Optional[QuickPanel] = None

    @property
    def panel(self) -> Optional[QuickPanel]:
        return self._panel

    def visible(self) -> bool:
        return self._panel is not None and self._panel.isVisible()

    def toggle(self) -> None:
        if self.visible():
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        if self._panel is None:
            self._panel = QuickPanel()
            self._panel.sig_changed.connect(self._on_changed)
            self._panel.sig_request_main.connect(self._open_main)
        self._panel.refresh()
        self._panel.show()
        self._panel.raise_()
        self._panel.activateWindow()
        self._panel._input.setFocus()

    def hide(self) -> None:
        if self._panel is not None:
            self._panel.hide()

    # ------------------------------------------------------------------
    def _on_changed(self) -> None:
        """面板里改了数据 → 主窗那边跟着刷 (月视图格子要同步打勾)。"""
        if self._main is not None and hasattr(self._main, "refresh_view"):
            self._main.refresh_view()

    def _open_main(self) -> None:
        if self._main is None:
            return
        self._main.showNormal()
        self._main.raise_()
        self._main.activateWindow()


__all__ = ["QuickPanel", "QuickPanelManager", "PANEL_W", "MAX_LIST_H"]
