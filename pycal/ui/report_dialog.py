"""日报 / 周报 UI — 预览 + 导出。

布局:
  顶部: [日报] [周报] | 日期/周选择器 | [◀] [▶] [今天] | [复制] [保存MD] [导出HTML] [打印/PDF]
  主体: QTextBrowser (渲染 HTML 预览)
  底部: 统计摘要 (自动更新)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from PySide6.QtCore import Qt, QDate, QTimer
from PySide6.QtGui import QDesktopServices, QTextDocument
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QDateEdit, QTextBrowser, QMessageBox, QFileDialog, QToolButton,
    QApplication,
)

from pycal.report import (
    render_daily_markdown, render_weekly_markdown, render_html,
    load_day, week_bounds,
)


class ReportDialog(QDialog):
    """报告预览对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PyCal — 日报 / 周报")
        self.resize(900, 720)
        self._mode = "daily"
        self._anchor = date.today()
        self._build()
        self.refresh()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)

        # 顶部控制
        top = QHBoxLayout()

        self._btn_daily = QPushButton("📋 日报")
        self._btn_daily.setCheckable(True)
        self._btn_daily.setChecked(True)
        self._btn_daily.clicked.connect(lambda: self._switch("daily"))
        self._btn_weekly = QPushButton("📈 周报")
        self._btn_weekly.setCheckable(True)
        self._btn_weekly.clicked.connect(lambda: self._switch("weekly"))
        top.addWidget(self._btn_daily)
        top.addWidget(self._btn_weekly)
        top.addSpacing(20)

        self._btn_prev = QPushButton("◀")
        self._btn_next = QPushButton("▶")
        self._btn_today = QPushButton("今天")
        self._btn_prev.clicked.connect(lambda: self._shift(-1))
        self._btn_next.clicked.connect(lambda: self._shift(+1))
        self._btn_today.clicked.connect(lambda: self._goto_today())
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDisplayFormat("yyyy-MM-dd")
        self._date_edit.dateChanged.connect(self._on_date_pick)
        top.addWidget(self._btn_prev)
        top.addWidget(self._date_edit)
        top.addWidget(self._btn_next)
        top.addWidget(self._btn_today)
        top.addStretch()

        self._btn_copy = QPushButton("📋 复制 MD")
        self._btn_copy.clicked.connect(self._copy)
        self._btn_save = QPushButton("💾 保存")
        self._btn_save.clicked.connect(self._save)
        self._btn_html = QPushButton("🌐 导出 HTML")
        self._btn_html.clicked.connect(self._export_html)
        self._btn_print = QPushButton("🖨 打印 / PDF")
        self._btn_print.clicked.connect(self._print)
        for b in (self._btn_copy, self._btn_save, self._btn_html, self._btn_print):
            top.addWidget(b)

        root.addLayout(top)

        # 摘要条
        self._summary = QLabel("")
        self._summary.setStyleSheet(
            "padding: 8px; background: #F0F8FF; border: 1px solid #4A90E2;"
            "border-radius: 4px; color: #2C3E50;"
        )
        root.addWidget(self._summary)

        # 预览
        self._view = QTextBrowser()
        self._view.setOpenExternalLinks(True)
        self._view.setStyleSheet("background: white;")
        root.addWidget(self._view, 1)

        # 底部
        bottom = QHBoxLayout()
        self._status = QLabel("")
        self._status.setStyleSheet("color: #888;")
        bottom.addWidget(self._status)
        bottom.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    # ------------------------------------------------------------------
    def _switch(self, mode: str) -> None:
        self._mode = mode
        self._btn_daily.setChecked(mode == "daily")
        self._btn_weekly.setChecked(mode == "weekly")
        self.refresh()

    def _shift(self, delta: int) -> None:
        if self._mode == "daily":
            self._anchor = self._anchor + timedelta(days=delta)
        else:
            self._anchor = self._anchor + timedelta(days=7 * delta)
        self.refresh()

    def _goto_today(self) -> None:
        self._anchor = date.today()
        self.refresh()

    def _on_date_pick(self) -> None:
        qd = self._date_edit.date()
        self._anchor = date(qd.year(), qd.month(), qd.day())
        self.refresh()

    # ------------------------------------------------------------------
    def _markdown(self) -> str:
        if self._mode == "daily":
            return render_daily_markdown(self._anchor)
        return render_weekly_markdown(self._anchor)

    def _title_for_file(self) -> str:
        if self._mode == "daily":
            return f"pycal_daily_{self._anchor.strftime('%Y%m%d')}"
        start, end = week_bounds(self._anchor)
        return f"pycal_weekly_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        md = self._markdown()
        html = render_html(md, title=f"PyCal {self._mode}")
        self._view.setHtml(html)
        # 日期选择器同步
        self._date_edit.blockSignals(True)
        self._date_edit.setDate(
            QDate(self._anchor.year, self._anchor.month, self._anchor.day)
        )
        self._date_edit.blockSignals(False)

        # 摘要
        if self._mode == "daily":
            stats = load_day(self._anchor)
            if stats.has_items:
                bar = "█" * int(stats.rate / 10) + "░" * (10 - int(stats.rate / 10))
                self._summary.setText(
                    f"<b>{self._anchor.isoformat()}</b> — "
                    f"总 {stats.total_lines} 行 · "
                    f"<span style='color:#27ae60'>完成 {stats.done_lines}</span> · "
                    f"<span style='color:#c0392b'>未完成 {stats.pending_lines}</span> · "
                    f"完成率 <b>{stats.rate:.0f}%</b> <code>{bar}</code>"
                )
            else:
                self._summary.setText(
                    f"<b>{self._anchor.isoformat()}</b> — 今日暂无安排")
        else:
            from pycal.report import load_week
            days = load_week(self._anchor)
            total = sum(d.total_lines for d in days)
            done = sum(d.done_lines for d in days)
            rate = (done / total * 100) if total else 0
            start, end = week_bounds(self._anchor)
            iso = start.isocalendar()
            self._summary.setText(
                f"<b>{start.isoformat()} ~ {end.isoformat()}</b> "
                f"(第 {iso[1]} 周) — "
                f"总 {total} 行 · "
                f"<span style='color:#27ae60'>完成 {done}</span> · "
                f"完成率 <b>{rate:.0f}%</b>"
            )
        self._status.setText(f"字数 {len(md)} · 渲染于 {date.today()}")

    # ------------------------------------------------------------------
    def _copy(self) -> None:
        QApplication.clipboard().setText(self._markdown())
        QMessageBox.information(self, "已复制", "Markdown 已复制到剪贴板")

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 Markdown",
            f"{self._title_for_file()}.md",
            "Markdown (*.md);;Text (*.txt);;All files (*)",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(self._markdown())
        QMessageBox.information(self, "已保存", path)

    def _export_html(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 HTML",
            f"{self._title_for_file()}.html",
            "HTML (*.html);;All files (*)",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_html(self._markdown(), f"PyCal {self._mode}"))
        QMessageBox.information(self, "已导出", path)

    def _print(self) -> None:
        """打印 / 另存 PDF — 用 QTextDocument.print_ 弹系统打印对话框。"""
        doc = self._view.document()
        ok = doc.print_(QApplication.primaryScreen())
        if not ok:
            QMessageBox.information(
                self, "打印",
                "若想存成 PDF, 请在打印对话框里选择\n"
                "「Microsoft Print to PDF」作为打印机。\n"
                "或直接用「导出 HTML」后用浏览器打印成 PDF。",
            )


__all__ = ["ReportDialog"]