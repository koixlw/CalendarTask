"""给 README 生成截图。

关键点:
  - **不用 offscreen** —— 要看的就是真平台上长什么样 (offscreen 下字体渲染不一样)
  - **只抓 PyCal 自己的窗口** (widget.grab()) —— 不会把桌面上别的窗口
    (比如你正在写的代码、聊天软件) 一起拍进去
  - APPDATA 指到临时目录 —— 绝对不碰用户真实数据

用法:
    python tools/make_screenshots.py
    OUT=docs python tools/make_screenshots.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 默认 offscreen —— 不打扰用户桌面, CI 上也能跑。
# 想在**真平台**上截 (字体渲染更真实): REAL_PLATFORM=1 python tools/make_screenshots.py
if os.environ.get("REAL_PLATFORM") == "1":
    os.environ.pop("QT_QPA_PLATFORM", None)
else:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pycal_docs_shot_")

from PySide6.QtWidgets import QApplication

from pycal.db.connection import open_default
from pycal.db.models import CalendarItem
from pycal.db.repository import upsert_item

app = QApplication.instance() or QApplication(sys.argv)


def load_system_fonts() -> None:
    """把系统字体注册进 Qt 的字体库。

    offscreen 平台插件不碰系统字体库 —— families() 返回空,
    结果所有中文全渲染成方块 □□□。
    手动 addApplicationFont 把字体灌进去就能正常显示,
    这样截图既能保持离屏 (不打扰用户桌面), 又能看见真文字。
    """
    from PySide6.QtGui import QFontDatabase, QFont

    candidates = [
        "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/segoeui.ttf",    # Segoe UI (界面里的拉丁字)
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/consola.ttf",    # Consolas (等宽)
        "C:/Windows/Fonts/simsun.ttc",     # 宋体
        "C:/Windows/Fonts/simhei.ttf",     # 黑体
        "C:/Windows/Fonts/symbol.ttf",     # 符号字体
        "C:/Windows/Fonts/seguiemj.ttf",   # Emoji
    ]
    loaded = 0
    for path in candidates:
        if Path(path).exists():
            if QFontDatabase.addApplicationFont(path) >= 0:
                loaded += 1
    fams = QFontDatabase.families()
    print(f"fonts: loaded {loaded}/{len(candidates)}, "
          f"families={len(fams)}")
    if not fams:
        print("警告: 一个字体都没加载到, 截图里的中文会是方块")
        return
    # 挑一个能显示中文的当默认
    for want in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun"):
        if want in fams:
            app.setFont(QFont(want, 9))
            print(f"default font: {want}")
            return


load_system_fonts()

OUT = Path(os.environ.get("OUT", str(ROOT / "docs")))
OUT.mkdir(parents=True, exist_ok=True)

# ── 造点数据, 免得截出来全是空格子 ─────────────────────────────────
SEED = [
    ("写周报\n修同步 bug\n回邮件", 0, ""),
    ("[+] 站会 10:00", 0, ""),
    ("交周报", 0, ""),
    ("[+] 买咖啡", 0, ""),
    ("每日跑步 3km", 1, "每天跑步"),
    ("牙医预约", 2, "看牙医"),
    ("产品评审", 3, "产品评审"),
]

con = open_default()
today = date.today()
month_start = today.replace(day=1)
for i, (content, t, title) in enumerate(SEED):
    d = month_start + timedelta(days=i * 3)
    upsert_item(con, CalendarItem(
        item_id=f"shot-{i}", date=d.isoformat(), type=t, title=title,
        content=content, is_completed=1 if content.startswith("[+]") else 0,
    ))
# 今天也放两条, 让「今天」那一格不是空的
for i, (content, t, _title) in enumerate(SEED[:3]):
    upsert_item(con, CalendarItem(
        item_id=f"today-{i}", date=today.isoformat(), type=t, title="",
        content=content, is_completed=1 if content.startswith("[+]") else 0,
    ))


def settle(w, rounds: int = 6) -> None:
    """让窗口把布局、字体、皮肤都算完再截图。"""
    w.show()
    for _ in range(rounds):
        app.processEvents()


def shoot(widget, name: str) -> None:
    settle(widget)
    path = OUT / f"{name}.png"
    widget.grab().save(str(path))
    print(f"saved: {path}")


# ── 1. 主窗口 (窗口模式) ────────────────────────────────────────────
from pycal.ui.main_window import PyCalMainWindow

win = PyCalMainWindow(app, fullscreen=False)
# 工具栏带文字标签要 >= 1460px (_COMPACT_UNDER), 窄了就只剩图标
win.resize(1560, 840)
shoot(win, "main-window")

# ── 2. 年视图 ──────────────────────────────────────────────────────
win._toggle_year_view(True)
shoot(win, "year-view")
win._toggle_year_view(False)

# ── 3. 快捷待办面板 ────────────────────────────────────────────────
from pycal.ui.quick_panel import QuickPanel

panel = QuickPanel()
panel.resize(panel.width(), panel.sizeHint().height())
shoot(panel, "quick-panel")
panel.close()

# ── 4. 桌面挂件 (贴在桌面层的小月历) ────────────────────────────────
from pycal.ui.desktop_widget import DesktopWidget

widget = DesktopWidget()
shoot(widget, "desktop-widget")
widget.close()

win.close()
print("done")
