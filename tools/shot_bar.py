"""用真实 windows 平台渲染工具栏 (真实中文字体), 输出 PNG 供肉眼检查。

用法:  python tools/shot_bar.py [输出名]
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# 注意: 不要设 QT_QPA_PLATFORM=offscreen — 那样中文全是豆腐块, 看不出效果
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pycal_shot_")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)

from pycal.db.connection import open_default
from pycal.db.models import CalendarItem
from pycal.db.repository import upsert_item
from datetime import date

con = open_default()
today = date.today()
for i, (content, t) in enumerate([
    ("写日报\n修同步 bug\n回邮件", 0),
    ("[+] 站会", 1),
    ("交周报", 0),
    ("🔔 牙医预约", 2),
    ("产品评审", 3),
]):
    upsert_item(con, CalendarItem(
        item_id=f"shot-{i}", date=today.isoformat(), type=t,
        title="" if t == 0 else f"示例 {t}", content=content,
        is_completed=1 if content.startswith("[+]") else 0,
    ))

from pycal.ui.main_window import PyCalMainWindow

win = PyCalMainWindow(app, fullscreen=False)
win.resize(1280, 820)
win.show()


def shoot():
    out = ROOT / "build" / (sys.argv[1] if len(sys.argv) > 1
                            else "shot_toolbar.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    win.grab().save(str(out))
    print(f"saved: {out}")
    app.quit()


QTimer.singleShot(700, shoot)
app.exec()