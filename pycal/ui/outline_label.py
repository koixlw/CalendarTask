"""描边文字 — 压在花花绿绿的背景上也看得清。

QLabel 直接画字: 遇到浅色背景配浅色字就糊成一片。格子不填底色的那种
模式 (背景直接透出来), 字必须自带一圈半透明描边 (halo) 才认得出。

没设描边时 paintEvent 原样交给 QLabel —— 主窗那几张皮肤一个字都不用改,
行为跟以前完全一样。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import (
    QColor, QFontMetrics, QPainter, QPalette,
)
from PySide6.QtWidgets import QLabel


# 描边的八个方向 (乘上半径)
_HALO_OFFSETS = (
    (-1, 0), (1, 0), (0, -1), (0, 1),
    (-0.7, -0.7), (0.7, -0.7), (-0.7, 0.7), (0.7, 0.7),
)


class HaloLabel(QLabel):
    """带描边的标签。

    - ``setHalo("")`` → 不描边, 跟普通 QLabel 一模一样
    - ``setInk("#FFFFFF")`` → 指定字色 (不给就用样式表/调色板里的颜色)
    """

    def __init__(self, text: str = "", parent=None,
                 halo: str = "", halo_width: float = 2.4):
        # 参数顺序跟 QLabel 一致 (第一个是文字, 不是 parent) ——
        # 跟 QLabel 混着用才不别扭
        super().__init__(text, parent)
        self._halo = ""
        self._halo_width = halo_width
        self._ink: Optional[str] = None
        if halo:
            self.setHalo(halo, halo_width)

    # ------------------------------------------------------------------
    # 外观
    # ------------------------------------------------------------------
    def setHalo(self, color: str, width: float = 2.4) -> None:
        """描边色 — 空字符串 = 不描边。"""
        self._halo = color or ""
        self._halo_width = width
        self.update()

    def halo(self) -> str:
        return self._halo

    def setInk(self, color: Optional[str]) -> None:
        """指定字色 — None = 用样式表/调色板里的颜色。"""
        self._ink = color
        self.update()

    def ink(self) -> Optional[str]:
        return self._ink

    # ------------------------------------------------------------------
    # 画字
    # ------------------------------------------------------------------
    def _text_color(self) -> QColor:
        if self._ink:
            return QColor(self._ink)
        return self.palette().color(QPalette.ColorRole.WindowText)

    def paintEvent(self, ev) -> None:
        if not self._halo:
            super().paintEvent(ev)      # 没描边就走 Qt 原版
            return
        text = self.text()
        if not text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        # 裁到内容区 —— 格子窄的时候字比标签宽, 不裁就会淌到隔壁格子上
        p.setClipRect(self.contentsRect())
        # ⚠ 画笔默认用的是「应用程序」字体, 不是控件自己的 ——
        #   不显式设一遍, 字号和字形全都不对。
        font = self.font()
        p.setFont(font)
        fm = QFontMetrics(font)
        rect = self.contentsRect()
        align = self.alignment()

        # 横向摆位 —— QLabel 的几种对齐都认
        width = fm.horizontalAdvance(text)
        if align & Qt.AlignmentFlag.AlignHCenter:
            x = rect.x() + (rect.width() - width) / 2
        elif align & Qt.AlignmentFlag.AlignRight:
            x = rect.x() + rect.width() - width
        else:
            x = rect.x()

        # 纵向摆位 —— y 是基线
        if align & Qt.AlignmentFlag.AlignVCenter:
            y = rect.y() + (rect.height() + fm.ascent() - fm.descent()) / 2
        elif align & Qt.AlignmentFlag.AlignBottom:
            y = rect.y() + rect.height() - fm.descent()
        else:
            y = rect.y() + fm.ascent()

        # 描边: 八个方向各描一遍, 再把正片盖上去。
        # 不用 QPainterPath.addText —— 那条路要拿字体轮廓, 某些环境里
        # 取不到就画成一坨实心方块 (Windows 离屏模式就这毛病)。
        r = max(0.6, self._halo_width / 2)
        p.setPen(QColor(self._halo))
        for dx, dy in _HALO_OFFSETS:
            p.drawText(QPointF(x + dx * r, y + dy * r), text)
        p.setPen(self._text_color())
        p.drawText(QPointF(x, y), text)
        p.end()


__all__ = ["HaloLabel"]
