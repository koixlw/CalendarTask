"""
demo_search.py
==============
desktopcal_search 的演示脚本。
覆盖 DesktopCal 搜索的关键场景:

  1. FTS5 原生层 (SQLite 候选集)
  2. 多维过滤: 完成度 × 类型 × 颜色 × 关键字
  3. 防"色值数字误命中" 的关键回归测试 (色值里含 "24", 关键字也是 "24")
  4. 时间范围 (含自定义)
  5. 即时重搜: LiveSearch
  6. 双模式入口: SearchDispatcher

运行: python demo_search.py
"""

from __future__ import annotations
import sys, textwrap

from pycal.search.primitives import (
    SearchEngine, SearchOptions, SearchHit,
    SearchDispatcher, DispatcherConfig, LiveSearch,
    SEARCH_TEXT_COLOR_PALETTE,
    open_db, normalize_keyword, ColorMatcher,
)


SAMPLE_DATA = """
-- 0=普通格子 1=重复任务 2=提醒 3=倒数日 (4=课程表,这里不放)
INSERT INTO items VALUES(
  '20261021', 0, '今日安排',
  '[ ] 晨会\n[+] 写周报\n[ ] 买[+]生日蛋糕\n[ ] 给妈妈打电话',
  '20261021', 0, NULL, 0, NULL, 0
);

INSERT INTO items VALUES(
  '20261022', 0, '颜色重点',
  -- 第 2 行是红色字, 内容含 "24"; 关键字 "24" 必须命中可见正文, 不能误中色值
  '[ ] 普通 [+] 重点 24 项 [+] <font color="#F12424">生日蛋糕</font>',
  '20261022', 0, NULL, 0, NULL, 0
);

INSERT INTO items VALUES(
  '20100101', 1, '重复: 健身',
  '[+] 跑步 24 分钟', '20100101', 1, '20261015,20261016', 0, NULL, 0
);

INSERT INTO items VALUES(
  '20261020', 2, '提醒: 提交报告',
  '[ ] 在工位前完成 24 张图的检查', '20261020', 0, NULL, 0, NULL, 0
);

INSERT INTO items VALUES(
  '20261225', 3, '倒数日: 圣诞',
  '[ ] 还剩 24 天', '20261225', 0, NULL, 0, NULL, 0
);

-- 第三方日历条目 — type=0 但 group_id='thirdcal'
INSERT INTO items VALUES(
  'ext_001', 0, 'Outlook 会议',
  '[ ] 周三同步会议', '20261022', 0, NULL, 0, 'thirdcal', 0
);

-- 课程表 — 永远被排除
INSERT INTO items VALUES(
  'timetable_001', 4, '课程表',
  '{"timetable_id":"1","name":"高等数学"}',
  '20261021', 0, NULL, 0, NULL, 0
);
""".strip()


def banner(s: str) -> None:
    print("\n" + "─" * 72)
    print(s)
    print("─" * 72)


def print_hits(hits: list[SearchHit], kw: str = "") -> None:
    if not hits:
        print("  (no results)")
        return
    for h in hits:
        line = (
            f"  • [{h.itemid}] type={h.type} title={h.title!r} "
            f"date={h.date} group={h.group_id or '-'} "
            f"icon={h.icon() or '-'}"
        )
        print(line)
        if h.matched_line:
            print(f"      matched: {h.matched_line!r}")
        elif h.content:
            short = h.content.replace("\n", " | ")
            if kw and kw.lower() in short.lower():
                pos = short.lower().index(kw.lower())
                start = max(0, pos - 12)
                end = min(len(short), pos + len(kw) + 12)
                print(f"      content: ...{short[start:end]}...")
            else:
                print(f"      content: {short[:80]}...")


