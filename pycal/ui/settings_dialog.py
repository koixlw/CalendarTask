"""设置对话框 — 界面里配 API key 的入口。

以前配 key 只能改环境变量, 还得重启 PyCal 才生效, 门槛太高。
现在工具栏「⋯ 更多 → 🔑 AI 润色 API key」(或 Ctrl+,) 就能直接填。

这一页目前只有「AI 润色」一个标签 —— 以后想加别的设置
(报告目录 / 自动生成时间 / 主题…) 往 QTabWidget 里加页就行。

安全:
  - key 存在本机 settings 表, base64 + XOR 打乱 —— 只防"随手打开 DB
    看到明文", 不是强加密。真要强保护得走 Windows 凭据管理器。
  - key 不写日志, 不进异常信息
"""

from __future__ import annotations

import webbrowser
from typing import Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QMessageBox, QTabWidget, QWidget, QGroupBox,
    QFormLayout,
)

from pycal.report.ai_polish import (
    ClaudePolishClient, MODEL_DEFAULT,
    api_key_source, clear_api_key, resolve_api_key, save_api_key,
)


KEY_URL = "https://console.anthropic.com/settings/keys"


class _TestWorker(QThread):
    """后台测连接 —— 网络请求可能卡十几秒, 不能堵着界面。"""

    done = Signal(bool, str)

    def __init__(self, key: str, model: str, parent=None):
        super().__init__(parent)
        self._key = key
        self._model = model

    def run(self):
        try:
            ok, msg = ClaudePolishClient(
                self._model, api_key=self._key
            ).check_connection()
        except Exception as e:      # 兜底, 免得线程里抛出没人接
            ok, msg = False, str(e)[:200]
        self.done.emit(ok, msg)


