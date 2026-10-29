"""FramelessWindow 的拖动 / resize 基类。

工作原理:
  PySide6 FramelessWindowHint 下, 系统不再处理窗口拖动/边缘 resize,
  我们手动接管 mousePress/Move/Release.

支持:
  - 标题栏 (drag_area) 区域拖动
  - 八向 resize 边缘 (左/右/上/下 + 四角)
  - 鼠标 hover 在边缘时显示对应 cursor
  - 双击标题栏 (最大化 / 还原)
  - escape 关闭 (QShortcut)
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, QRect, QPoint, QSize, QEvent
from PySide6.QtGui import (
    QMouseEvent, QCursor, QKeySequence, QShortcut,
)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QApplication, QAbstractButton, QAbstractSpinBox,
    QComboBox, QLineEdit, QAbstractScrollArea, QTextEdit, QSlider,
)


# 落在这些控件上的鼠标按下必须放行给控件自己处理 — 否则工具栏的按钮
# 全都点不动 (press 被拖动逻辑吃掉, 按钮永远收不到, clicked 不会触发)。
_INTERACTIVE = (
    QAbstractButton,      # QToolButton / QPushButton / QCheckBox …
    QLineEdit,
    QAbstractSpinBox,
    QComboBox,
    QSlider,
    QAbstractScrollArea,  # QTextEdit / QPlainTextEdit 的基类
    QTextEdit,
)


# 八向光标对应表
_RESIZE_CURSORS = {
    "n":   Qt.CursorShape.SizeVerCursor,
    "s":   Qt.CursorShape.SizeVerCursor,
    "w":   Qt.CursorShape.SizeHorCursor,
    "e":   Qt.CursorShape.SizeHorCursor,
    "nw":  Qt.CursorShape.SizeFDiagCursor,
    "se":  Qt.CursorShape.SizeFDiagCursor,
    "ne":  Qt.CursorShape.SizeBDiagCursor,
    "sw":  Qt.CursorShape.SizeBDiagCursor,
}


class FloatingWindow(QMainWindow):
    """Frameless + 可拖动 + 可 resize 主窗。

    子类用法:
        class MyWindow(FloatingWindow):
            def __init__(self):
                super().__init__()
                # 创建标题栏 widget, set_drag_area(title_widget)
                # 创建中央 widget
                ...
    """

    EDGE_MARGIN = 8   # 边缘 resize 触发宽度 (无边框窗口, 边太窄不好抓)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._drag_pos: Optional[QPoint] = None
        self._resize_dir: Optional[str] = None
        self._drag_area: Optional[QWidget] = None
        self._drag_area_height: int = 36
        self._maximized_rect: Optional[QRect] = None
        self.setMouseTracking(True)
        self._install_escape()

    # ------------------------------------------------------------------
    # 子类 API
    # ------------------------------------------------------------------
    def set_drag_area(self, widget: QWidget, height: int = 36) -> None:
        """指定拖动区域 (通常是顶部工具栏 widget)。

        height 仅在 widget.height() 动态变化时, 让 hit-test 不可靠时的兜底。
        """
        self._drag_area = widget
        self._drag_area_height = height
        widget.setMouseTracking(True)
        # 用事件过滤器把 mousePressEvent 重定向给主窗
        widget.installEventFilter(self)
        # 找其中所有 child 也都装, 防止 button 等拦截事件
        for child in widget.findChildren(QWidget):
            child.setMouseTracking(True)
            child.installEventFilter(self)

    def _is_interactive(self, widget: QWidget) -> bool:
        """控件自己要不要处理这次点击 (按钮 / 输入框 / 滚动区 …)。"""
        w = widget
        while w is not None:
            if isinstance(w, _INTERACTIVE):
                return True
            w = w.parentWidget()
        return False

    def _begin_drag(self, global_pos: QPoint) -> None:
        """起一次窗口拖动 — grabMouse 让后续 move/release 都进主窗。"""
        self._drag_pos = global_pos - self.frameGeometry().topLeft()
        self._resize_dir = None
        try:
            self.grabMouse()
        except Exception:
            pass

    def eventFilter(self, obj, event):
        """拖动区域里只有「空白处」才当作标题栏拖动。

        按钮/输入框上的 press 必须原样放行 —— 之前这里一律 return True,
        结果工具栏 12 个按钮全部点不动。
        """
        if event.type() == QEvent.Type.MouseButtonPress and \
                event.button() == Qt.MouseButton.LeftButton:
            area = self._drag_area
            in_area = area is not None and (
                obj is area or area.isAncestorOf(obj))
            if in_area and not self._is_interactive(obj):
                pos_in_window = obj.mapTo(self, event.position().toPoint())
                if pos_in_window.y() <= self._drag_area_height:
                    self._begin_drag(event.globalPosition().toPoint())
                    return True   # 阻断原本的事件
        return super().eventFilter(obj, event)

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            if self._maximized_rect:
                self.setGeometry(self._maximized_rect)
            self.showNormal()
        else:
            self._maximized_rect = self.geometry()
            screen = QApplication.primaryScreen().geometry()
            self.setGeometry(screen)
            self.showMaximized()

    # ------------------------------------------------------------------
    # 鼠标事件
    # ------------------------------------------------------------------
    def mouseMoveEvent(self, event: QMouseEvent):
        # 按钮按下
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos:
            global_pos = event.globalPosition().toPoint()
            if self._resize_dir:
                self._perform_resize(global_pos)
            else:
                self.move(global_pos - self._drag_pos)
            event.accept()
            return
        # 无按钮按下, 更新 resize cursor
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            direction = self._hit_test_edge(event.position().toPoint())
            if direction:
                self.setCursor(QCursor(_RESIZE_CURSORS[direction]))
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            global_pos = event.globalPosition().toPoint()
            local_pos = event.position().toPoint()
            # 1) 边缘 resize — 先判。
            #    不先判的话, 工具栏 (占着最上面 36px) 的拖动区会把顶边和
            #    两个上角全抢走, 那三个方向根本没法用来缩放。
            direction = self._hit_test_edge(local_pos)
            if direction:
                self._drag_pos = global_pos
                self._resize_dir = direction
                try:
                    self.grabMouse()
                except Exception:
                    pass
                event.accept()
                return
            # 2) 标题栏拖动
            if self._drag_area and local_pos.y() <= self._drag_area_height:
                self._begin_drag(global_pos)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._drag_pos or self._resize_dir:
                self._drag_pos = None
                self._resize_dir = None
                self.unsetCursor()
                try:
                    self.releaseMouse()
                except Exception:
                    pass
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        # 双击标题栏 = 最大化 / 还原
        if event.button() == Qt.MouseButton.LeftButton and \
                event.position().toPoint().y() <= self._drag_area_height:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # resize 逻辑
    # ------------------------------------------------------------------
    def _hit_test_edge(self, pos: QPoint) -> Optional[str]:
        w, h = self.width(), self.height()
        m = self.EDGE_MARGIN
        x, y = pos.x(), pos.y()
        if x < 0 or x > w or y < 0 or y > h:
            return None
        on_left = x <= m
        on_right = x >= w - m
        on_top = y <= m
        on_bot = y >= h - m
        if on_top and on_left:
            return "nw"
        if on_top and on_right:
            return "ne"
        if on_bot and on_left:
            return "sw"
        if on_bot and on_right:
            return "se"
        if on_top:
            return "n"
        if on_bot:
            return "s"
        if on_left:
            return "w"
        if on_right:
            return "e"
        return None

    def _perform_resize(self, global_pos: QPoint) -> None:
        """按 _resize_dir 方向缩放到 global_pos。

        四条边 / 四个角一次算清楚再摆 —— 之前是一条边一条边地摆, 后摆的
        会把先摆的结果盖掉 (西北角拖完宽度又弹回原样)。
        """
        rect = self.geometry()
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        delta = global_pos - self._drag_pos
        MIN_W, MIN_H = 360, 280
        if "e" in self._resize_dir:
            w = max(MIN_W, w + delta.x())
        if "s" in self._resize_dir:
            h = max(MIN_H, h + delta.y())
        if "w" in self._resize_dir:
            w = max(MIN_W, w - delta.x())
            x = x + (rect.width() - w)      # 左边界往里收, 右边不动
        if "n" in self._resize_dir:
            h = max(MIN_H, h - delta.y())
            y = y + (rect.height() - h)     # 上边界往里收, 下边不动
        self._apply_resize_geometry(QRect(x, y, w, h))
        # 更新 _drag_pos 为"当前差值"
        self._drag_pos = global_pos

    def _apply_resize_geometry(self, rect: QRect) -> None:
        """真正把窗口摆到新的位置和大小。

        单独拎出来一个口子 —— 子类可以在摆之前先做「尺寸相关的调整」。
        (主窗要先按目标宽度把工具栏切成紧凑模式, 否则布局的最小宽度
        会把窗口卡在那儿, 往里拖边框根本收不动。)
        """
        self.setGeometry(rect)

    # ------------------------------------------------------------------
    # Esc 关闭
    # ------------------------------------------------------------------
    def _install_escape(self) -> None:
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc_shortcut.activated.connect(self._on_escape)

    def _on_escape(self) -> None:
        """默认收起窗口。子类可覆盖成走自己的 close 流程。"""
        self.hide()


__all__ = ["FloatingWindow"]
