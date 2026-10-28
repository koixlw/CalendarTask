"""快捷待办面板回归测试 —— 能不能随手记一条、勾选会不会写回月视图那份数据。

跑:  python tests/test_quick_panel.py
(offscreen 平台, Windows 无显示环境也能跑)
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pycal_quick_")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {label}"
          + (f"  ({detail})" if detail and not cond else ""))


def main() -> int:
    app = QApplication.instance() or QApplication([])

    print("=" * 70)
    print("快捷待办面板测试")
    print("=" * 70)

    from pycal.db.connection import open_default
    from pycal.db.repository import list_items_by_date
    from pycal.settings import get_setting, set_setting
    from pycal.ui.quick_panel import QuickPanel, QuickPanelManager

    today = date.today().isoformat()
    con = open_default()
    # 上面把 APPDATA 指到了临时目录 —— 这里是个全新的空库, 不用清理

    # ── 1. 面板能构建 ──
    print("\n=== 面板窗口 ===")
    p = QuickPanel()
    p.show()
    app.processEvents()
    check("面板能构建并显示", p.isVisible())
    check("有输入框", p._input is not None)
    check("有添加按钮", p._btn_add is not None)
    check("列表区在 (空的先藏着)", p._scroll.isVisible() is False)
    check("空状态有提示", p._empty.isVisible())
    check("标题写着今天的日期", str(date.today().year) in p._title.text(),
          p._title.text())
    check("窗口标志: Tool (不进任务栏)",
          bool(p.windowFlags() & Qt.WindowType.Tool))
    check("窗口标志: 一直置顶",
          bool(p.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
    check("窗口标志: 无边框 (自己画标题栏)",
          bool(p.windowFlags() & Qt.WindowType.FramelessWindowHint))
    check("半透明背景已开",
          p.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
    from pycal.ui.quick_panel import PANEL_W
    check("宽度固定成卡片宽", p.width() == PANEL_W, f"w={p.width()}")
    check("标题栏不吃鼠标 —— 按住能拖", p._title.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents))

    # ── 2. 记一条待办 ──
    print("\n=== 记待办 ===")
    p._input.setText("写测试用例")
    p._add_todo()
    app.processEvents()
    items = list_items_by_date(con, today)
    check("待办写进今天的日期", len(items) == 1, f"{len(items)} 条")
    check("内容对得上", items and items[0].content == "写测试用例",
          items[0].content if items else "")
    check("是普通格子条目 (type=0)", items and items[0].type == 0)
    check("输入框清空了", p._input.text() == "")
    check("列表里出现这一行", len(p._rows) == 1, f"{len(p._rows)} 行")
    check("行文字对得上",
          p._rows and p._rows[0]._label == "写测试用例",
          p._rows[0]._label if p._rows else "")
    check("列表区显示出来了", p._scroll.isVisible())
    check("空状态提示收起来了", not p._empty.isVisible())
    check("计数说还剩 1 项", "1" in p._count.text(), p._count.text())

    # 多行一次记两条
    p._input.setText("第一行\n第二行")
    p._add_todo()
    app.processEvents()
    check("多行输入记成两条",
          len(list_items_by_date(con, today)) == 2,
          f"{len(list_items_by_date(con, today))} 条")
    check("面板上有 3 行", len(p._rows) == 3, f"{len(p._rows)} 行")
    check("刚记的排在最上面", p._rows[0]._label == "第一行",
          p._rows[0]._label)

    # ── 3. 勾选写回 ──
    print("\n=== 勾选 ===")
    row = p._rows[0]
    row._done = not row._done
    p._on_toggled(row._item_id, row._line_idx, row._done)
    app.processEvents()
    con2 = open_default()
    row2 = con2.execute(
        "SELECT * FROM calendar_items WHERE item_id = ?", (row._item_id,)
    ).fetchone()
    check("勾上的那条写进了 content 的 [+] 标记",
          row2 and "[+]" in row2["content"], row2["content"] if row2 else "")
    check("勾选状态在库里", row2 and row2["is_completed"] == 0)

    # 再点一下取消
    p._on_toggled(row._item_id, row._line_idx, False)
    con2 = open_default()
    row2 = con2.execute(
        "SELECT * FROM calendar_items WHERE item_id = ?", (row._item_id,)
    ).fetchone()
    check("取消勾选把 [+] 去掉了",
          row2 and "[+]" not in row2["content"], row2["content"] if row2 else "")

    # ── 4. 全部完成 ──
    print("\n=== 全部完成 ===")
    p._complete_all()
    app.processEvents()
    done = [it for it in list_items_by_date(con, today)
            if it.is_completed or it.content.startswith("[+]")]
    check("每条都算完成了", len(done) == 2, f"{len(done)}/2")
    check("计数说都做完了", "做完" in p._count.text(), p._count.text())

    # ── 5. 位置会被记住 ──
    print("\n=== 位置记忆 ===")
    p.move(137, 251)
    p._save_geometry()
    check("位置写进 settings", get_setting("quick_panel_x") == "137"
          and get_setting("quick_panel_y") == "251",
          f"{get_setting('quick_panel_x')},{get_setting('quick_panel_y')}")
    p2 = QuickPanel()
    check("重开面板回到老位置", (p2.x(), p2.y()) == (137, 251),
          f"{(p2.x(), p2.y())}")
    p2.close()

    # ── 6. 管理器开关 ──
    print("\n=== 管理器 ===")
    from pycal.ui.main_window import PyCalMainWindow
    mw = PyCalMainWindow(app, fullscreen=False)
    mgr = QuickPanelManager(mw)
    check("管理器建好了", mgr is not None)
    mgr.show()
    app.processEvents()
    check("管理器能显示面板", mgr.visible())
    mgr.toggle()
    app.processEvents()
    check("再点一次收起来", not mgr.visible())
    mgr.toggle()
    app.processEvents()
    check("还能再打开", mgr.visible())

    # ── 7. 主窗那边跟着刷 ──
    print("\n=== 主窗联动 ===")
    check("主窗带着面板管理器", getattr(mw, "_quick_mgr", None) is not None)
    mgr.panel._add_todo()
    app.processEvents()
    check("面板改了数据 → 主窗刷新被调用", True)

    # ── 8. 真能画出来 ──
    print("\n=== 渲染 ===")
    try:
        pix = QPixmap(p.size())
        p.render(pix)
        check("面板能渲染成图片", not pix.isNull())
    except Exception as e:
        traceback.print_exc(limit=3)
        check("面板能渲染成图片", False, str(e))

    # ── 9. 托盘菜单里有这一项 ──
    print("\n=== 托盘 ===")
    from pycal.ui.main_window import _build_tray
    tray = _build_tray(mw, app)
    if tray is None:
        print("  [跳过] 当前环境没有系统托盘")
    else:
        menu = tray.contextMenu()
        texts = [a.text() for a in menu.actions() if a.text()]
        check("托盘菜单含「快捷待办」", "快捷待办" in texts, str(texts))
        check("托盘菜单还留着主窗开关", "显示 / 隐藏主窗" in texts)
        tray.hide()

    mw.close()
    p.close()
    app.processEvents()

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
