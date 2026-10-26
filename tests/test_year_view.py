"""年视图测试 — 12 个小月 + 下方详情, 以及主窗的月/年切换。

覆盖:
  1. MiniMonth — 42 天从星期一开始, 点日期/点标题各发各的信号
  2. YearView — 12 个小月、换年重建、跨月跨年翻页、小月上的条目统计
  3. 主窗口 — 年/月视图切换, ◀ ▶ 在年视图下走的是详情里的月

跑:  python tests/test_year_view.py
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

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

PASS, FAIL = [], []


def check(label: str, fn):
    try:
        fn()
        PASS.append(label)
        print(f"  [OK]   {label}")
    except Exception as e:
        FAIL.append(f"{label}: {type(e).__name__}: {e}")
        print(f"  [FAIL] {label}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _click(widget, x, y):
    """合成一次左键按下。"""
    ev = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(ev)


def _cell_pos(mm, day_iso):
    """算出某一天在小月里的坐标 (和 MiniMonth._geometry 一致的算法)。"""
    days = {d.isoformat(): i for i, d in enumerate(mm._days)}
    idx = days[day_iso]
    row, col = divmod(idx, 7)
    r, title_h, head_h, cell_w, cell_h = mm._geometry()
    days_top = r.top() + title_h + head_h
    return (r.left() + col * cell_w + cell_w // 2,
            days_top + row * cell_h + cell_h // 2)


def main() -> int:
    # DB 指到临时目录 — 别碰用户真实数据
    tmp = tempfile.mkdtemp(prefix="pycal_test_year_")
    os.environ["APPDATA"] = tmp
    from pycal.db.connection import open_default, close_default
    close_default()

    app = QApplication.instance() or QApplication([])

    from pycal.db.repository import upsert_item
    from pycal.db.models import CalendarItem
    from pycal.ui.year_view import MiniMonth, YearView

    print("=" * 70)
    print("年视图测试")
    print("=" * 70)

    # ------------------------------------------------------------------
    print("\n=== 1. MiniMonth ===")

    mm = MiniMonth(2026, 10)
    mm.resize(340, 96)

    check("一个月是 42 天 (6 行 × 7 列)", lambda: _assert(
        len(mm._days) == 42, f"实际 {len(mm._days)} 天",
    ))

    check("第一天是星期一", lambda: _assert(
        mm._days[0].weekday() == 0,
        f"第一天是 {mm._days[0]} (weekday={mm._days[0].weekday()})",
    ))

    check("第一天落在当月 1 号那周的周一", lambda: _assert(
        mm._days[0] <= date(2026, 10, 1) <= mm._days[6],
        f"第一周 {mm._days[0]}~{mm._days[6]} 不含 10 月 1 号",
    ))

    check("星期顺序是周一~周日", lambda: _assert(
        [d.weekday() for d in mm._days[:7]] == [0, 1, 2, 3, 4, 5, 6],
        [d.weekday() for d in mm._days[:7]],
    ))

    check("10 月 31 号在网格里", lambda: _assert(
        date(2026, 10, 31) in mm._days, "找不到 10 月 31 号",
    ))

    # 点某一天
    got_day: list[str] = []
    got_month: list[tuple[int, int]] = []
    mm.sig_day_clicked.connect(got_day.append)
    mm.sig_month_clicked.connect(lambda y, m: got_month.append((y, m)))

    x, y = _cell_pos(mm, "2026-10-14")
    _click(mm, x, y)
    check("点日期发 sig_day_clicked", lambda: _assert(
        got_day == ["2026-10-14"], got_day,
    ))

    _click(mm, 20, 4)      # 标题那一行
    check("点标题发 sig_month_clicked", lambda: _assert(
        got_month == [(2026, 10)], got_month,
    ))

    check("小月知道自己是谁", lambda: _assert(
        mm.year == 2026 and mm.month == 10, (mm.year, mm.month),
    ))

    # ------------------------------------------------------------------
    print("\n=== 2. YearView ===")

    yv = YearView()
    yv.resize(1200, 800)
    QApplication.processEvents()

    check("有 12 个小月", lambda: _assert(
        len(yv._minis) == 12, f"实际 {len(yv._minis)} 个",
    ))

    check("小月排成 3 行 × 4 列", lambda: _assert(
        [m.month for m in yv._minis] == list(range(1, 13)),
        [m.month for m in yv._minis],
    ))

    check("小月都属于同一年", lambda: _assert(
        all(m.year == yv._year for m in yv._minis),
        [m.year for m in yv._minis],
    ))

    check("下方详情是月视图 (42 格)", lambda: _assert(
        len(yv.detail_view._cells) == 42, len(yv.detail_view._cells),
    ))

    yv.goto(2026, 10)
    QApplication.processEvents()
    check("goto 之后详情停在那个月", lambda: _assert(
        yv.detail_view.current_month() == (2026, 10),
        yv.detail_view.current_month(),
    ))

    check("标题写的是「年 · 全年总览」", lambda: _assert(
        "全年总览" in yv._year_lbl.text(), yv._year_lbl.text(),
    ))

    check("选中的那个月被标记出来", lambda: _assert(
        [m._selected for m in yv._minis]
        == [False] * 9 + [True] + [False] * 2,
        [(m.month, m._selected) for m in yv._minis],
    ))

    yv.shift_month(1)
    check("往后翻一个月到 11 月", lambda: _assert(
        yv.current_month() == (2026, 11), yv.current_month(),
    ))

    yv.shift_month(2)
    check("12 月再翻 → 跨到下一年 1 月", lambda: _assert(
        yv.current_month() == (2027, 1), yv.current_month(),
    ))
    check("跨年后小月重建成新的一年", lambda: _assert(
        all(m.year == 2027 for m in yv._minis), yv._minis[0].year,
    ))

    yv.shift_year(-1)
    check("翻年不会改选中的月份", lambda: _assert(
        yv.current_month() == (2026, 1), yv.current_month(),
    ))

    # 条目统计
    con = open_default()
    upsert_item(con, CalendarItem(
        item_id="yv-1", date="2026-03-05", type=0, title="", content="A",
    ))
    upsert_item(con, CalendarItem(
        item_id="yv-2", date="2026-03-05", type=0, title="", content="B",
    ))
    yv.goto(2026, 3)
    QApplication.processEvents()

    march = yv._minis[2]
    check("有两天条目的那天统计是 2", lambda: _assert(
        march._counts.get("2026-03-05") == 2, march._counts,
    ))
    check("没条目的那天不标", lambda: _assert(
        march._counts.get("2026-03-06") is None, march._counts,
    ))

    check("点某一天会跳到那天", lambda: (
        yv._on_day_clicked("2026-03-05"),
        _assert(yv.current_month() == (2026, 3), yv.current_month()),
        _assert(yv.detail_view.current_month() == (2026, 3),
                yv.detail_view.current_month()),
    ))

    check("换皮肤不炸", lambda: (
        yv.apply_skin("dark_mode"),
        yv.apply_skin("light_default"),
    ))

    check("调字号不炸", lambda: (
        yv.apply_font_scale(1.3),
        yv.apply_font_scale(1.0),
    ))

    # ------------------------------------------------------------------
    print("\n=== 3. 主窗口的月/年切换 ===")

    from pycal.ui.main_window import PyCalMainWindow
    win = PyCalMainWindow(app, fullscreen=False)
    win.resize(1400, 900)
    win.show()
    QApplication.processEvents()

    check("默认是月视图", lambda: _assert(
        not win._is_year_view(), "默认应该是月视图",
    ))

    win._active_month_view.goto(2026, 10)
    win._toggle_year_view(True)
    QApplication.processEvents()

    check("切到年视图后栈切到第 2 页", lambda: _assert(
        win._view_stack.currentIndex() == 1, win._view_stack.currentIndex(),
    ))

    check("年视图里带着原来那个月", lambda: _assert(
        win._year_view.current_month() == (2026, 10),
        win._year_view.current_month(),
    ))

    check("工具栏按钮跟着勾上", lambda: _assert(
        win._btn_year.isChecked(), "年视图按钮没勾选",
    ))

    win.shift_month(1)
    check("年视图下 ◀ ▶ 走的是详情里的月", lambda: _assert(
        win._year_view.current_month() == (2026, 11),
        win._year_view.current_month(),
    ))

    check("翻月后工具栏标签跟着走", lambda: _assert(
        "11" in win._month_label.text(), win._month_label.text(),
    ))

    check("点小月也会换月 + 标签跟上", lambda: (
        win._year_view._on_month_clicked(2026, 3),
        _assert(win._year_view.current_month() == (2026, 3),
                win._year_view.current_month()),
        _assert("03" in win._month_label.text(), win._month_label.text()),
    ))

    win.goto_today()
    check("「今天」回到当月", lambda: _assert(
        win._year_view.current_month()
        == (date.today().year, date.today().month),
        win._year_view.current_month(),
    ))

    win._toggle_year_view(False)
    QApplication.processEvents()

    check("切回月视图后停在同一个月", lambda: _assert(
        win._active_month_view.current_month()
        == win._year_view.current_month(),
        (win._active_month_view.current_month(),
         win._year_view.current_month()),
    ))

    check("切回月视图后按钮取消勾选", lambda: _assert(
        not win._btn_year.isChecked(), "年视图按钮还勾着",
    ))

    check("F5 刷新不炸", lambda: win.refresh_view())

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
