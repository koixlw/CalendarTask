"""把桌面挂件截到真实壁纸上 — 用来肉眼检查描边字和网格线。

用法:
    python tools/shot_widget.py            # 真桌面 + 真壁纸
    OUT=D:/x.png python tools/shot_widget.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 不用 offscreen —— 要的就是「贴在桌面上」那个效果
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pycal_widget_shot_")

from datetime import date, timedelta

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from pycal.db.connection import open_default
from pycal.db.models import CalendarItem
from pycal.db.repository import upsert_item

app = QApplication.instance() or QApplication(sys.argv)

# 造点数据, 免得截出来全是空格子
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
        item_id=f"wshot-{i}", date=today.isoformat(), type=t,
        title="" if t == 0 else f"示例 {t}", content=content,
        is_completed=1 if content.startswith("[+]") else 0,
    ))
for i in range(1, 5):
    upsert_item(con, CalendarItem(
        item_id=f"wshot-n{i}", date=(today + timedelta(days=i)).isoformat(),
        type=0, content=f"第 {i} 天的事",
    ))

from pycal.ui.desktop_widget import DesktopWidget

w = DesktopWidget()
w.show()


def grab():
    """把挂件抠出来, 贴到真壁纸上 — 桌面层会被别的窗口盖住, 直接截屏看不到。"""
    from PySide6.QtGui import QPainter, QImage
    from pycal.desktop.wallpaper import _read_windows_original_wallpaper

    out = Path(os.environ.get("OUT", str(ROOT / "build" / "widget_shot.png")))

    # 1) 挂件自己那层 (带透明通道)
    wpix = w.grab()
    # 2) 壁纸
    wpath = _read_windows_original_wallpaper()
    if wpath is None:
        print("no wallpaper found")
        app.quit()
        return
    screen = app.primaryScreen()
    geo = screen.geometry()
    base = QImage(str(wpath))
    scaled = base.scaled(
        geo.size(),
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x0 = max(0, (scaled.width() - geo.width()) // 2)
    y0 = max(0, (scaled.height() - geo.height()) // 2)
    canvas = scaled.copy(x0, y0, geo.width(), geo.height())

    # 3) 合成 — 挂件在屏幕上的位置
    p = QPainter(canvas)
    p.drawPixmap(w.x() - geo.x(), w.y() - geo.y(), wpix)
    p.end()
    canvas.save(str(out))
    print(f"saved {out}  ({canvas.width()}x{canvas.height()})  "
          f"widget at ({w.x()},{w.y()}) {w.width()}x{w.height()}")
    app.quit()


QTimer.singleShot(2500, grab)
sys.exit(app.exec())
