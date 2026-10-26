"""窗口自由缩放测试 — 铺满屏幕 / 普通窗口 / 工具栏紧凑模式。

背景: 以前工具栏 12 个带字的按钮最少要 1430px, 窗口的下限被它钉死,
用户根本没法把日历缩小成一块小挂历。现在:
  - 窗口窄了 → 工具栏自动切成纯图标
  - 「□」按钮在「铺满屏幕」和「普通窗口」之间来回切
  - 月视图只画这个月真正占到的周

跑:  python tests/test_resize.py
"""

from __future__ import annotations
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

PASS, FAIL = [], []


def check(label: str, fn):
    try:
        fn()
        PASS.append(label)
        print(f"  [OK]   {label}")
    except Exception as e:
        FAIL.append(f"{label}: {type(e).__name__}: {e}")
        print(f"  [FAIL] {label}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="pycal_test_resize_")
    os.environ["APPDATA"] = tmp
    from pycal.db.connection import open_default, close_default
    close_default()

    app = QApplication.instance() or QApplication([])

    from pycal.ui.main_window import PyCalMainWindow

    print("=" * 70)
    print("窗口自由缩放测试")
    print("=" * 70)

    win = PyCalMainWindow(app, fullscreen=True)
    win.show()
    app.processEvents()

    # ------------------------------------------------------------------
    print("\n=== 1. 最小尺寸 ===")

    check("窗口最小宽度不再是 1448 (工具栏不再钉死窗口)", lambda: _assert(
        win.minimumSizeHint().width() < 900,
        f"最小宽度 {win.minimumSizeHint().width()}",
    ))

    check("最小高度也降下来了", lambda: _assert(
        win.minimumSizeHint().height() < 500,
        f"最小高度 {win.minimumSizeHint().height()}",
    ))

    check("月视图格子的最小宽度降下来了", lambda: _assert(
        win._active_month_view.minimumSizeHint().width() < 600,
        f"{win._active_month_view.minimumSizeHint().width()}",
    ))

    # ------------------------------------------------------------------
    print("\n=== 2. 工具栏紧凑模式 ===")

    win.resize(700, 500)
    app.processEvents()
    check("窄窗口 → 工具栏切成纯图标", lambda: _assert(
        win._compact and win._btn_report.text() == "📑",
        f"compact={win._compact} 按钮文字={win._btn_report.text()!r}",
    ))

    check("紧凑模式下按钮不再撑宽度", lambda: _assert(
        win._btn_report.width() <= 40, win._btn_report.width(),
    ))

    check("紧凑模式下月份标签用短写法", lambda: _assert(
        "年" in win._month_label.text()
        and len(win._month_label.text()) < 10,
        win._month_label.text(),
    ))

    check("窄窗口真的能缩到 700 宽", lambda: _assert(
        win.width() <= 760, f"实际 {win.width()}",
    ))

    win.resize(1600, 900)
    app.processEvents()
    check("宽窗口 → 工具栏恢复完整文字", lambda: _assert(
        not win._compact and win._btn_report.text() == "📑 日报",
        f"compact={win._compact} 按钮文字={win._btn_report.text()!r}",
    ))

    check("tooltip 始终是完整说明", lambda: _assert(
        "日报" in win._btn_report.toolTip(), win._btn_report.toolTip(),
    ))

    # ------------------------------------------------------------------
    print("\n=== 3. 铺满屏幕 / 普通窗口 ===")

    check("启动是铺满屏幕", lambda: _assert(
        win._full is True, win._full,
    ))

    win.toggle_maximized()
    app.processEvents()
    check("点一下 → 变成普通窗口", lambda: _assert(
        win._full is False, win._full,
    ))

    check("普通窗口下边缘可以缩放", lambda: _assert(
        win._hit_test_edge(win.rect().bottomLeft() + type(
            win.rect().bottomLeft())(0, -2)) is not None,
        "左下角应该能缩放",
    ))

    win.toggle_maximized()
    app.processEvents()
    check("再点 → 回到铺满", lambda: _assert(
        win._full is True, win._full,
    ))

    check("铺满时边缘不触发缩放", lambda: _assert(
        win._hit_test_edge(type(win.rect().topLeft())(0, 0)) is None,
        "满屏时拖边缘会变成拖屏幕, 不该缩放",
    ))

    # ------------------------------------------------------------------
    print("\n=== 4. 月视图只画本月占到的周 ===")

    from datetime import date
    from calendar import monthrange

    mv = win._active_month_view
    for y, m in ((2026, 10), (2026, 2), (2026, 8), (2027, 1)):
        mv.goto(y, m)
        app.processEvents()
        want = (date(y, m, 1).weekday() + monthrange(y, m)[1] + 6) // 7
        check(f"{y}-{m:02d} 画 {want} 周 ({want * 7} 格)", lambda y=y, m=m,
              want=want: _assert(
                  len(mv._cells) == want * 7, len(mv._cells),
              ))

    mv.goto(2026, 10)
    app.processEvents()
    check("别的月份的格子不显示日期号", lambda: _assert(
        all(c._num_lbl.text() == ""
            for c in mv._cells.values() if not c.in_month),
        "有别的月份的日子漏进来了",
    ))

    check("本月的格子都有日期号", lambda: _assert(
        all(c._num_lbl.text() == str(c.day_num)
            for c in mv._cells.values() if c.in_month),
        "本月的日子没显示出来",
    ))

    check("别的月份的格子也不显示农历", lambda: _assert(
        all(c._sub_lbl.text() == ""
            for c in mv._cells.values() if not c.in_month),
        "有别的月份的农历漏进来了",
    ))

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