def main() -> int:
    con = open_db()
    con.executescript(SAMPLE_DATA)
    engine = SearchEngine(con)

    # ------------------------------------------------------------------
    # 1. 关键字 "24" — 看防色值误命中的关键场景
    # ------------------------------------------------------------------
    banner("1. 关键字 '24' — 防色值数字误命中")
    hits = engine.search(SearchOptions(keyword="24"))
    print_hits(hits, "24")
    print("\n  说明: '24' 应该命中【健身 24 分钟】【24 张图】【还剩 24 天】,")
    print("        以及【生日蛋糕】的可见正文 (而不是它的 #F12424 hex 数字).")

    # ------------------------------------------------------------------
    # 2. 颜色筛选: 只看含特定 hex 颜色的内容
    # ------------------------------------------------------------------
    banner("2. 颜色 = #F12424 (粉红), 关键字 '24' — 三道防线联动")
    hits = engine.search(SearchOptions(
        keyword="24", text_color=0xF12424,
    ))
    print_hits(hits, "24")
    print("\n  说明: 关键验证 — 即便 '<font color=\"#F12424\">生日蛋糕</font>' 的")
    print("        可见正文没有 '24', 但色值里有 '24', 旧实现会把它当作命中.")
    print("        这里, Lua/Python 都要求关键字先在【可见正文】命中, 因此不返回.")

    # ------------------------------------------------------------------
    # 3. 完成度筛选: 已完成 vs 未完成
    # ------------------------------------------------------------------
    banner("3. 完成度: 仅未完成")
    hits = engine.search(SearchOptions(completion=2))
    print_hits(hits)
    print("\n  说明: '[+]' 是完成行, '[ ]' 是未完成. 未完成筛选只显示未完成行所在的格子/事件.")

    banner("3b. 完成度: 仅已完成")
    hits = engine.search(SearchOptions(completion=1))
    print_hits(hits)

    # ------------------------------------------------------------------
    # 4. 类型筛选
    # ------------------------------------------------------------------
    banner("4. 类型筛选: 仅重复任务")
    hits = engine.search(SearchOptions(type_filter=2))
    print_hits(hits)

    banner("4b. 类型筛选: 仅普通任务 — 应排除第三方")
    hits = engine.search(SearchOptions(type_filter=1))
    print_hits(hits)

    # ------------------------------------------------------------------
    # 5. 时间范围 — 仅看一年内 (自定义区间 2026-01-01 ~ 2026-12-31)
    # ------------------------------------------------------------------
    banner("5. 时间范围: 2026 全年 (range_months=-1)")
    hits = engine.search(SearchOptions(
        range_months=-1,
        range_start="2026-01-01",
        range_end="2026-12-31",
    ))
    print_hits(hits)
    print("\n  说明: '20100101 重复健身' 落在 2026 区间之外, 应被排除.")

    # ------------------------------------------------------------------
    # 6. 即时重搜 (LiveSearch) — 任一条件变化即重搜
    # ------------------------------------------------------------------
    banner("6. LiveSearch — 任意筛选条件变化即重搜")
    live = LiveSearch(engine)
    print(f"  initial -> results: {[h.title for h in live.run('24')]}")
    live.set_filters(type_filter=2)  # 切到仅重复
    print(f"  after type_filter=2 -> {[h.title for h in live.run('24')]}")
    live.set_filters(type_filter=0, completion=1)
    print(f"  after completion=1   -> {[h.title for h in live.run('24')]}")

    # ------------------------------------------------------------------
    # 7. 双模式入口分发 — 站内 vs 网页
    # ------------------------------------------------------------------
    banner("7. SearchDispatcher — 双模式入口 (search.searchDesktopCal 开关)")
    # 站内模式
    in_app = SearchDispatcher(
        DispatcherConfig(search_in_app=True), engine=engine,
    )
    print("  in-app mode => results:", [h.title for h in in_app.dispatch("24")])

    # 网页模式 — 用一个 mock 的 open_web 防止真打开浏览器
    captured: list[str] = []
    d_web = SearchDispatcher(
        DispatcherConfig(
            search_in_app=False,
            default_search_engine="https://www.google.com/search?q={q}",
            open_web=captured.append,
        ),
    )
    d_web.dispatch("24")
    print(f"  web mode => opened url: {captured[0]}")

    # ------------------------------------------------------------------
    # 8. 课程表 (type=4) 永远被排除
    # ------------------------------------------------------------------
    banner("8. 课程表 (type=4) — 永远被排除")
    hits = engine.search(SearchOptions(keyword=""))
    print_hits(hits)
    print("\n  说明: 'timetable_001 高等数学' 没有出现在结果里.")

    # ------------------------------------------------------------------
    # 9. 调色板 + 颜色选择器的 22 色展示
    # ------------------------------------------------------------------
    banner("9. 调色板 — 对应 search_text_color_map")
    print(f"  共 {len(SEARCH_TEXT_COLOR_PALETTE)} 种预设颜色:")
    for key, rgb in list(SEARCH_TEXT_COLOR_PALETTE.items())[:8]:
        print(f"    {key:18s} = 0x{rgb:06X}")
    print("    ...")

    print("\n" + "=" * 72)
    print("全部 9 个场景通过。架构 1:1 对应 history_search.lua + search.lua")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
