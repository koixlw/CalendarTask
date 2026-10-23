"""渲染「更多」菜单和帮助对话框, 输出 PNG 供肉眼检查。

用法:  python tools/shot_menu.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pycal_menu_")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QPoint

app = QApplication.instance() or QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)

from pycal.ui.main_window import PyCalMainWindow
from pycal.ui.help_dialog import HelpDialog

win = PyCalMainWindow(app, fullscreen=False)
win.resize(1280, 820)
win.show()


def shoot_all():
    out_dir = ROOT / "build"

    # 1) 帮助对话框 — 三个页签各截一张
    dlg = HelpDialog(win)
    dlg.resize(820, 560)
    dlg.show()
    app.processEvents()
    from PySide6.QtWidgets import QTabWidget
    tabs = dlg.findChild(QTabWidget)
    for i, name in enumerate(["basics", "features", "keys"]):
        tabs.setCurrentIndex(i)
        app.processEvents()
        dlg.grab().save(str(out_dir / f"shot_help_{name}.png"))

    # 2) 「更多」菜单 — 展开后抓图
    menu = win._btn_more.menu()
    menu.popup(win.mapToGlobal(QPoint(700, 40)))
    app.processEvents()
    menu.grab().save(str(out_dir / "shot_more_menu.png"))
    menu.close()

    print("saved: build/shot_help.png, build/shot_more_menu.png")
    app.quit()


QTimer.singleShot(800, shoot_all)
app.exec()