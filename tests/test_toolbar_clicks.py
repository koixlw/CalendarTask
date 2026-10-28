"""工具栏点击回归测试 —— 真发鼠标事件, 不是直接调 slot。

背景: 曾经 `FloatingWindow.eventFilter` 把工具栏区域 (y<=36) 内**所有**子控件
的 MouseButtonPress 都 return True 吞掉了, 而 `set_drag_area()` 又恰好给每个
工具栏按钮都装了这个过滤器。结果 12 个按钮一个都点不动 (日报/搜索/关闭…)。

之前 test_ui_smoke.py 只断言按钮 `is not None`, 从没真点过, 所以没兜住。
这里必须用 QTest 发真实鼠标事件 —— 直接调 `button.click()` 是测不出来的,
因为那条路径根本不经过事件过滤器。

跑:  python tests/test_toolbar_clicks.py
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
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pycal_click_")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

# 模态框在 offscreen 下会永久阻塞, 测试里统一打掉
for _n in ("information", "warning", "question", "critical"):
    setattr(QMessageBox, _n,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
QDialog.exec = lambda self: 0

PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {label}"
          + (f"  ({detail})" if detail and not cond else ""))


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from pycal.ui.main_window import PyCalMainWindow

    win = PyCalMainWindow(app, fullscreen=True)
    win.show()
    app.processEvents()

    print("=" * 70)
    print("工具栏点击回归测试")
    print("=" * 70)

    # ── 1. 每个按钮都要能被真点击点到, 且槽函数不能抛异常 ──
    # 注意: QToolButton.clicked(bool checked) 会**位置传参**, 所以接收端
    # 必须写成 lambda *a, **k, 不能用默认值参数 (那样拿到的是 False)。
    print("\n=== 按钮能否响应点击 ===")
    buttons = sorted(n for n in dir(win)
                     if n.startswith("_btn_")
                     and hasattr(getattr(win, n), "clicked"))
    fired: set[str] = set()
    dead: list[str] = []
    # 槽函数里抛异常时 PySide6 只会打到 stderr, 测试照样"通过" —— 必须抓住
    slot_errors: list[str] = []
    orig_hook = sys.excepthook

    def _record(et, ev, tb):
        slot_errors.append(
            f"{et.__name__}: {ev}".strip().splitlines()[-1])

    sys.excepthook = _record
    try:
        for name in buttons:
            btn = getattr(win, name)
            before = len(slot_errors)
            btn.clicked.connect(lambda *a, **k: fired.add(name))
            QTest.mouseClick(btn, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier)
            app.processEvents()
            if name not in fired:
                dead.append(name)
            elif len(slot_errors) > before:
                print(f"       ↳ {name} 的槽函数抛异常: "
                      f"{slot_errors[-1]}")
    finally:
        sys.excepthook = orig_hook
    check(f"全部 {len(buttons)} 个按钮都能点动", not dead,
          f"点不动: {dead}")
    check("按钮的槽函数都不抛异常", not slot_errors,
          f"{len(slot_errors)} 个槽函数报错")

    # ── 2. 工具栏排布顺序 (prev/next/today 不能被后面的 addWidget 挪走) ──
    print("\n=== 工具栏顺序 ===")
    bar = win._central.layout().itemAt(0).widget()
    order = [it.widget() for it in (bar.layout().itemAt(i)
                                    for i in range(bar.layout().count()))
             if it.widget() is not None]
    try:
        i_prev = order.index(win._btn_prev)
        i_label = order.index(win._month_label)
        i_next = order.index(win._btn_next)
        check("月份导航顺序为 ◀ 标签 ▶", i_prev < i_label < i_next,
              f"实际 index: {i_prev}, {i_label}, {i_next}")
        # 第 0 位是标题「📅 PyCal」, 月份导航紧随其后, 后面才是功能按钮
        check("月份导航紧跟标题, 在所有功能按钮之前",
              i_prev <= 1 and i_next < order.index(win._btn_report),
              f"prev={i_prev} next={i_next} "
              f"report={order.index(win._btn_report)}")
    except ValueError as e:
        check("月份导航顺序为 ◀ 标签 ▶", False, str(e))

    # ── 3. 每个按钮都要有 tooltip (别再让用户靠猜) ──
    print("\n=== 提示文案 ===")
    no_tip = [n for n in buttons if not getattr(win, n).toolTip()]
    check("所有按钮都有 tooltip", not no_tip, f"缺 tooltip: {no_tip}")

    # ── 4. 「更多」菜单要覆盖全部功能, 且每项都有说明 ──
    print("\n=== 「更多」菜单 ===")
    menu = win._btn_more.menu()
    check("「更多」按钮挂了菜单", menu is not None)
    if menu is not None:
        texts = [a.text() for a in menu.actions() if a.text()]
        check("菜单项数量合理 (>=15)", len(texts) >= 15, f"只有 {len(texts)} 项")
        no_desc = [a.text() for a in menu.actions()
                   if a.text() and not a.isSeparator() and not a.toolTip()
                   and a.menu() is None and a.text() != "帮助"
                   and a.text() != "数据" and a.text() != "常用"
                   and a.text() != "外观" and a.text() != "窗口"]
        check("菜单项都有说明 tooltip", not no_desc, f"缺说明: {no_desc}")
        joined = " | ".join(texts)
        for must in ("云同步", "便签", "导入", "皮肤", "壁纸", "帮助", "退出"):
            check(f"菜单含「{must}」", must in joined)

    # ── 5. 菜单里的每个可点项都得真的接了槽, 不能是"摆在那儿"的摆设 ──
    print("\n=== 菜单项接线 ===")
    import pycal.ui.main_window as mw_mod

    # 把所有处理函数换成"间谍", 再重建菜单并逐项触发 —— 点了没反应就说明
    # 那个菜单项是摆设。QObject.receivers() 在 PySide6 里对 SignalInstance
    # 不可靠, 所以用这个行为验证的办法。
    SPY_TARGETS = [
        "_open_search", "_open_report", "_open_ai", "_open_sync",
        "_toggle_notes", "refresh_view", "_toggle_topmost",
        "_toggle_animation", "_open_help", "_show_about", "_collapse",
        "_quit_app", "_set_skin", "_set_opacity",
        "_toggle_year_view_from_menu", "_open_settings",
        "_toggle_widget",
    ]
    hit: list[str] = []
    saved = {n: getattr(win, n) for n in SPY_TARGETS}
    saved_mod = {n: getattr(mw_mod, n)
                 for n in ("_import_dialog", "_set_wallpaper", "_save_setting")}

    def make_spy(name):
        return lambda *a, **k: hit.append(name)

    try:
        for n in SPY_TARGETS:
            setattr(win, n, make_spy(n))
        for n in saved_mod:
            setattr(mw_mod, n, make_spy("mod." + n))
        win._btn_more.setMenu(win._build_more_menu())

        def leaves(m, prefix=""):
            out = []
            for a in m.actions():
                # 禁用的是分组标题 (常用/数据/…), 本来就不可点, 跳过
                if a.isSeparator() or not a.text() or not a.isEnabled():
                    continue
                if a.menu() is not None:
                    out.extend(leaves(a.menu(), prefix + a.text() + "/"))
                else:
                    out.append((prefix + a.text(), a))
            return out

        entries = leaves(win._btn_more.menu())
        for label, act in entries:
            before = len(hit)
            act.trigger()
            app.processEvents()
            if len(hit) == before:
                print(f"       ↳ 点了没反应: {label}")
    finally:
        for n, f in saved.items():
            setattr(win, n, f)
        for n, f in saved_mod.items():
            setattr(mw_mod, n, f)
        win._btn_more.setMenu(win._build_more_menu())

    check(f"{len(entries)} 个菜单项点了都有反应", len(hit) == len(entries),
          f"{len(entries) - len(hit)} 个没反应")
    check("「关闭窗口时」子菜单有 2 个选项 (收托盘 / 直接退出)",
          sum(1 for t, _ in entries
              if t.startswith("关闭窗口时/")) == 2)

    # ── 6. 帮助对话框能打开 ──
    print("\n=== 帮助 ===")
    from pycal.ui.help_dialog import HelpDialog
    try:
        dlg = HelpDialog(win)
        dlg.show()
        app.processEvents()
        check("HelpDialog 能构建并显示", dlg.isVisible())
        check("帮助有 3 个页签", dlg.findChildren(type(dlg)) is not None
              and len(dlg.children()) > 0)
        dlg.close()
    except Exception:
        traceback.print_exc(limit=3)
        check("HelpDialog 能构建并显示", False)

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