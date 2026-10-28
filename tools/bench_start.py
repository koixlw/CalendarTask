"""启动耗时基准 — 进程启动 → 主窗可交互。

用法:
    python tools/bench_start.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

BENCH_DIR = tempfile.mkdtemp(prefix="pycal_bench_")
os.environ["APPDATA"] = BENCH_DIR          # 数据库重定向到临时目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

T0 = time.perf_counter()

from PySide6.QtWidgets import QApplication, QWidget

t_qt = time.perf_counter() - T0

t = time.perf_counter()
from pycal.ui.main_window import PyCalMainWindow
t_import = time.perf_counter() - t

app = QApplication([])

t = time.perf_counter()
win = PyCalMainWindow(app, fullscreen=False)
t_build = time.perf_counter() - t

t = time.perf_counter()
win.show()
app.processEvents()
t_show = time.perf_counter() - t

n_widgets = 1 + len(win.findChildren(QWidget))

# 第一次翻月 (会按需建出动画用的第二个月视图)
t = time.perf_counter()
win._active_month_view.goto(2026, 7)
app.processEvents()
t_flip = time.perf_counter() - t

# 第一次切年视图 (会按需建出年视图: 12 个小月 + 一整块月视图)
t = time.perf_counter()
win._toggle_year_view(True)
app.processEvents()
t_year = time.perf_counter() - t

n_widgets2 = 1 + len(win.findChildren(QWidget))

print("=" * 58)
print(f"Qt 初始化              : {t_qt * 1000:8.1f} ms")
print(f"pycal 模块导入         : {t_import * 1000:8.1f} ms")
print(f"主窗构造               : {t_build * 1000:8.1f} ms")
print(f"show() + 首帧          : {t_show * 1000:8.1f} ms")
print(f"── 可交互 (构造+首帧)  : {(t_build + t_show) * 1000:8.1f} ms")
print(f"第一次翻月             : {t_flip * 1000:8.1f} ms")
print(f"第一次切年视图         : {t_year * 1000:8.1f} ms")
print(f"widget 数 (月视图)     : {n_widgets:8d}")
print(f"widget 数 (开过年视图) : {n_widgets2:8d}")
print("=" * 58)
