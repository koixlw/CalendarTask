"""GUI 冒烟测试 — 在 offscreen 平台实例化每个窗口/对话框。

这是防回归的关键: PySide6 里 `NameError` / `AttributeError` 往往只在
真正 new 出来时才炸, 光 import 看不出来。

跑:  python tests/test_ui_smoke.py
(Windows 无显示环境也能跑, 因为用 QT_QPA_PLATFORM=offscreen)
"""

from __future__ import annotations
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pycal.console import setup_stdout
# Windows 中文控制台默认 GBK, 打印 ✓/◀/☐ 会抛 UnicodeEncodeError
setup_stdout()


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

PASS, FAIL = [], []


def check(label: str, fn, destroy: bool = True):
    """destroy=True 会把 fn() 返回的顶层 widget 当场销毁。

    光 close() 不够 —— 多个对话框先后存活过之后, 解释器退出时 shiboken
    回收顺序不定, 会随机 access violation / 退出码 127。共用同一个窗口的
    检查项要传 destroy=False, 否则后面的检查会撞上已删除的 C++ 对象。
    """
    try:
        w = fn()
        if w is not None and hasattr(w, "show"):
            w.show()
            QApplication.processEvents()
            w.close()
            if destroy:
                w.deleteLater()
                QCoreApplication.sendPostedEvents(
                    None, QEvent.Type.DeferredDelete)
                QApplication.processEvents()
        PASS.append(label)
        print(f"  [OK]   {label}")
    except Exception as e:
        FAIL.append(f"{label}: {type(e).__name__}: {e}")
        print(f"  [FAIL] {label}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)


def main() -> int:
    app = QApplication.instance() or QApplication([])

    print("=" * 70)
    print("PyCal GUI 冒烟测试 (offscreen)")
    print("=" * 70)

    print("\n=== 主窗口 ===")
    from pycal.ui.main_window import PyCalMainWindow
    mw = PyCalMainWindow(app, fullscreen=True)
    mw.show()
    app.processEvents()
    check("PyCalMainWindow 构建+显示", lambda: mw, destroy=False)
    check("月视图格数 = 本月占到的周数 × 7",
          lambda: (_ for _ in ()).throw(AssertionError(
              f"渲染了 {len(mw._active_month_view._cells)} 格"))
          if len(mw._active_month_view._cells) not in (28, 35, 42) else None)
    check("月份标签非空",
          lambda: None if mw._month_label.text() else
          (_ for _ in ()).throw(AssertionError("月份标签为空")))
    check("工具栏按钮齐全",
          lambda: None if all([
              mw._btn_prev, mw._btn_next, mw._btn_search, mw._btn_report,
              mw._btn_more, mw._btn_help, mw._btn_min, mw._btn_max,
              mw._btn_exit,
          ]) else (_ for _ in ()).throw(AssertionError("有按钮为 None")))
    check("「更多」菜单含全部功能",
          lambda: None if all(
              any(k in a.text() for a in mw._btn_more.menu().actions())
              for k in ("云同步", "便签", "导入", "皮肤", "壁纸", "帮助",
                        "API key")
          ) else (_ for _ in ()).throw(AssertionError("菜单缺功能项")))

    print("\n=== 对话框 ===")
    from pycal.ui.report_dialog import ReportDialog
    from pycal.ui.ai_polish_dialog import PolishDialog
    from pycal.ui.search_dialog import SearchDialog
    from pycal.ui.sync_dialog import SyncDialog
    from pycal.ui.editor import ItemEditorDialog
    from pycal.ui.color_picker import ColorPickerDropdown
    from pycal.ui.settings_dialog import SettingsDialog

    check("ReportDialog", lambda: ReportDialog())
    check("PolishDialog", lambda: PolishDialog("# 报告\n- [x] 完成项"))
    check("SearchDialog", lambda: SearchDialog())
    check("SyncDialog", lambda: SyncDialog())
    check("ItemEditorDialog", lambda: ItemEditorDialog())
    check("ColorPickerDropdown", lambda: ColorPickerDropdown())
    check("SettingsDialog (API key 配置)", lambda: SettingsDialog())

    print("\n=== 模块导入完整性 ===")
    import importlib
    import pkgutil
    import pycal
    bad = []
    for m in pkgutil.walk_packages(pycal.__path__, "pycal."):
        if m.name.endswith("__main__"):
            continue
        try:
            importlib.import_module(m.name)
        except Exception as e:
            bad.append(f"{m.name}: {e}")
    check("所有子模块可导入",
          lambda: None if not bad else
          (_ for _ in ()).throw(AssertionError("; ".join(bad))))

    print("\n=== DayCell 属性 ===")
    from pycal.ui.month_view import DayCell
    cell = DayCell("2026-10-21", 21, in_month=True, is_today=True)
    cell.show()
    app.processEvents()
    check("DayCell 有 day_num", lambda: None if cell.day_num == 21 else
          (_ for _ in ()).throw(AssertionError(f"day_num={cell.day_num}")))
    cell.close()

    print("\n=== CLI 参数 (不带子命令) ===")
    from pycal.__main__ import main as cli_main
    old_argv = sys.argv
    try:
        sys.argv = ["pycal"]          # 触发 args.cmd is None 分支
        # 不真跑 GUI (会阻塞), 只验证 getattr 逻辑存在
        import pycal.__main__ as m
        src_ok = "getattr(args, \"no_fullscreen\", False)" in \
            Path(m.__file__).read_text(encoding="utf-8")
        check("cli 用 getattr 兜底 no_fullscreen", lambda: None if src_ok
              else (_ for _ in ()).throw(AssertionError("仍是 args.no_fullscreen")))
    finally:
        sys.argv = old_argv

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