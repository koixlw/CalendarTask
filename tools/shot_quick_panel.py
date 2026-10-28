"""把快捷待办面板截到真桌面上 — 用来肉眼检查排版、字和圆角。

用法:
    python tools/shot_quick_panel.py            # 真桌面 + 造几条示例数据
    OUT=D:/x.png python tools/shot_quick_panel.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 不用 offscreen —— 要看的就是真平台上长什么样
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pycal_quick_shot_")

from datetime import date

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pycal.db.connection import open_default
from pycal.db.models import CalendarItem
from pycal.db.repository import upsert_item

app = QApplication.instance() or QApplication(sys.argv)

# 造点数据, 免得截出来只有一个空框
con = open_default()
today = date.today()
seed = [
    ("写周报", 0),
    ("[+] 回邮件", 0),
    ("改同步 bug", 0),
    ("站会", 1),
    ("牙医预约", 2),
    ("产品评审", 3),
]
for i, (content, t) in enumerate(seed):
    upsert_item(con, CalendarItem(
        item_id=f"qshot-{i}", date=today.isoformat(), type=t,
        title="" if t == 0 else f"示例 {t}", content=content,
        is_completed=1 if content.startswith("[+]") else 0,
    ))

from pycal.ui.quick_panel import QuickPanel

panel = QuickPanel()
panel.show()
panel.raise_()


def grab():
    out = Path(os.environ.get("OUT", str(ROOT / "build" / "quick_panel_shot.png")))
    out.parent.mkdir(parents=True, exist_ok=True)

    # 1) 面板自己 (带透明圆角)
    panel.grab().save(str(out))

    # 2) 整屏一张 —— 看它在桌面上跟别的窗口挤在一起什么样
    screen = app.primaryScreen()
    screen.grabWindow(0).save(str(out.with_name(out.stem + "_screen.png")))

    print(f"saved {out}  panel at ({panel.x()},{panel.y()}) "
          f"{panel.width()}x{panel.height()}")
    app.quit()


QTimer.singleShot(2500, grab)
sys.exit(app.exec())
