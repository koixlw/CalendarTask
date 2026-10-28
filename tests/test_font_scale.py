"""字体缩放测试。

用户的原话: 「这个字太小了, 我想加一个功能, 字体大小能够自行调整」。
以前字号全写死在各个控件里 (条目行 8pt / 农历 7pt / 日期号 10pt),
现在收成 pycal.ui.fonts: 给一个比例算出全部字号 + 配套行高。

要验的:
  - 比例 → 字号 / 行高单调递增, 且行高跟得上字号 (不然字会被裁掉)
  - 越大的字, 一格塞得下的条数越少 (多了放不下)
  - 改字号能推到所有格子, 且立刻生效 (不用翻月 / 重启)
  - 存进 settings 后重新开窗口还在
  - 越界的比例 (手改数据库 / 连点 A+) 会被夹住, 不撑爆界面
  - 换皮肤不会把用户的字号打回默认

跑:  python tests/test_font_scale.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pycal_font_")

from PySide6.QtWidgets import QApplication

PASS, FAIL = [], []


def check(label: str, fn, detail: str = "") -> None:
    """fn 是传 detail 的 _assert(...) 调用 — 抛异常就记失败, 不中断整套测试。"""
    try:
        ok = bool(fn())
        err = ""
    except AssertionError as e:
        ok, err = False, str(e)
    except Exception as e:      # 测试里任何异常都算失败
        ok, err = False, f"{type(e).__name__}: {e}"
    (PASS if ok else FAIL).append(label)
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}"
          + (f"  ({err or detail})" if (err or detail) and not ok else ""))


def _assert(cond, detail: str = "") -> bool:
    if not cond:
        raise AssertionError(detail)
    return True


def _shown_rows(cell) -> int:
    return sum(1 for r in cell._rows if r.isVisible())


def main() -> int:
    global app
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)

    from pycal.ui.fonts import (
        SCALES, DEFAULT_SCALE, MAX_SCALE, fonts_for, max_rows_for,
        label_for_scale, scale_from_setting,
    )

    print("=" * 70)
    print("字体缩放测试")
    print("=" * 70)

    # ------------------------------------------------------------------
    print("\n=== 1. 字号随比例单调变大 ===")

    scales = [s for s, _ in SCALES] + [MAX_SCALE, 0.7]
    small, big = fonts_for(min(scales)), fonts_for(max(scales))
    check("所有字号都随比例变大", lambda: _assert(
        big.row > small.row and big.num > small.num
        and big.sub > small.sub and big.title > small.title
        and big.month > small.month,
        f"行 {small.row}->{big.row} / 标题 {small.title}->{big.title}",
    ))
    check("行高跟着字号走 (只放大字会裁字)", lambda: _assert(
        big.row_h > small.row_h and big.box_w > small.box_w,
        f"行高 {small.row_h}->{big.row_h}",
    ))
    check("工具栏 / 状态栏也跟着缩", lambda: _assert(
        big.toolbar > small.toolbar and big.status > small.status))
    check("标准档还是原来的字号 (行 8 / 号 10 / 农历 7 / 标题 12)", lambda: _assert(
        (fonts_for(DEFAULT_SCALE).row, fonts_for(DEFAULT_SCALE).num,
         fonts_for(DEFAULT_SCALE).sub, fonts_for(DEFAULT_SCALE).title)
        == (8, 10, 7, 12),
        "加了字号功能不该动默认档的观感",
    ))

    print("\n=== 2. 字越大, 每格塞得下的条数越少 ===")

    caps = [max_rows_for(s) for s, _ in SCALES]
    check("每档条数单调不增", lambda: _assert(
        all(a >= b for a, b in zip(caps, caps[1:])), str(caps)))
    check("标准档仍是每格 5 条", lambda: _assert(caps[1] == 5, str(caps)))
    check("再大的字也至少留 2 条", lambda: _assert(
        all(c >= 2 for c in caps + [max_rows_for(MAX_SCALE)])))

    print("\n=== 3. 越界的比例会被夹住 ===")

    check("调过头 (99 倍) 夹回上限", lambda: _assert(
        fonts_for(99).scale <= MAX_SCALE, str(fonts_for(99).scale)))
    check("调到负数夹回下限", lambda: _assert(
        fonts_for(-3).scale >= 0.7, str(fonts_for(-3).scale)))
    check("上限比例不会算出 0 号字", lambda: _assert(
        all(v >= 6 for v in (fonts_for(99).row, fonts_for(99).sub))))
    check("settings 里读出乱码回退到默认", lambda: _assert(
        scale_from_setting("abc") == DEFAULT_SCALE))
    check("档位比例有现成人话, 档位之间说自定义", lambda: _assert(
        label_for_scale(1.15) == "大 — 115%"
        and "自定义" in label_for_scale(1.22),
        label_for_scale(1.22),
    ))

    # ------------------------------------------------------------------
    print("\n=== 4. 改字号立刻推给所有格子 ===")

    from pycal.ui.main_window import PyCalMainWindow, _load_setting, _save_setting

    # 造 6 条内容 —— 字放大后每格塞不下 6 条, 正好验「多的折成 +N 更多」
    from datetime import date
    from pycal.db.connection import open_default
    from pycal.db.repository import upsert_item
    from pycal.db.models import CalendarItem
    TODAY = date.today().isoformat()
    upsert_item(open_default(), CalendarItem(
        item_id="font-scale-test", date=TODAY, type=0, title="",
        content="\n".join(f"第 {i} 行" for i in range(1, 7)),
    ))

    _save_setting("font_scale", "")
    win = PyCalMainWindow(app, fullscreen=False)
    win.show()
    app.processEvents()
    cells = list(win._month_left._cells.values())
    today_cell = win._month_left._cells[TODAY]
    base = fonts_for(DEFAULT_SCALE)

    check("月视图格数是整周数", lambda: _assert(
        len(cells) in (28, 35, 42),
        f"{len(cells)}",
    ))
    check("默认档: 条目行 8pt / 行高 15px", lambda: _assert(
        today_cell._fonts.row == base.row
        and today_cell._fonts.row_h == base.row_h,
        f"{today_cell._fonts.row}pt / {today_cell._fonts.row_h}px",
    ))
    check("格子里的行用的是同一份字号", lambda: _assert(
        today_cell._rows[0]._font.pointSize() == base.row
        and today_cell._rows[0].height() == base.row_h,
        f"{today_cell._rows[0]._font.pointSize()}pt"
        f" / {today_cell._rows[0].height()}px",
    ))
    check("标准档 6 条只显示 5 条, 多余的折起来", lambda: _assert(
        _shown_rows(today_cell) == base.max_rows
        and today_cell._more_lbl.isVisible(),
        f"可见 {_shown_rows(today_cell)} 行",
    ))

    # 用户选「巨大」— 现有的月视图都得变, 且不用翻月
    win._set_font_scale(1.5)
    app.processEvents()
    big = fonts_for(1.5)

    def _live_views(w):
        """当前存在的月视图 — 翻月动画那块是第一次翻月时才建的。"""
        out = [w._month_left]
        if w._idle_month_view is not None:
            out.append(w._idle_month_view)
        return out

    check("所有月视图的格子都变大了", lambda: _assert(
        all(c._fonts.row == big.row
            for v in _live_views(win)
            for c in v._cells.values()),
    ))
    check("之后才建的动画层也带新字号", lambda: _assert(
        all(c._fonts.row == big.row
            for c in win._ensure_idle_month_view()._cells.values()),
    ))
    check("已有的行跟着放大 (不是只管新格子)", lambda: _assert(
        today_cell._rows[0]._font.pointSize() == big.row
        and today_cell._rows[0].height() == big.row_h,
        f"{today_cell._rows[0]._font.pointSize()}pt"
        f" / {today_cell._rows[0].height()}px",
    ))
    check("每格条数跟着变少", lambda: _assert(
        today_cell._fonts.max_rows == max_rows_for(1.5)
        and max_rows_for(1.5) < base.max_rows,
        f"{today_cell._fonts.max_rows} 条",
    ))
    check("放大的那几条不会挤爆格子", lambda: _assert(
        _shown_rows(today_cell) == big.max_rows
        and today_cell._more_lbl.isVisible(),
        f"可见 {_shown_rows(today_cell)} 行",
    ))
    check("工具栏高度跟着长", lambda: _assert(
        win._top_bar.height() == big.bar_h and big.bar_h > 36,
        f"{win._top_bar.height()}px",
    ))
    check("工具栏按钮字号写进了 QSS", lambda: _assert(
        f"font-size: {big.toolbar}pt" in win._btn_exit.styleSheet()))
    check("对话框的默认字号也一起放大", lambda: _assert(
        win._base_app_pt > 0
        and app.font().pointSize() == round(win._base_app_pt * 1.5),
        f"{app.font().pointSize()}pt",
    ))

    print("\n=== 5. 连点 A+ 微调 ===")

    win._set_font_scale(1.0)
    win._step_font_scale(+0.05)
    check("A+ 调大 5%", lambda: _assert(
        abs(win._font_scale - 1.05) < 1e-6, str(win._font_scale)))
    win._step_font_scale(-0.05)
    check("A− 调回原位", lambda: _assert(
        abs(win._font_scale - 1.0) < 1e-6, str(win._font_scale)))
    for _ in range(60):        # 连点到顶
        win._step_font_scale(+0.05)
    check("连点 A+ 顶到上限就停", lambda: _assert(
        win._font_scale <= MAX_SCALE, str(win._font_scale)))

    # ------------------------------------------------------------------
    print("\n=== 6. 存进 settings, 重开还在 ===")

    win._set_font_scale(1.5)
    check("比例存进了 settings", lambda: _assert(
        abs(float(_load_setting("font_scale", "1.0")) - 1.5) < 1e-6,
        _load_setting("font_scale", "(空)"),
    ))
    win2 = PyCalMainWindow(app, fullscreen=False)
    app.processEvents()
    check("新窗口直接按存的尺寸显示", lambda: _assert(
        win2._font_scale == 1.5 and win2._fonts.row == fonts_for(1.5).row,
        f"{win2._font_scale} / {win2._fonts.row}pt",
    ))
    check("新窗口的格子也是 1.5 倍", lambda: _assert(
        all(c._fonts.row == fonts_for(1.5).row
            for c in win2._month_left._cells.values()),
    ))

    print("\n=== 7. 换皮肤不打回默认字号 ===")

    win2._set_skin("dark_mode")
    app.processEvents()
    check("换皮肤后字号还是 1.5 倍", lambda: _assert(
        win2._fonts.row == fonts_for(1.5).row
        and all(c._fonts.row == fonts_for(1.5).row
                for c in win2._month_left._cells.values()),
    ))
    check("换皮肤后按钮字号也没掉", lambda: _assert(
        f"font-size: {fonts_for(1.5).toolbar}pt" in win2._btn_exit.styleSheet()))

    # ------------------------------------------------------------------
    print("\n=== 8. 菜单里能调到 ===")

    menu = win2._build_more_menu()
    check("「更多」菜单里有「字体大小」", lambda: _assert(
        any("字体大小" in a.text() for a in menu.actions()),
        str([a.text() for a in menu.actions()][-8:]),
    ))
    font_menu = next(a.menu() for a in menu.actions()
                     if "字体大小" in a.text())
    check("子菜单列出了全部档位", lambda: _assert(
        all(any(lbl in a.text() for a in font_menu.actions())
            for _, lbl in SCALES),
        str([a.text() for a in font_menu.actions()]),
    ))
    check("当前档位有勾", lambda: _assert(
        any(a.isChecked() for a in font_menu.actions())))
    # 点「巨大」这一项, 走和用户点菜单一样的路径
    next(a for a in font_menu.actions() if "150%" in a.text()).trigger()
    app.processEvents()
    check("点了「巨大」窗口立刻变大", lambda: _assert(
        win2._font_scale == 1.5
        and win2._top_bar.height() == fonts_for(1.5).bar_h,
        f"{win2._font_scale}",
    ))

    print("\n=== 9. 能改回默认 ===")

    menu2 = win2._build_more_menu()
    fm2 = next(a.menu() for a in menu2.actions() if "字体大小" in a.text())
    next(a for a in fm2.actions() if "恢复默认" in a.text()).trigger()
    app.processEvents()
    check("恢复默认回到 8pt / 36px 工具栏", lambda: _assert(
        win2._font_scale == DEFAULT_SCALE
        and win2._fonts.row == 8
        and win2._top_bar.height() == 36,
        f"{win2._font_scale} / {win2._fonts.row}pt / {win2._top_bar.height()}px",
    ))
    check("对话框的默认字号也回到基准 (反复调不会滚雪球)", lambda: _assert(
        win2._base_app_pt > 0
        and app.font().pointSize() == win2._base_app_pt,
        f"{app.font().pointSize()}pt ≠ 基准 {win2._base_app_pt}pt",
    ))

    for w in (win, win2):
        w.close()
    app.processEvents()

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())