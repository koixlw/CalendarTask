"""同步账号管理 UI。

两栏:
  左 — 账号列表 (provider 图标 + 名称 + 上次同步状态)
  右 — 表单 (服务商 / URL / 用户名 / 密码 / 只读 / 方向)
       + 按钮 (测试连接 / 列举日历 / 保存 / 立即同步)

顶部还有:
  - "同步所有账号" 按钮
  - 冲突策略下拉
  - 进度显示 + 日志区
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QComboBox, QPushButton, QCheckBox, QLabel, QListWidget,
    QListWidgetItem, QSplitter, QTextEdit, QMessageBox, QGroupBox,
    QDialogButtonBox, QProgressBar, QWidget,
)

from pycal.sync.presets import PRESETS, get_preset
from pycal.sync.accounts import (
    SyncAccount, list_accounts, create_account, update_account,
    delete_account, get_account,
)
from pycal.sync.client import CaldavClient, CaldavError


class SyncWorker(QThread):
    """后台跑同步/测试连接, 避免阻塞 UI。"""

    progress = Signal(str)
    done = Signal(object)          # SyncResult / list / str
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            res = self._fn(*self._args, progress_cb=self.progress.emit, **self._kwargs)
            self.done.emit(res)
        except Exception as e:
            self.failed.emit(str(e))


class AccountFormWidget(QGroupBox):
    """账号编辑表单。"""

    def __init__(self, parent=None):
        super().__init__("账号设置", parent)
        form = QFormLayout(self)

        self._provider = QComboBox()
        for key, spec in PRESETS.items():
            self._provider.addItem(spec.label, key)
        self._provider.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("服务商:", self._provider)

        self._display_name = QLineEdit()
        self._display_name.setPlaceholderText("留空则用服务商默认名")
        form.addRow("显示名:", self._display_name)

        self._base_url = QLineEdit()
        self._base_url.setPlaceholderText("https://...")
        form.addRow("服务器 URL:", self._base_url)

        self._username = QLineEdit()
        form.addRow("用户名:", self._username)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("密码:", self._password)

        self._calendar_url = QLineEdit()
        self._calendar_url.setPlaceholderText("留空则自动选第一个日历")
        form.addRow("日历 URL:", self._calendar_url)

        self._read_only = QCheckBox("只读 (只拉取, 永不修改云端)")
        form.addRow("", self._read_only)

        self._direction = QComboBox()
        self._direction.addItem("双向同步", "both")
        self._direction.addItem("仅拉取 (云端 → 本地)", "pull")
        self._direction.addItem("仅推送 (本地 → 云端)", "push")
        form.addRow("方向:", self._direction)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(
            "color: #666; background: #FFF8DC; padding: 6px;"
            "border: 1px solid #E0C060; border-radius: 4px;"
        )
        form.addRow(self._hint)

    def _on_provider_changed(self) -> None:
        key = self._provider.currentData()
        spec = get_preset(key)
        self._base_url.setText(spec.base_url)
        self._username.setPlaceholderText(spec.username_hint)
        self._password.setPlaceholderText(spec.password_hint)
        self._hint.setText(spec.notes)

    def load(self, acc: SyncAccount) -> None:
        idx = self._provider.findData(acc.provider)
        if idx >= 0:
            self._provider.setCurrentIndex(idx)
        self._display_name.setText(acc.display_name)
        self._base_url.setText(acc.base_url)
        self._username.setText(acc.username)
        self._password.setText(acc.password)
        self._calendar_url.setText(acc.calendar_url)
        self._read_only.setChecked(bool(acc.read_only))
        didx = self._direction.findData(acc.sync_direction)
        if didx >= 0:
            self._direction.setCurrentIndex(didx)

    def collect(self) -> dict:
        return {
            "provider": self._provider.currentData(),
            "display_name": self._display_name.text().strip(),
            "base_url": self._base_url.text().strip(),
            "username": self._username.text().strip(),
            "password": self._password.text(),
            "calendar_url": self._calendar_url.text().strip(),
            "read_only": 1 if self._read_only.isChecked() else 0,
            "sync_direction": self._direction.currentData(),
        }

    def is_valid(self) -> bool:
        d = self.collect()
        return bool(d["base_url"] and d["username"] and d["password"])


class SyncDialog(QDialog):
    """同步管理主对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PyCal — 云同步")
        self.resize(880, 620)
        self._current_id: Optional[int] = None
        self._build()
        self.reload_accounts()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)

        # 顶部: 全局操作
        top = QHBoxLayout()
        self._btn_sync_all = QPushButton("▶ 同步所有账号")
        self._btn_sync_all.clicked.connect(self._sync_all)
        top.addWidget(self._btn_sync_all)

        top.addSpacing(16)
        top.addWidget(QLabel("冲突策略:"))
        self._policy = QComboBox()
        self._policy.addItem("较新者胜 (推荐)", "newest_wins")
        self._policy.addItem("本地优先", "local_wins")
        self._policy.addItem("远端优先", "remote_wins")
        top.addWidget(self._policy)
        top.addStretch()
        root.addLayout(top)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # 忙碌模式
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # 主体: 左列表 + 右表单
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_select_row)
        splitter.addWidget(self._list)

        right = QWidget()
        right_layout = QVBoxLayout(right)

        self._form = AccountFormWidget()
        right_layout.addWidget(self._form)

        # 账号操作按钮
        btn_row = QHBoxLayout()
        self._btn_test = QPushButton("🔌 测试连接")
        self._btn_test.clicked.connect(self._test_connection)
        self._btn_list_cals = QPushButton("📋 列举日历")
        self._btn_list_cals.clicked.connect(self._list_calendars)
        self._btn_save = QPushButton("💾 保存")
        self._btn_save.clicked.connect(self._save_account)
        self._btn_sync_one = QPushButton("🔄 立即同步")
        self._btn_sync_one.clicked.connect(self._sync_one)
        self._btn_delete = QPushButton("🗑 删除")
        self._btn_delete.clicked.connect(self._delete_account)
        for b in (self._btn_test, self._btn_list_cals, self._btn_save,
                  self._btn_sync_one, self._btn_delete):
            btn_row.addWidget(b)
        right_layout.addLayout(btn_row)

        self._calendar_list = QComboBox()
        right_layout.addWidget(QLabel("可用日历:"))
        right_layout.addWidget(self._calendar_list)

        # 日志区
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(140)
        self._log.setStyleSheet(
            "background: #1E1E1E; color: #D4D4D4; font-family: Consolas;"
        )
        right_layout.addWidget(QLabel("日志:"))
        right_layout.addWidget(self._log, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        # 底部
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.accept)
        root.addWidget(btns)

        # 初始 provider 提示
        self._form._on_provider_changed()

    # ------------------------------------------------------------------
    # 账号列表
    # ------------------------------------------------------------------
    def reload_accounts(self) -> None:
        self._list.clear()
        for acc in list_accounts():
            it = QListWidgetItem(f"{'✓' if acc.enabled else '✗'} {acc.display_name}")
            it.setData(Qt.ItemDataRole.UserRole, acc.account_id)
            status = acc.last_status or "未同步过"
            it.setToolTip(
                f"服务商: {acc.provider}\n"
                f"URL: {acc.base_url}\n"
                f"用户: {acc.username}\n"
                f"上次同步: {acc.last_sync or '—'}\n"
                f"状态: {status}"
            )
            self._list.addItem(it)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        else:
            self._form._provider.setCurrentIndex(
                self._form._provider.findData("google")
            )

    def _on_select_row(self, row: int) -> None:
        if row < 0:
            return
        acc_id = self._list.item(row).data(Qt.ItemDataRole.UserRole)
        acc = get_account(acc_id)
        if acc:
            self._current_id = acc.account_id
            self._form.load(acc)
            self._log.append(f"── 选中: {acc.display_name} ({acc.provider})")

    # ------------------------------------------------------------------
    # 账号 CRUD
    # ------------------------------------------------------------------
    def _save_account(self) -> None:
        d = self._form.collect()
        if not (d["base_url"] and d["username"] and d["password"]):
            QMessageBox.warning(self, "信息不完整",
                                "需要填写: 服务器 URL / 用户名 / 密码")
            return
        if self._current_id is None:
            acc_id = create_account(
                d["provider"], d["base_url"], d["username"], d["password"],
                d["display_name"], d["calendar_url"],
                d["read_only"], d["sync_direction"],
            )
            self._log.append(f"✓ 新建账号 #{acc_id}: {d['display_name'] or d['provider']}")
        else:
            update_account(self._current_id, **d)
            self._log.append(f"✓ 更新账号 #{self._current_id}")
        self.reload_accounts()

    def _delete_account(self) -> None:
        if self._current_id is None:
            return
        ret = QMessageBox.question(
            self, "确认删除",
            "删除这个同步账号?\n\n"
            "注意: 只删本地账号配置和同步链接记录,\n"
            "已经拉下来的日历条目会保留在本地。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            delete_account(self._current_id)
            self._current_id = None
            self._log.append(f"🗑 删除账号 #{self._current_id}")
            self.reload_accounts()

    # ------------------------------------------------------------------
    # 连接测试
    # ------------------------------------------------------------------
    def _test_connection(self) -> None:
        d = self._form.collect()
        if not (d["base_url"] and d["username"] and d["password"]):
            QMessageBox.warning(self, "信息不完整",
                                "需要填写: 服务器 URL / 用户名 / 密码")
            return
        acc = SyncAccount(
            account_id=self._current_id or 0,
            provider=d["provider"], display_name=d["display_name"],
            base_url=d["base_url"], username=d["username"],
            password=d["password"], calendar_url=d["calendar_url"],
        )
        self._progress.setVisible(True)
        self._btn_test.setEnabled(False)

        def _work(progress_cb):
            progress_cb("连接中…")
            with CaldavClient(acc) as c:
                cals = c.list_calendars()
                names = [n for n, _ in cals]
                progress_cb(f"发现 {len(cals)} 个日历")
                return names

        self._worker = SyncWorker(_work)
        self._worker.progress.connect(self._log.append)
        self._worker.done.connect(self._on_test_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_test_done(self, names) -> None:
        self._progress.setVisible(False)
        self._btn_test.setEnabled(True)
        if names:
            self._log.append(f"✓ 连接成功! 发现 {len(names)} 个日历:")
            for n in names:
                self._log.append(f"    · {n}")
            self._calendar_list.clear()
            self._calendar_list.addItems(names)
        else:
            self._log.append("✓ 连接成功, 但该账号下没有日历")

    # ------------------------------------------------------------------
    def _list_calendars(self) -> None:
        self._test_connection()

    # ------------------------------------------------------------------
    # 同步
    # ------------------------------------------------------------------
    def _sync_one(self) -> None:
        if self._current_id is None:
            QMessageBox.information(self, "未选中", "先选一个账号")
            return
        from pycal.sync.engine import SyncEngine
        acc = get_account(self._current_id)
        if not acc:
            return
        self._progress.setVisible(True)
        policy = self._policy.currentData()

        def _work(progress_cb):
            eng = SyncEngine(acc, conflict_policy=policy, progress_cb=progress_cb)
            return eng.sync()

        self._worker = SyncWorker(_work)
        self._worker.progress.connect(self._log.append)
        self._worker.done.connect(self._on_sync_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _sync_all(self) -> None:
        from pycal.sync.engine import sync_all
        self._progress.setVisible(True)
        policy = self._policy.currentData()

        def _work(progress_cb):
            return sync_all(conflict_policy=policy, progress_cb=progress_cb)

        self._worker = SyncWorker(_work)
        self._worker.progress.connect(self._log.append)
        self._worker.done.connect(self._on_sync_all_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_sync_done(self, result) -> None:
        self._progress.setVisible(False)
        self._log.append("")
        self._log.append(f"═══ 同步完成: {result.summary()} ═══")
        for e in result.errors:
            self._log.append(f"  ✗ {e}")
        self.reload_accounts()

    def _on_sync_all_done(self, results) -> None:
        self._progress.setVisible(False)
        self._log.append("")
        self._log.append("═══ 全部账号同步完成 ═══")
        for r in results:
            self._log.append(f"  {r.account_name}: {r.summary()}")
            for e in r.errors:
                self._log.append(f"      ✗ {e}")
        self.reload_accounts()

    def _on_failed(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._btn_test.setEnabled(True)
        self._log.append(f"✗ 失败: {msg}")
        QMessageBox.warning(self, "操作失败", msg)


__all__ = ["SyncDialog"]