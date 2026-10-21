"""色板选择器 — 复刻 DesktopCal 的 search_text_color_map + 颜色下拉。

提供两种用法:
  - 内联 22 色调色板 (颜色按钮 grid)
  - 最近使用 (recents, 最多 8 个)
  - 自定义颜色 (BGR / COLORREF) -> SystemColorDialog

widget 形态: ColorPickerButton + ColorPickerDropdown
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, Signal, QPoint, QSize
from PySide6.QtGui import QColor, QIcon, QPixmap, QPainter
from PySide6.QtWidgets import (
    QToolButton, QWidget, QGridLayout, QPushButton, QHBoxLayout, QLabel,
    QVBoxLayout, QFrame,
)

# 22 色调色板 (与 desktopcal_search.SEARCH_TEXT_COLOR_PALETTE 同步)
PALETTE: list[tuple[str, int]] = [
    (f"search_color_{i:02d}", rgb)
    for i, rgb in enumerate([
        0x9595FF, 0x0BBCF9, 0x78F0FB, 0x91FBCB, 0xF6CEB5,
        0xF9B1D7, 0xA8C9E6, 0x282828, 0x2424F1, 0x2168FF,
        0x07D1F5, 0x009300, 0xF43737, 0x81004A, 0xBAD261,
        0x44C465, 0x9D9490, 0xE9D9FF, 0xCEE3FA, 0xEBF0CD,
        0xFFE8D9, 0xF0CDD4,
    ], start=1)
]


def color_swatch(color: int, size: int = 22) -> QPixmap:
    """生成单个色块 pixmap。"""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(color)
    p.setBrush(c)
    p.setPen(QColor(40, 40, 40, 100))
    p.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    p.end()
    return pix


def color_to_hex(color: int) -> str:
    """BGR/COLORREF -> "#RRGGBB" for QSS。"""
    if not color:
        return "transparent"
    r = color & 0xFF
    g = (color >> 8) & 0xFF
    b = (color >> 16) & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"


class ColorPickerButton(QToolButton):
    """色板选择按钮 — 点击弹出色板下拉。

    current_color 改变时发射 color_changed (rgb int)
    """

    color_changed = Signal(int)

    def __init__(self, current_color: int = 0, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._current = current_color
        self.setFixedSize(28, 22)
        self._refresh()
        self.clicked.connect(self._open_dropdown)

    def set_color(self, color: int) -> None:
        self._current = color
        self._refresh()

    def color(self) -> int:
        return self._current

    def _refresh(self) -> None:
        if not self._current:
            self.setIcon(QIcon())  # 空色
            self.setToolTip("无颜色")
            return
        self.setIcon(QIcon(color_swatch(self._current)))
        self.setToolTip(f"0x{self._current:06X}")

    def _open_dropdown(self) -> None:
        dlg = ColorPickerDropdown(self._current, parent=self)
        if dlg.exec():
            self.set_color(dlg.selected_color())
            self.color_changed.emit(self._current)


class ColorPickerDropdown(QWidget):
    """24 个色块 (22 + null + palette 入口) 的弹窗。"""

    NullButtonIndex = 22     # "无颜色"
    PaletteButtonIndex = 23  # "更多..."

    def __init__(self, current: int = 0, parent: Optional[QWidget] = None):
        # 用 QDialog exec 语义
        from PySide6.QtWidgets import QDialog
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup)
        self._result_color = current
        self._setup(current)

    def _setup(self, current: int) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        title = QLabel("选择颜色")
        title.setStyleSheet("font-weight: bold; color: #444;")
        outer.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(4)
        # 22 色 + null + palette
        coords = [
            *[(i % 6, i // 6) for i in range(22)],   # 22 color
            (5, 3),                                   # null
            (0, 4),                                   # "更多" 文本按钮
        ]
        for i, (col, row) in enumerate(coords):
            if i < 22:
                _, rgb = PALETTE[i]
                btn = QPushButton()
                btn.setIcon(QIcon(color_swatch(rgb)))
                btn.setFixedSize(28, 22)
                btn.setToolTip(f"0x{rgb:06X}")
                btn.clicked.connect(lambda _=False, c=rgb: self._pick(c))
                if rgb == current:
                    btn.setStyleSheet("border: 2px solid #0066CC;")
                grid.addWidget(btn, row, col)
            elif i == self.NullButtonIndex:
                btn = QPushButton("×")
                btn.setFixedSize(28, 22)
                btn.setToolTip("无颜色")
                btn.clicked.connect(lambda: self._pick(0))
                grid.addWidget(btn, row, col)
            else:  # palette
                btn = QPushButton("更多…")
                btn.setFixedSize(120, 24)
                btn.clicked.connect(self._open_system)
                grid.addWidget(btn, row, 0, 1, 6)
                break
        outer.addLayout(grid)

        hint = QLabel("(设置文字颜色, 用于筛选 / 高亮)")
        hint.setStyleSheet("color: #888; font-size: 9pt;")
        outer.addWidget(hint)

    def _pick(self, color: int) -> None:
        self._result_color = color
        self.close()

    def _open_system(self) -> None:
        from PySide6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(
            QColor(self._result_color),
            self, "自定义颜色",
        )
        if c.isValid():
            color_rgb = (c.red() | (c.green() << 8) | (c.blue() << 16))
            self._pick(color_rgb)

    def exec(self) -> bool:
        """覆盖 exec 用 Popup 模式返回。"""
        from PySide6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        self.destroyed.connect(loop.quit)
        self.show()
        # 点击之外自动关闭 — 用 timer 兜底
        QTimer.singleShot(0, self._grab_focus)
        loop.exec()
        return self._result_color is not None

    def _grab_focus(self) -> None:
        self.setFocus()

    def selected_color(self) -> int:
        return self._result_color


__all__ = [
    "ColorPickerButton", "ColorPickerDropdown",
    "color_swatch", "color_to_hex", "PALETTE",
]
