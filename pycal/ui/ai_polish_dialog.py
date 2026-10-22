"""AI 润色对话框。

布局:
  ┌─ 顶部: 模型选择 + [测试连接] ────────────────────┐
  ├─ 风格: [x]简洁精炼 [x]专业正式 [ ]轻松友好 [x]复盘分析 ─┤
  ├─ 补充说明: [____________________]                 ┤
  ├─ 原始 (可折叠) ────╮                              │
  │                   │ 对比视图 (Tab)                  │
  ├─ 润色结果 (流式)  ─╯                              │
  ├─ 状态栏: 模型 / tokens / 成本                     │
  └─ 底部: [取消] [开始润色] [保存MD] [导出HTML] [复制]  │
"""

from __future__ import annotations

import sys
import webbrowser
from datetime import date
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QCheckBox, QTextEdit, QTextBrowser, QMessageBox, QFileDialog,
    QGroupBox, QProgressBar, QSplitter, QWidget, QPlainTextEdit,
    QApplication, QTabWidget,
)

from pycal.report.ai_polish import (
    STYLES, PolishError, ClaudePolishClient, has_api_key, api_key_hint,
    MODEL_DEFAULT, MODEL_FAST, MODEL_SMART,
)


class PolishWorker(QThread):
    """后台流式润色。"""

    chunk = Signal(str)          # 文本增量
    done = Signal(str, int, int, str)   # 全文, in_tokens, out_tokens, model
    failed = Signal(str, str)    # message, hint

    def __init__(self, client: ClaudePolishClient, report_md: str,
                 styles: list[str], kind: str, notes: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._report = report_md
        self._styles = styles
        self._kind = kind
        self._notes = notes

    def run(self):
        try:
            acc: list[str] = []
            for piece in self._client.polish_stream(
                self._report, self._styles, self._kind, self._notes
            ):
                acc.append(piece)
                self.chunk.emit(piece)
            text = "".join(acc)
            # 流式拿不到精确 usage, 粗估 ~4 chars/token
            est_out = len(text) // 4
            est_in = len(self._report) // 4 + 400
            self.done.emit(text, est_in, est_out, self._client.model)
        except PolishError as e:
            self.failed.emit(e.message, e.hint)
        except Exception as e:
            self.failed.emit(f"未预期错误: {str(e)[:200]}", "")


class PolishDialog(QDialog):
    """AI 润色对话框。"""

    def __init__(self, report_md: str, kind: str = "日报", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"PyCal — AI 润色{kind}")
        self.resize(960, 760)
        self._report_md = report_md
        self._kind = kind
        self._result = ""
        self._worker: Optional[PolishWorker] = None
        self._build()
        self._check_key()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)

        # ── 顶部: 模型 + 连接测试 ──
        top = QHBoxLayout()
        top.addWidget(QLabel("模型:"))
        self._model = QComboBox()
        self._model.addItem("Claude Sonnet 5 — 质量优先 (推荐)", MODEL_DEFAULT)
        self._model.addItem("Claude Haiku 4.5 — 最快最便宜", MODEL_FAST)
        self._model.addItem("Claude Opus 5.5 — 深度分析", MODEL_SMART)
        self._model.setCurrentIndex(0)
        top.addWidget(self._model, 1)
        self._btn_test = QPushButton("🔌 测试连接")
        self._btn_test.clicked.connect(self._test_connection)
        top.addWidget(self._btn_test)
        root.addLayout(top)

        self._key_label = QLabel("")
        self._key_label.setWordWrap(True)
        self._key_label.setStyleSheet(
            "padding: 6px; border-radius: 4px;"
        )
        root.addWidget(self._key_label)

        # ── 风格 ──
        style_box = QGroupBox("润色风格 (可多选)")
        s_layout = QVBoxLayout(style_box)
        grid = QHBoxLayout()
        self._style_checks: dict[str, QCheckBox] = {}
        defaults = {"concise": True, "professional": True,
                    "friendly": False, "analysis": True}
        for key, st in STYLES.items():
            cb = QCheckBox(st.label)
            cb.setChecked(defaults.get(key, False))
            cb.setToolTip(st.instruction)
            self._style_checks[key] = cb
            grid.addWidget(cb)
        grid.addStretch()
        s_layout.addLayout(grid)
        root.addWidget(style_box)

        # ── 补充说明 ──
        note_box = QGroupBox("补充说明 (可选 — 告诉 AI 报告里没写清的背景)")
        n_layout = QVBoxLayout(note_box)
        self._notes = QPlainTextEdit()
        self._notes.setPlaceholderText(
            "例: 评审任务延期是因为等设计稿, 不算拖延\n"
            "例: 今天实际比平时多花 1 小时在处理线上问题\n"
            "例: 这周刚入职, 任务量还不稳定"
        )
        self._notes.setMaximumHeight(70)
        n_layout.addWidget(self._notes)
        root.addWidget(note_box)

        # ── 对比视图 ──
        self._tabs = QTabWidget()

        # 原始
        orig_page = QWidget()
        o_layout = QVBoxLayout(orig_page)
        self._original = QTextBrowser()
        from pycal.report import render_html
        self._original.setHtml(render_html(self._report_md, f"原始{kind}"))
        o_layout.addWidget(self._original)
        self._tabs.addTab(orig_page, "📄 原始版本")

        # 润色结果
        result_page = QWidget()
        r_layout = QVBoxLayout(result_page)
        self._result_view = QTextBrowser()
        r_layout.addWidget(self._result_view)
        self._result_view.setHtml(
            '<div style="padding:40px;color:#999;text-align:center">'
            '点击下方「开始润色」生成结果</div>'
        )
        self._tabs.addTab(result_page, "✨ AI 润色版")
        self._tabs.setEnabled(False)
        root.addWidget(self._tabs, 1)

        # ── 进度 ──
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # ── 状态栏 ──
        self._status = QLabel("")
        self._status.setStyleSheet("color: #888;")
        root.addWidget(self._status)

        # ── 按钮 ──
        btns = QHBoxLayout()
        self._btn_polish = QPushButton("✨ 开始润色")
        self._btn_polish.setStyleSheet(
            "QPushButton { background: #3498db; color: white; font-weight: bold;"
            " padding: 8px 20px; border: none; border-radius: 4px; }"
            "QPushButton:hover { background: #2980b9; }"
            "QPushButton:disabled { background: #95a5a6; }"
        )
        self._btn_polish.clicked.connect(self._start_polish)
        btns.addWidget(self._btn_polish)
        btns.addStretch()

        self._btn_copy = QPushButton("📋 复制")
        self._btn_copy.clicked.connect(self._copy)
        self._btn_save = QPushButton("💾 保存 MD")
        self._btn_save.clicked.connect(self._save)
        self._btn_html = QPushButton("🌐 导出 HTML")
        self._btn_html.clicked.connect(self._export_html)
        for b in (self._btn_copy, self._btn_save, self._btn_html):
            b.setEnabled(False)
            btns.addWidget(b)
        self._export_btns = [self._btn_copy, self._btn_save, self._btn_html]

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        root.addLayout(btns)

    # ------------------------------------------------------------------
    def _check_key(self) -> None:
        if has_api_key():
            self._key_label.setStyleSheet(
                "background: #E8F8F0; color: #1E8449; padding: 6px;"
                "border: 1px solid #A9DFBF; border-radius: 4px;"
            )
            self._key_label.setText("✓ 已配置 ANTHROPIC_API_KEY")
        else:
            self._key_label.setStyleSheet(
                "background: #FDEDEC; color: #922B21; padding: 6px;"
                "border: 1px solid #F5B7B1; border-radius: 4px;"
            )
            self._key_label.setText(
                "⚠ 未配置 API key — 设置环境变量 ANTHROPIC_API_KEY 后重开此窗口"
            )
            self._btn_polish.setEnabled(False)

    def _test_connection(self) -> None:
        if not has_api_key():
            QMessageBox.information(self, "缺少 API key", api_key_hint())
            return
        self._btn_test.setEnabled(False)
        self._btn_test.setText("测试中…")
        QApplication.processEvents()
        try:
            ok, msg = ClaudePolishClient(
                self._model.currentData()).check_connection()
        finally:
            self._btn_test.setEnabled(True)
            self._btn_test.setText("🔌 测试连接")
        QMessageBox.information(
            self, "连接测试",
            ("✓ " if ok else "✗ ") + msg
        )

    # ------------------------------------------------------------------
    def _selected_styles(self) -> list[str]:
        return [k for k, cb in self._style_checks.items() if cb.isChecked()]

    def _start_polish(self) -> None:
        styles = self._selected_styles()
        if not styles:
            QMessageBox.warning(self, "未选风格", "至少选一种润色风格")
            return
        if not has_api_key():
            QMessageBox.information(self, "缺少 API key", api_key_hint())
            return

        self._btn_polish.setEnabled(False)
        self._progress.setVisible(True)
        self._result_view.clear()
        self._status.setText(f"正在润色 (模型: {self._model.currentData()}) …")

        client = ClaudePolishClient(self._model.currentData())
        self._worker = PolishWorker(
            client, self._report_md, styles,
            self._kind, self._notes.toPlainText().strip(), self,
        )
        self._worker.chunk.connect(self._on_chunk)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_chunk(self, text: str) -> None:
        self._result_view.moveCursor(
            self._result_view.textCursor().MoveOperation.End)
        self._result_view.insertPlainText(text)
        self._status.setText(f"生成中… {len(text)} 字")

    def _on_done(self, text: str, in_tok: int, out_tok: int, model: str) -> None:
        self._progress.setVisible(False)
        self._btn_polish.setEnabled(True)
        self._btn_polish.setText("✨ 重新润色")
        self._result = text
        self._tabs.setEnabled(True)
        self._tabs.setCurrentIndex(1)
        for b in self._export_btns:
            b.setEnabled(True)

        from pycal.report import render_html
        self._result_view.setHtml(
            render_html(text, f"AI 润色{self._kind}"))
        # 粗估成本
        cost = (in_tok * 3.0 + out_tok * 15.0) / 1_000_000
        self._status.setText(
            f"✓ 完成 | {model} | ~{in_tok} in / ~{out_tok} out | "
            f"成本 ~${cost:.4f}"
        )

    def _on_failed(self, message: str, hint: str) -> None:
        self._progress.setVisible(False)
        self._btn_polish.setEnabled(True)
        self._status.setText(f"✗ {message}")
        QMessageBox.warning(
            self, "润色失败",
            message + (f"\n\n提示: {hint}" if hint else "")
        )

    # ------------------------------------------------------------------
    def _copy(self) -> None:
        QApplication.clipboard().setText(self._result)
        QMessageBox.information(self, "已复制", "润色后的 Markdown 已复制")

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 Markdown",
            f"pycal_{self._kind}_ai_{date.today():%Y%m%d}.md",
            "Markdown (*.md);;All files (*)",
        )
        if not path:
            return
        Path(path).write_text(self._result, encoding="utf-8")
        QMessageBox.information(self, "已保存", path)

    def _export_html(self) -> None:
        from pycal.report import render_html
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 HTML",
            f"pycal_{self._kind}_ai_{date.today():%Y%m%d}.html",
            "HTML (*.html);;All files (*)",
        )
        if not path:
            return
        Path(path).write_text(
            render_html(self._result, f"AI 润色{self._kind}"),
            encoding="utf-8",
        )
        QMessageBox.information(self, "已导出", path)

    def polished_markdown(self) -> str:
        """外部获取润色结果 (未润色则返回空)。"""
        return self._result


__all__ = ["PolishDialog"]