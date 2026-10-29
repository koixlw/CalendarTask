"""关闭窗口的行为测试。

用户的原话: 「目前还有很多功能用不了, 比如说关闭窗口等」。
根子上是 closeEvent 无条件 `event.ignore(); self.hide()` —— 按 Alt+F4 后
窗口凭空消失, 没有任何提示, 用户不知道程序还活着, 也不知道怎么真退出。

现在的约定:
  - 第一次关窗 → 弹框问清楚「收起到托盘」还是「直接退出」, 并说明各自后果
  - 用户的选择记进 settings, 之后静默按选择执行, 不再打扰
  - 没有托盘 (收起来就再也找不回来) → 直接退出
  - 工具栏「× 退出」→ 永远是真退出

跑:  python tests/test_close_behavior.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pycal.console import setup_stdout
# Windows 中文控制台默认 GBK, 打印 ✓/◀/☐ 会抛 UnicodeEncodeError
setup_stdout()


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pycal_close_")

from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {label}"
          + (f"  ({detail})" if detail and not cond else ""))


def _clear_settings() -> None:
    from pycal.ui.main_window import _save_setting
    _save_setting("close_hint_shown", "0")
    _save_setting("close_behavior", "tray")


def _close_with(win, click_text: str | None):
    """关窗, 并把首次弹出的确认框按 click_text 点掉 (None = 不点=取消)。"""
    orig = QMessageBox.exec
    box_ref = {}

    def fake_exec(self):
        box_ref["box"] = self
        if click_text:
            for b in self.buttons():
                if b.text().strip() == click_text:
                    b.click()
                    break
        return 0

    QMessageBox.exec = fake_exec
    try:
        win.show()
        app.processEvents()
        win.close()
        app.processEvents()
    finally:
        QMessageBox.exec = orig
    return box_ref.get("box")


def main() -> int:
    global app
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    from pycal.ui.main_window import PyCalMainWindow, _load_setting

    print("=" * 70)
    print("关闭窗口行为测试")
    print("=" * 70)

    win = PyCalMainWindow(app, fullscreen=True)
    win._tray = QSystemTrayIcon()      # 假装有托盘
    tray_msgs: list[str] = []
    win._tray.showMessage = lambda *a, **k: tray_msgs.append(a[0])
    quit_calls: list[int] = []
    win._app.quit = lambda: quit_calls.append(1)

    # ── 1. 第一次关窗 → 应该弹出确认框 ──
    print("\n=== 首次关窗 ===")
    _clear_settings()
    box = _close_with(win, "取消")
    check("首次关窗会弹确认框", box is not None)
    check("确认框解释了两个选项的区别", box is not None and
          "收起到托盘" in box.text() + box.informativeText() and
          "退出" in box.informativeText())
    check("选了「取消」→ 窗口还在", win.isVisible())
    check("选了「取消」→ 不记 close_hint_shown",
          _load_setting("close_hint_shown", "0") == "0")

    # ── 2. 选「收起到托盘」 ──
    print("\n=== 选择收起到托盘 ===")
    _clear_settings()
    win.show()
    _close_with(win, "收起到托盘")
    check("窗口已隐藏", not win.isVisible())
    check("记下了 close_behavior=tray",
          _load_setting("close_behavior", "") == "tray")
    check("记下了不再打扰 (close_hint_shown=1)",
          _load_setting("close_hint_shown", "0") == "1")
    check("弹了托盘气泡告诉用户还在后台", bool(tray_msgs),
          f"气泡: {tray_msgs}")

    # ── 3. 之后再关 → 静默收托盘, 不再弹框 ──
    print("\n=== 之后再关 (静默) ===")
    tray_msgs.clear()
    box2 = _close_with(win, None)      # 传 None: 若真的弹了框, 就不点任何按钮
    check("第二次关窗不再弹框", box2 is None)
    check("第二次关窗仍然隐藏窗口", not win.isVisible())

    # ── 4. 改成「直接退出」 ──
    print("\n=== 选择直接退出 ===")
    from pycal.ui.main_window import _save_setting
    _save_setting("close_behavior", "exit")
    _save_setting("close_hint_shown", "1")
    quit_calls.clear()
    win.show()
    app.processEvents()
    win.close()
    app.processEvents()
    check("close_behavior=exit 时直接退出", bool(quit_calls))
    check("退出时窗口不留在后台", not win.isVisible())

    # ── 5. 没有托盘 → 不能把窗口藏起来 ──
    print("\n=== 无托盘兜底 ===")
    win._tray = None
    quit_calls.clear()
    _save_setting("close_behavior", "tray")
    win.show()
    app.processEvents()
    win.close()
    app.processEvents()
    check("没有托盘时关窗 = 退出 (否则用户再也找不回窗口)",
          bool(quit_calls))

    # ── 6. 工具栏「× 退出」永远是真退出 ──
    print("\n=== 工具栏退出按钮 ===")
    win._tray = QSystemTrayIcon()
    quit_calls.clear()
    _save_setting("close_behavior", "tray")
    win.show()
    app.processEvents()
    win._btn_exit.click()
    app.processEvents()
    check("「× 退出」不受 close_behavior 影响, 总是真退出",
          bool(quit_calls))

    # ── 7. Esc 走同一条路, 不再无声消失 ──
    print("\n=== Esc ===")
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    _clear_settings()
    quit_calls.clear()
    seen = {}
    orig = QMessageBox.exec

    def fake_exec(self):
        seen["box"] = self
        for b in self.buttons():
            if b.text().strip() == "取消":
                b.click()
                break
        return 0

    QMessageBox.exec = fake_exec
    try:
        win.show()
        app.processEvents()
        QTest.keyClick(win, Qt.Key.Key_Escape)
        app.processEvents()
    finally:
        QMessageBox.exec = orig
    check("按 Esc 会弹确认框, 而不是无声无息把窗口藏掉",
          "box" in seen)
    check("Esc 取消后窗口仍在", win.isVisible())

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("\n失败项:")
        for f in FAIL:
            print(f"  x {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())