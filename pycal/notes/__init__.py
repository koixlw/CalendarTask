"""便签模块 — 桌面浮贴纸 + 富文本编辑。"""

from __future__ import annotations
import sys
from typing import Optional, Sequence

from PySide6.QtCore import Qt, QPoint, QSize, Signal
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QFontDatabase
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QHBoxLayout, QPushButton, QLabel,
    QMainWindow, QApplication, QInputDialog, QColorDialog,
)

from pycal.db.connection import open_default
from pycal.db.repository import (
    list_notes, upsert_note, soft_delete_note,
)
from pycal.db.models import Note

# 五种便签背景色 (浅色系)
NOTE_PALETTE = [
    "#FFF8B0",  # 黄
    "#FFCCD5",  # 粉
    "#CDEAC0",  # 绿
    "#C5E0F5",  # 蓝
    "#E0D6F5",  # 紫
]


class NoteWidget(QWidget):
    """单个桌面便签。"""

    deleted = Signal(int)        # note_id
    persist_requested = Signal()

    def __init__(self, note: Note, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._note = note
        self._build()

    def _build(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 颜色映射
        bg = NOTE_PALETTE[min(self._note.color_ref, len(NOTE_PALETTE) - 1)]
        shadow = "QWidget { background: rgba(0,0,0,30); }"
        self.setStyleSheet(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        card = QWidget()
        card.setStyleSheet(
            f"QWidget#card {{ background: {bg}; border-radius: 6px; }}"
        )
        card.setObjectName("card")
        outer.addWidget(card)

        lay = QVBoxLayout(card)

        # 顶部工具条: 标题 + 关闭 + 颜色 + 置顶
        bar = QHBoxLayout()
        self._title = QLabel(self._note.title or "便签")
        self._title.setStyleSheet("font-weight: bold;")
        bar.addWidget(self._title)
        bar.addStretch()

        btn_pin = QPushButton("📌")
        btn_pin.setFixedWidth(30)
        btn_pin.clicked.connect(self._toggle_pin)
        bar.addWidget(btn_pin)

        btn_color = QPushButton("🎨")
        btn_color.setFixedWidth(30)
        btn_color.clicked.connect(self._pick_color)
        bar.addWidget(btn_color)

        btn_close = QPushButton("✕")
        btn_close.setFixedWidth(30)
        btn_close.clicked.connect(self._close)
        bar.addWidget(btn_close)

        lay.addLayout(bar)

        # 内容
        self._edit = QTextEdit()
        self._edit.setPlainText(self._note.content)
        self._edit.setStyleSheet(
            "QTextEdit { background: transparent; border: none; }"
        )
        font = QFont()
        font.setPointSize(11)
        self._edit.setFont(font)
        self._edit.textChanged.connect(self._on_text_changed)
        lay.addWidget(self._edit)

        self.resize(self._note.width, self._note.height)
        self.move(self._note.pos_x, self._note.pos_y)

    def _on_text_changed(self) -> None:
        self._note.content = self._edit.toPlainText()
        self.persist_requested.emit()

    def _toggle_pin(self) -> None:
        self._note.pinned = 0 if self._note.pinned else 1
        self.persist_requested.emit()

    def _pick_color(self) -> None:
        c = QColorDialog.getColor()
        if not c.isValid():
            return
        # 近似匹配 palette index
        r, g, b = c.red(), c.green(), c.blue()
        # 取最接近的预设
        best_i = 0
        best_d = 1e18
        for i, hex_str in enumerate(NOTE_PALETTE):
            pr = int(hex_str[1:3], 16)
            pg = int(hex_str[3:5], 16)
            pb = int(hex_str[5:7], 16)
            d = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
            if d < best_d:
                best_d = d
                best_i = i
        self._note.color_ref = best_i
        self._build()  # 重建以应用新配色
        self._edit.setPlainText(self._note.content)
        self.persist_requested.emit()

    def _close(self) -> None:
        nid = self._note.note_id or -1
        con = open_default()
        if nid > 0:
            soft_delete_note(con, nid)
        self.deleted.emit(nid)
        self.close()


class NotesManager:
    """便签桌面管理器。"""

    def __init__(self):
        self._notes: list[NoteWidget] = []

    def load(self) -> None:
        con = open_default()
        for n in list_notes(con):
            self._spawn(n)

    def _spawn(self, note: Note) -> None:
        w = NoteWidget(note)
        w.persist_requested.connect(lambda: self._save(w))
        w.show()
        self._notes.append(w)

    def _save(self, w: NoteWidget) -> None:
        geom = w.geometry()
        w._note.pos_x = geom.left()
        w._note.pos_y = geom.top()
        w._note.width = geom.width()
        w._note.height = geom.height()
        con = open_default()
        upsert_note(con, w._note)

    def create(self) -> None:
        n = Note(title="便签", content="")
        self._spawn(n)
        self._save(self._notes[-1])


__all__ = ["NoteWidget", "NotesManager", "NOTE_PALETTE"]
