"""桌面挂件回归测试 —— 挂件能不能贴在桌面上、字色会不会跟着壁纸反。

跑:  python tests/test_desktop_widget.py
(offscreen 平台, Windows 无显示环境也能跑)
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pycal.console import setup_stdout
# Windows 中文控制台默认 GBK, 打印 ✓/◀/☐ 会抛 UnicodeEncodeError
setup_stdout()


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pycal_widget_")

from PySide6.QtCore import Qt, QRect
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
    print("桌面挂件测试")
    print("=" * 70)

    from pycal.ui.desktop_widget import (
        DesktopWidget, DesktopWidgetManager, DARK_SCHEME, LIGHT_SCHEME,
    )
    from pycal.ui.month_view import MonthView, colors_for_skin

    # ── 1. 透明皮肤 ──
    print("\n=== 挂件皮肤 ===")
    colors = colors_for_skin("desktop_widget")
    check("格子底色是透明的 (壁纸透得出来)",
          colors.body_bg == "transparent" and colors.muted_bg == "transparent",
          f"body_bg={colors.body_bg}")
    check("透明模式带文字描边", bool(colors.halo), "halo 为空")
    check("普通皮肤不受影响",
          colors_for_skin("light_default").body_bg == "#FFFFFF"
          and colors_for_skin("light_default").transparent is False)

    # ── 2. 挂件本身 ──
    print("\n=== 挂件窗口 ===")
    w = DesktopWidget()
    w.show()
    app.processEvents()
    check("挂件能构建并显示", w.isVisible())
    check("挂件带月视图", w._month is not None
          and len(w._month._cells) in (28, 35, 42))
    check("月视图用透明皮肤", w._month._colors.transparent)
    check("标题显示当前年月", "年" in w._title.text() and "月" in w._title.text(),
          w._title.text())
    check("层级默认在桌面层 (不是置顶)",
          bool(w.windowFlags() & Qt.WindowType.WindowStaysOnBottomHint))
    check("层级默认不是置顶",
          not (bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)))
    check("窗口标志里有 Tool (不进任务栏)",
          bool(w.windowFlags() & Qt.WindowType.Tool))
    check("半透明背景已开",
          w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))

    # ── 3. 字色跟着壁纸反 ──
    print("\n=== 字色方案 ===")
    w._scheme_mode = "dark"
    w._apply_scheme()
    check("深色字模式字色变深",
          w._month._colors.num_fg == DARK_SCHEME["num_fg"],
          w._month._colors.num_fg)
    check("深色字模式描边变浅 (亮壁纸上看得清)",
          w._month._colors.halo == DARK_SCHEME["halo"])
    w._scheme_mode = "light"
    w._apply_scheme()
    check("浅色字模式字色变浅",
          w._month._colors.num_fg == LIGHT_SCHEME["num_fg"])
    check("标题也跟着换色", w._title.ink() == LIGHT_SCHEME["num_fg"])
    w._scheme_mode = "auto"
    w._apply_scheme()

    # ── 4. 层级切换 ──
    print("\n=== 层级 / 大小 ===")
    w._set_layer("top")
    app.processEvents()
    check("切到永远置顶", bool(w.windowFlags()
                             & Qt.WindowType.WindowStaysOnTopHint))
    check("置顶后不再占桌面层",
          not (bool(w.windowFlags() & Qt.WindowType.WindowStaysOnBottomHint)))
    w._set_layer("bottom")
    app.processEvents()
    check("切回桌面层", bool(w.windowFlags()
                            & Qt.WindowType.WindowStaysOnBottomHint))

    old_scale = w._scale
    w._set_scale("0.70")
    check("改大小后字号跟着变", w._scale != old_scale
          and w._fonts.cell_min_w < 64, f"scale={w._scale}")
    w._set_scale("0.85")

    # ── 5. 位置会被记住 ──
    print("\n=== 位置记忆 ===")
    from pycal.settings import get_setting, set_setting
    w.move(137, 251)
    w._save_geometry()
    check("位置写进 settings", get_setting("widget_x") == "137"
          and get_setting("widget_y") == "251",
          f"{get_setting('widget_x')},{get_setting('widget_y')}")
    w2 = DesktopWidget()
    check("重开挂件回到老位置", (w2.x(), w2.y()) == (137, 251),
          f"{(w2.x(), w2.y())}")
    w2.close()

    # ── 6. 真能画出来 (描边那段是最容易炸的地方) ──
    print("\n=== 渲染 ===")
    try:
        pix = QPixmap(w.size())
        w.render(pix)
        check("挂件能渲染成图片", not pix.isNull())
    except Exception as e:
        traceback.print_exc(limit=3)
        check("挂件能渲染成图片", False, str(e))

    # ── 6b. 格子里的字不能溢出格子 (窗口比格子矮就会切掉最后一行) ──
    over = []
    for cell in w._month._cells.values():
        if cell.isHidden():
            continue
        need = cell._lay.minimumSize().height()
        if cell.height() < need:
            over.append(f"{cell.day_num}号 {cell.height()}<{need}")
    check("格子内容不溢出格子", not over, "; ".join(over[:3]))
    check("窗口高度装得下所有周", w.height() >= w._month.sizeHint().height(),
          f"窗口 {w.height()} / 需要 {w._month.sizeHint().height()}")

    # ── 7. 管理器开关 ──
    print("\n=== 管理器 ===")
    from pycal.ui.main_window import PyCalMainWindow
    mw = PyCalMainWindow(app, fullscreen=False)
    mgr = mw._widget_mgr
    check("管理器建好了", mgr is not None)
    mgr.show()
    app.processEvents()
    check("管理器能显示挂件", mgr.visible())
    check("显示状态存进 settings",
          get_setting("desktop_widget", "0") == "1")
    mgr.toggle()
    app.processEvents()
    check("再点一次收起来", not mgr.visible())
    check("收起状态也存了", get_setting("desktop_widget", "1") == "0")
    mgr.toggle()
    app.processEvents()
    check("还能再打开", mgr.visible())

    # ── 8. 亮度采样不出错 ──
    print("\n=== 壁纸亮度 ===")
    from pycal.desktop.wallpaper import sample_brightness
    from PySide6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen()
    b = sample_brightness(screen, QRect(0, 0, 200, 200))
    check("亮度采样返回 0~1", 0.0 <= b <= 1.0, f"b={b}")

    # ── 9. 挂件菜单 ──
    print("\n=== 挂件菜单 ===")
    menu = w._build_menu()
    texts = [a.text() for a in menu.actions() if a.text()]
    joined = " | ".join(texts)
    for must in ("打开 PyCal 主窗口", "回到今天", "贴在桌面层", "永远置顶",
                 "深色字", "浅色字", "小", "中", "大", "隐藏挂件", "退出 PyCal"):
        check(f"菜单含「{must}」", must in joined)
    no_desc = [a.text() for a in menu.actions()
               if a.text() and not a.isSeparator() and a.isEnabled()
               and not a.toolTip()]
    check("菜单项都有说明", not no_desc, f"缺说明: {no_desc}")
    check("当前层级有勾", sum(
        1 for a in menu.actions()
        if a.isCheckable() and a.isChecked()) >= 2)

    mw.close()
    w.close()
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
