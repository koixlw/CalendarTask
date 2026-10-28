"""PyCal 性能基准 — 绘制 / 缩放 / 搜索 / 翻月 的实测耗时。

只写临时目录里的临时数据库 (APPDATA 被重定向), 不碰用户数据。

用法:
    python tools/bench_perf.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

BENCH_DIR = tempfile.mkdtemp(prefix="pycal_bench_")
os.environ["APPDATA"] = BENCH_DIR          # 数据库重定向到临时目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QWidget

app = QApplication([])

from pycal.db.connection import open_default
from pycal.db.models import CalendarItem
from pycal.db.repository import upsert_item
import pycal.ui.main_window as mw

# ── 造一个"满月"的数据集: 60 天 × 8 条, 每条 4 行 ────────────────────
con = open_default()
rows = []
d0 = date(2026, 3, 1)
for i in range(60):
    day = (d0 + timedelta(days=i)).isoformat()
    for j in range(8):
        rows.append(CalendarItem(
            item_id=f"m{i}-{j}", date=day, type=0, title=f"条目 {j}",
            content="\n".join(f"[+] 第{k}行内容示例文本" for k in range(4)),
            color_ref=0,
        ))
con.execute("BEGIN")
for it in rows:
    upsert_item(con, it)
con.commit()
print(f"造了 {len(rows)} 条 (60 天 x 8 条, 每天 4 行)\n")

win = mw.PyCalMainWindow(app, fullscreen=False)
win.resize(1400, 900)
win.show()
app.processEvents()

mv = win._active_month_view
mv.goto(2026, 3)
app.processEvents()

print("-" * 64)
times = []
for w in (1400, 1300, 1200, 1100, 1000):
    t = time.perf_counter()
    win.resize(w, 900)
    app.processEvents()
    times.append((time.perf_counter() - t) * 1000)
print(f"resize + 重排 (满月)     : {sum(times) / len(times):8.1f} ms/次"
      f"  (min {min(times):.1f} max {max(times):.1f})")

n_rows = sum(len(c._rows) for c in mv._cells.values())
print(f"当前可见行数            : {n_rows}")

# ── 年视图绘制 (MiniMonth.paintEvent 每次新建 QFont) ──────────────────
win._set_view_mode("year")
app.processEvents()
yv = win._year_view

times = []
for _ in range(10):
    t = time.perf_counter()
    for m in yv._minis:
        m.update()
    app.processEvents()
    times.append((time.perf_counter() - t) * 1000)
print(f"\n年视图全量重绘 x10     : {sum(times) / len(times):8.1f} ms/次"
      f"  (min {min(times):.1f} max {max(times):.1f})")

times = []
for _ in range(10):
    t = time.perf_counter()
    yv._minis[0].update()
    app.processEvents()
    times.append((time.perf_counter() - t) * 1000)
print(f"年视图单个小月重绘 x10  : {sum(times) / len(times):8.1f} ms/次")

win._set_view_mode("month")
app.processEvents()

# ── 搜索 (FTS + LIKE 交叉验证 = 两次全表扫) ───────────────────────────
from pycal.search import SearchEngine, SearchOptions
eng = SearchEngine(con)
for kw in ("条目", "第0行", "abc"):
    t = time.perf_counter()
    hits = eng.search(SearchOptions(keyword=kw))
    print(f"搜索 {kw!r:12s}        : {(time.perf_counter() - t) * 1000:8.1f} ms"
          f"  ({len(hits)} 命中)")

# ── 勾选一条 (全量刷新整月) ──────────────────────────────────────────
t = time.perf_counter()
mv._handle_toggle("m0-0", 0, True)
print(f"\n勾选一条 (满月)         : {(time.perf_counter() - t) * 1000:8.1f} ms")

# ── 翻月 (销毁重建 42 个格子) ─────────────────────────────────────────
times = []
for i in range(5):
    t = time.perf_counter()
    mv.goto(2026, 4 + (i % 2))
    app.processEvents()
    times.append((time.perf_counter() - t) * 1000)
print(f"翻月 goto() (满月)       : {sum(times) / len(times):8.1f} ms/次")

print("-" * 64)
print("widget 总数:", 1 + len(win.findChildren(QWidget)))
