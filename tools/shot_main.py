"""把主窗口渲染成 PNG — 用来肉眼检查工具栏排版和格子里的勾选框。

用法:
    QT_QPA_PLATFORM=offscreen python tools/shot_main.py
    QT_QPA_PLATFORM=offscreen SKIN=dark_mode python tools/shot_main.py
"""

from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pycal_shot_")

from PySide6.QtWidgets import QApplication

from pycal.db.connection import open_default
from pycal.db.repository import upsert_item
from pycal.db.models import CalendarItem
from datetime import date, timedelta

app = QApplication.instance() or QApplication([])

# 造一点数据, 免得截出来全是空格子
con = open_default()
today = date.today()
seed = [
    ("写日报\n修同步 bug\n回邮件", 0),
    ("[+] 站会", 1),
    ("交周报", 0),
    ("🔔 牙医预约", 2),
    ("产品评审", 3),
]
for i, (content, t) in enumerate(seed):
    upsert_item(con, CalendarItem(
        item_id=f"shot-{i}", date=today.isoformat(), type=t,
        title="" if t == 0 else f"示例 {t}", content=content,
        is_completed=1 if content.startswith("[+]") else 0,
    ))

from pycal.ui.main_window import PyCalMainWindow

skin = os.environ.get("SKIN", "light_default")
win = PyCalMainWindow(app, fullscreen=False)
win._skin = __import__("pycal.ui.skin", fromlist=["x"]).current_skin(skin)
win._apply_skin()
win.resize(1280, 820)
win.show()
app.processEvents()
app.processEvents()

out = ROOT / "build" / f"shot_{skin}.png"
out.parent.mkdir(parents=True, exist_ok=True)
win.grab().save(str(out))
print(f"saved: {out}")