class ApiKeyPage(QWidget):
    """API key 配置页 — 设置对话框里的那一页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[_TestWorker] = None
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        intro = QLabel(
            "AI 润色日报 / 周报需要一个 Anthropic API key。\n"
            "填在这里就不用再折腾环境变量, 存好立刻能用。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        # ── key 输入 ──
        box = QGroupBox("API key")
        form = QFormLayout(box)

        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.setPlaceholderText("sk-ant-api03-...")
        self._key.setMinimumWidth(380)
        self._key.textChanged.connect(self._on_text_changed)
        form.addRow("Key:", self._key)

        self._show = QCheckBox("显示明文 (当心截图/共享屏幕)")
        self._show.toggled.connect(self._on_show_toggled)
        form.addRow("", self._show)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        form.addRow("当前状态:", self._status)

        btns = QHBoxLayout()
        self._btn_save = QPushButton("💾 保存")
        self._btn_save.clicked.connect(self._save)
        self._btn_test = QPushButton("🔌 测试连接")
        self._btn_test.clicked.connect(self._test)
        self._btn_clear = QPushButton("🗑 清除")
        self._btn_clear.clicked.connect(self._clear)
        for b in (self._btn_save, self._btn_test, self._btn_clear):
            btns.addWidget(b)
        btns.addStretch()
        form.addRow(btns)
        root.addWidget(box)

        # ── 拿 key 的地方 ──
        get_box = QGroupBox("还没有 key?")
        g_layout = QVBoxLayout(get_box)
        g_layout.addWidget(QLabel(
            "到 Anthropic 控制台新建一个 key (Console → API Keys),\n"
            "新建时记得选一个有 credit 的工作区。"
        ))
        btn_url = QPushButton("🔗 打开 Anthropic 控制台")
        btn_url.clicked.connect(lambda: webbrowser.open(KEY_URL))
        g_layout.addWidget(btn_url)
        root.addWidget(get_box)

        note = QLabel(
            "ℹ key 存在本机的 PyCal 数据库里 (base64 + 简单打乱, "
            "不是强加密) —— 只在你自己的电脑上。\n"
            "   想完全不留痕, 就删掉它改用环境变量 ANTHROPIC_API_KEY。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        root.addWidget(note)
        root.addStretch(1)

        self._refresh_status()

    # ------------------------------------------------------------------
    def _on_text_changed(self, text: str) -> None:
        self._btn_test.setEnabled(bool(text.strip()))

    def _on_show_toggled(self, on: bool) -> None:
        self._key.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        )

    def _refresh_status(self) -> None:
        """状态条 — 告诉用户现在用的是哪一个 key。"""
        src = api_key_source()
        # 有 key (保存的或环境变量里的) 就能测连接
        self._btn_test.setEnabled(
            bool(self._key.text().strip()) or bool(resolve_api_key())
        )
        if src == "saved":
            self._status.setText("✓ 已配置 (保存在本机)")
            self._status.setStyleSheet("color: #1E8449;")
            self._btn_clear.setEnabled(True)
        elif src == "env":
            self._status.setText(
                "✓ 已配置 (来自环境变量 ANTHROPIC_API_KEY)\n"
                "   在上面填一个并保存, 会改用你填的这个。"
            )
            self._status.setStyleSheet("color: #1E8449;")
            self._btn_clear.setEnabled(False)
        else:
            self._status.setText("⚠ 还没配置 key")
            self._status.setStyleSheet("color: #922B21;")
            self._btn_clear.setEnabled(False)
        # 已保存的 key 不回显 —— 免得明文躺在内存/截图里
        if not self._key.text().strip():
            self._key.setPlaceholderText(
                "已保存 (不回显) — 输入新 key 可覆盖" if src == "saved"
                else "sk-ant-api03-..."
            )

    # ------------------------------------------------------------------
    def _save(self) -> None:
        key = self._key.text().strip()
        if not key:
            QMessageBox.information(
                self, "没东西可存",
                "先把 key 粘进上面的输入框。\n\n"
                "如果之前配过环境变量, 想改用界面里填的, "
                "把框里的内容换成新 key 再保存就行。",
            )
            return
        try:
            save_api_key(key)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"写不进数据库:\n{e}")
            return
        self._key.clear()
        self._refresh_status()
        QMessageBox.information(
            self, "已保存",
            "API key 已保存到本机, 现在就能用 AI 润色了。\n\n"
            "提示: 输入框里的内容已经清掉了 (不回显明文)。",
        )

    def _clear(self) -> None:
        if QMessageBox.question(
            self, "确认清除",
            "删掉本机保存的 API key?\n\n"
            "(环境变量里的 ANTHROPIC_API_KEY 不受影响)",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            clear_api_key()
        except Exception as e:
            QMessageBox.critical(self, "清除失败", str(e))
            return
        self._key.clear()
        self._refresh_status()

    def _test(self) -> None:
        key = self._key.text().strip() or resolve_api_key()
        if not key:
            QMessageBox.information(
                self, "没 key 可测",
                "先把 key 粘进输入框, 或者配好环境变量。",
            )
            return
        self._btn_test.setEnabled(False)
        self._btn_test.setText("测试中…")
        self._worker = _TestWorker(key, MODEL_DEFAULT, self)
        self._worker.done.connect(self._on_test_done)
        self._worker.start()

    def _on_test_done(self, ok: bool, msg: str) -> None:
        self._btn_test.setEnabled(True)
        self._btn_test.setText("🔌 测试连接")
        if ok:
            QMessageBox.information(self, "连接测试", "✓ " + msg)
        else:
            QMessageBox.warning(
                self, "连接测试",
                "✗ 连不上\n\n" + msg +
                "\n\nkey 填错 / 已过期 / 没网, 都可能是这个结果。",
            )


class SettingsDialog(QDialog):
    """设置主对话框 — 以后加别的设置往这加页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PyCal 设置")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)

        tabs = QTabWidget(self)
        self._api_page = ApiKeyPage(tabs)
        tabs.addTab(self._api_page, "🔑 AI 润色 API key")
        root.addWidget(tabs, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        root.addLayout(btns)

    def key_configured(self) -> bool:
        """关掉之后问一句: 现在有 key 了吗? (调用方决定要不要继续下一步)"""
        return bool(resolve_api_key())


__all__ = ["SettingsDialog", "ApiKeyPage"]
