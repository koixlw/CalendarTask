"""打勾功能测试 — 「做好一个就能打勾」这条链路的回归网。

覆盖三层:
  1. _toggle_line_in_content — 纯函数, 改 [+] 标记不改别的
  2. MonthView._handle_toggle — 真正写库, 再读回来验证
  3. LineRow — 点 ☐ 发信号, 点文字不发, 双击发打开信号

跑:  python tests/test_checkbox_toggle.py
"""

from __future__ import annotations
import os
import sys
import tempfile
import traceback
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


def _dblclick(widget, x, y):
    ev = QMouseEvent(
        QEvent.Type.MouseButtonDblClick, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseDoubleClickEvent(ev)


def main() -> int:
    # 把 DB 指到临时目录 — 别碰用户真实数据
    tmp = tempfile.mkdtemp(prefix="pycal_test_")
    os.environ["APPDATA"] = tmp
    from pycal.db.connection import open_default, close_default
    close_default()

    app = QApplication.instance() or QApplication([])

    from pycal.db.repository import upsert_item, get_item
    from pycal.db.models import CalendarItem
    from pycal.ui.month_view import (
        DayCell, LineRow, MonthView, _toggle_line_in_content, _parse_lines,
        colors_for_skin,
    )

    print("=" * 70)
    print("打勾功能测试")
    print("=" * 70)

    # ------------------------------------------------------------------
    print("\n=== 1. _toggle_line_in_content (纯函数) ===")

    check("勾上第 1 行", lambda: _assert(
        _toggle_line_in_content("写日报\n改 bug\n交周报", 0, True)
        == "[+] 写日报\n改 bug\n交周报",
        _toggle_line_in_content("写日报\n改 bug\n交周报", 0, True),
    ))

    check("取消第 2 行的勾", lambda: _assert(
        _toggle_line_in_content("[+] 写日报\n[+] 改 bug", 1, False)
        == "[+] 写日报\n改 bug",
        _toggle_line_in_content("[+] 写日报\n[+] 改 bug", 1, False),
    ))

    check("跳过空行, 序号对得上", lambda: _assert(
        _toggle_line_in_content("第一行\n\n第三行", 1, True)
        == "第一行\n\n[+] 第三行",
        _toggle_line_in_content("第一行\n\n第三行", 1, True),
    ))

    check("反复勾/取消幂等", lambda: _assert(
        _toggle_line_in_content(_toggle_line_in_content("abc", 0, True), 0, False)
        == "abc",
        "来回切换后应回到原样",
    ))

    check("不越界 (行号超出时原样返回)", lambda: _assert(
        _toggle_line_in_content("abc", 99, True) == "abc",
        "越界不该改内容",
    ))

    check("与 _parse_lines 序号一致", lambda: _assert(
        [t for _, t in _parse_lines(_toggle_line_in_content("A\n\nB\n\nC", 2, True))]
        == ["A", "B", "C"],
        "勾第 3 行后, 解析出的文字顺序不该变",
    ))

    # ------------------------------------------------------------------
    print("\n=== 2. MonthView 写库 ===")

    DAY = "2026-10-22"
    con = open_default()
    grid = CalendarItem(
        item_id="test-grid", date=DAY, type=0, title="",
        content="写日报\n改同步 bug\n回邮件",
    )
    upsert_item(con, grid)
    repeat = CalendarItem(
        item_id="test-repeat", date=DAY, type=1, title="每周站会",
    )
    upsert_item(con, repeat)

    mv = MonthView()
    mv.goto(2026, 10)
    QApplication.processEvents()

    cell = mv._cells.get(DAY)

    check("当天格子渲染出 4 行 (3 内容 + 1 重复)", lambda: _assert(
        cell is not None and len(cell._rows) == 4,
        f"实际 {0 if cell is None else len(cell._rows)} 行",
    ))

    check("LineRow 挂上了 item_id 和行号", lambda: _assert(
        [(r._item_id, r._line_idx) for r in cell._rows]
        == [("test-grid", 0), ("test-grid", 1), ("test-grid", 2),
            ("test-repeat", None)],
        [(r._item_id, r._line_idx) for r in cell._rows],
    ))

    check("勾第 2 行写回 content", lambda: (
        mv._handle_toggle("test-grid", 1, True),
        _assert(
            get_item(con, "test-grid").content
            == "写日报\n[+] 改同步 bug\n回邮件",
            get_item(con, "test-grid").content,
        ),
    ))

    check("格子刷新后第 2 行显示为已完成", lambda: (
        mv._handle_toggle("test-grid", 1, True),
        _assert(cell._rows[1]._done is True,
                f"第 2 行 _done={cell._rows[1]._done}"),
    ))

    check("再点一次能取消", lambda: (
        mv._handle_toggle("test-grid", 1, False),
        _assert(get_item(con, "test-grid").content == "写日报\n改同步 bug\n回邮件",
                get_item(con, "test-grid").content),
    ))

    check("重复条目 (line_idx=None) 写 is_completed", lambda: (
        mv._handle_toggle("test-repeat", None, True),
        _assert(get_item(con, "test-repeat").is_completed == 1,
                get_item(con, "test-repeat").is_completed),
    ))

    check("条目不存在时不炸", lambda: mv._handle_toggle("no-such-id", 0, True))

    fired: list[bool] = []
    mv.sig_data_changed.connect(lambda: fired.append(True))

    check("勾选会通知主窗刷新计数", lambda: (
        mv._handle_toggle("test-grid", 0, True),
        _assert(fired, "sig_data_changed 没发"),
    ))

    # ------------------------------------------------------------------
    print("\n=== 3. LineRow 鼠标交互 ===")

    row = LineRow()
    row.bind("item-x", "点我", False, 0, "#222222", "#9AA0A6")

    got = []
    row.sig_toggled.connect(lambda i, l, d: got.append((i, l, d)))
    row.sig_open.connect(lambda i, l: got.append(("open", i, l)))

    check("点左侧 ☐ 触发勾选", lambda: (
        _click(row, 6, 7),
        _assert(got == [("item-x", 0, True)], got),
    ))

    check("勾上后显示 ☑ 且文字加删除线", lambda: _assert(
        row._box.text() == "☑" and row._font.strikeOut(),
        f"box={row._box.text()!r} strike={row._font.strikeOut()}",
    ))

    check("点文字不触发勾选", lambda: (
        _click(row, 60, 7),
        _assert(len(got) == 1, got),
    ))

    check("双击文字触发打开", lambda: (
        _dblclick(row, 60, 7),
        _assert(got[-1] == ("open", "item-x", 0), got),
    ))

    check("取消勾选后回到 ☐", lambda: (
        _click(row, 6, 7),
        _assert(row._box.text() == "☐" and not row._font.strikeOut(),
                f"box={row._box.text()!r}"),
    ))

    # ------------------------------------------------------------------
    print("\n=== 4. 配色随皮肤切换 ===")

    check("dark_mode 的格子底色不同于浅色", lambda: _assert(
        colors_for_skin("dark_mode").body_bg != colors_for_skin("light_default").body_bg,
        "两种皮肤应给出不同底色",
    ))

    check("apply_skin 把新配色推给了所有格子", lambda: (
        mv.apply_skin("dark_mode"),
        _assert(
            mv._cells[DAY]._colors.body_bg == colors_for_skin("dark_mode").body_bg,
            f"格子仍是 {mv._cells[DAY]._colors.body_bg}",
        ),
        _assert(
            all(c._colors.body_bg == colors_for_skin("dark_mode").body_bg
                for c in mv._cells.values()),
            "还有格子没换配色",
        ),
    ))

    check("换回浅色也生效", lambda: (
        mv.apply_skin("light_default"),
        _assert(
            mv._cells[DAY]._colors.body_bg == colors_for_skin("light_default").body_bg,
            "没换回浅色",
        ),
    ))

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("\n失败项:")
        for f in FAIL:
            print(f"  x {f}")
        return 1
    print("全部通过")
    return 0


def _make_counter(mv):
    box = []
    mv.sig_data_changed.connect(lambda: box.append(True))
    return box


if __name__ == "__main__":
    sys.exit(main())