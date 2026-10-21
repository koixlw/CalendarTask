"""
desktopcal_search.py
====================
复刻 DesktopCal (桌面日历) 的搜索架构。

源自 CalendarTask\\app\\dkappcal\\resource.zip 中的两个 Lua 文件:
  - config/search.lua            (入口分发: 站内 / 网页)
  - config/history_search.lua    (完整搜索引擎 + 多维筛选)

架构 1:1 对应:
  ┌─────────────── 原生层 (SQLite FTS5) ───────────────┐
  │  historysearch.Search(text)   ≈   SearchEngine._fts_candidates  │
  └────────────────────────────────────────────────────┘
  ┌─────────────── 过滤层 (Python) ────────────────────┐
  │  is_data_in_range          ≈   filter_by_range                    │
  │  is_data_match_filters     ≈   is_data_match_filters (四维 AND)   │
  │  content_match_color_and_kw ≈ ColorMatcher.match_color_and_keyword │
  │  MarkdownParse line        ≈   markdown_to_plain_text             │
  └────────────────────────────────────────────────────┘

约束:
  - 关键字匹配一律 plain text (无 Lua 模式字符)
  - 大小写不敏感
  - 颜色匹配同时校验 RGB / BGR / 带不带 '#'
  - 关键字非空时必须先在可见正文中命中 (防色值数字误命中)
  - 格子 (type=0) 逐行匹配: 完成度 / 颜色 / 关键字 必须在同一行
  - 课程表 (type=4) 永远被排除

依赖: 仅 sqlite3 (标准库)。FTS5 自 SQLite 3.9+ 起随 python 自带可执行。
"""

from __future__ import annotations

import re
import sqlite3
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional


# ============================================================================
# 1. 调色板 — 对应 history_search.lua:117-140 search_text_color_map
# ============================================================================
# 在 DesktopCal 里这是 BGR (COLORREF), 这里用 RGB 二十四进制值。
SEARCH_TEXT_COLOR_PALETTE: dict[str, int] = {
    f"search_color_{i}": rgb for i, rgb in enumerate(
        [
            0x9595FF, 0x0BBCF9, 0x78F0FB, 0x91FBCB, 0xF6CEB5,
            0xF9B1D7, 0xA8C9E6, 0x282828, 0x2424F1, 0x2168FF,
            0x07D1F5, 0x009300, 0xF43737, 0x81004A, 0xBAD261,
            0x44C465, 0x9D9490, 0xE9D9FF, 0xCEE3FA, 0xEBF0CD,
            0xFFE8D9, 0xF0CDD4,
        ],
        start=1,
    )
}

# 任务类型 → 图标 (DesktopCal 的 searchItem{Repeat,Reminder,Countdown}Icon)
TYPE_ICONS: dict[int, str] = {
    1: "searchItemRepeatIcon",
    2: "searchItemReminderIcon",
    3: "searchItemCountdownIcon",
}


# ============================================================================
# 2. Markdown → 纯文本 — 对应 history_search.lua:51-71 content_to_plain_text
# ============================================================================
# 原件调 IxRichStatic 的 MarkdownParse。下列的精简实现足以覆盖日程条目里实际
# 出现的语法 (粗体/颜色标签/标记完成用的 [+])。
_FONT_TAG_RE = re.compile(
    # 捕获组: 1=attrs, 2=inner
    r"<\s*[Ff][Oo][Nn][Tt]\b([^>]*)>(.*?)</\s*[Ff][Oo][Nn][Tt]\s*>",
    re.DOTALL,
)


def _md_inline_to_text(line: str) -> str:
    """剥离 Markdown 行级标签 (<font>)，返回可见正文。"""
    return _FONT_TAG_RE.sub(r"\2", line)


def markdown_to_plain_text(content: Optional[str]) -> str:
    """对应 history_search.lua:51-71。逐行转换。"""
    if not content:
        return ""
    parts: list[str] = []
    # Lua 是 ([^\r\n]*)\r?\n 风格; 这里直接 str.splitlines 等价。
    for line in (content + "\n").splitlines():
        text = _md_inline_to_text(line)
        parts.append(text or "")
    return "\n".join(parts)


def normalize_keyword(keyword: Optional[str]) -> str:
    """对应 history_search.lua:73-80 — trim 首尾空白。"""
    return (keyword or "").strip()


# ============================================================================
# 3. 匹配原语 — 对应 history_search.lua:84-111, 219-232, 277-332
# ============================================================================
def _colorref_to_hex(color: int) -> str:
    """对应 history_search.lua:142-150 colorref_to_font_hex。"""
    if not color:
        return ""
    r = color & 0xFF
    g = (color >> 8) & 0xFF
    b = (color >> 16) & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"


def _hex_variants(color: int) -> set[str]:
    """四份 hex 形式: RGB/BGR × 有无 '#'。"""
    if not color:
        return set()
    rgb_hex = _colorref_to_hex(color)
    r, g, b = color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF
    bgr_hex = f"#{b:02X}{g:02X}{r:02X}"
    return {
        rgb_hex, rgb_hex.lstrip("#").upper(),
        bgr_hex, bgr_hex.lstrip("#").upper(),
    }


def text_has_keyword(text: Optional[str], keyword: str) -> bool:
    """对应 history_search.lua:219-232 — 大小写不敏感、plain text。"""
    kw = normalize_keyword(keyword)
    if not kw:
        return True
    if text is None or text == "":
        return False
    return kw.lower() in str(text).lower()


def is_content_match_keyword(content: Optional[str], keyword: str) -> bool:
    """对应 history_search.lua:84-111 — 在 MarkdownParse 后的可见正文里匹配。

    课程表 JSON (含 timetable_id) 走 parseContentForDisplay, 只取 name 字段,
    避免结构性字段命中关键字。
    """
    kw = normalize_keyword(keyword)
    if not kw:
        return True
    if not content:
        return False

    # 课程表 JSON 走特殊路径: 提取 name 字段
    if "timetable_id" in content and '"name"' in content:
        display = parse_content_for_display(content)
    else:
        display = markdown_to_plain_text(content)

    return text_has_keyword(display, kw)


def parse_content_for_display(content: Optional[str]) -> str:
    """对应 history_search.lua:27-48 parseContentForDisplay。

    课程表 JSON: 抽 "name":"..." 字串, 还原转义; 普通日程原样返回。
    """
    if not content:
        return ""
    if "timetable_id" in content and '"name"' in content:
        m = re.search(r'"name"\s*:\s*"((?:[^"\\]|\\.)*)"', content)
        if m:
            raw = m.group(1)
            return (
                raw.replace(r"\"", '"')
                .replace(r"\n", "\n")
                .replace(r"\r", "\r")
                .replace(r"\\", "\\")
            )
        return content
    return content


class ColorMatcher:
    """对应 history_search.lua:235-332 的全部颜色相关判定。

    关键: 关键字非空时, 必须先在可见正文命中关键字, 才算"该颜色 + 关键字"命中;
    杜绝 `<font color="#F12424">` 里 "24" 被搜到。
    """

    def __init__(self, color: int):
        self.color = color
        self._hexes = _hex_variants(color)

    def has_color(self, content: Optional[str]) -> bool:
        """对应 content_has_font_color — 看整段内容里是否出现该颜色 (含 <font 标签外)。"""
        if not self.color or not content:
            return False
        upper = content.upper()
        return any(h in upper for h in self._hexes)

    def match_color(self, content: Optional[str]) -> bool:
        """对应 content_has_font_color 的回退, 用于无关键字场景。"""
        if not self.color:
            return True
        return self.has_color(content)

    def match_color_and_keyword(
        self,
        content: Optional[str],
        keyword: str,
    ) -> bool:
        """对应 history_search.lua:278-332 content_match_color_and_keyword。

        逻辑:
          - 颜色 == 0: 仅匹配关键字 (在 MarkdownParse 后的可见正文)
          - 关键字空: 整段里有该颜色即可 (含 <font> 属性片段)
          - 关键字非空: 必须在带 <font color=hex> 的可见正文片段里命中关键字
        """
        kw = normalize_keyword(keyword)
        need_kw = bool(kw)
        if not self.color:
            return text_has_keyword(markdown_to_plain_text(content), kw)

        if not content:
            return False

        # 遍历 <font attrs>inner</font> 片段, attrs 中含 hex 才视为该颜色。
        bfound_color = False
        for attrs, inner in _FONT_TAG_RE.findall(content):
            if any(h in attrs.upper() for h in self._hexes):
                bfound_color = True
                if not need_kw:
                    return True
                if text_has_keyword(markdown_to_plain_text(inner), kw):
                    return True

        if need_kw:
            # 没找到带颜色的文字里命中关键字 -> 不通过。
            return False
        # 无关键字场景, 整段里出现 hex 也算 (历史 BGR 兼容)。
        return bfound_color or self.has_color(content)


# ============================================================================
# 4. 行级判定 — 对应 history_search.lua:207-217, 335-360
# ============================================================================
_COMPLETED_PREFIX_RE = re.compile(r"^\s*\[+\]")


def line_is_completed(line: str) -> bool:
    return bool(_COMPLETED_PREFIX_RE.match(line))


def line_is_nonempty_incomplete(line: str) -> bool:
    if not line.strip():
        return False
    return not line_is_completed(line)


def grid_line_matches(
    line: str,
    completion: int,
    text_color: int,
    keyword: str,
) -> bool:
    """对应 history_search.lua:335-360 grid_line_matches。

    同一行同时满足 完成度 / 颜色 / 关键字 (有筛选条件时)。
    """
    if completion == 1:
        if not line_is_completed(line):
            return False
    elif completion == 2:
        if not line_is_nonempty_incomplete(line):
            return False
    else:
        if not line.strip():
            return False

    kw = normalize_keyword(keyword)
    if kw:
        if not text_has_keyword(markdown_to_plain_text(line), kw):
            return False

    return ColorMatcher(text_color).match_color_and_keyword(line, kw)


def each_content_line(content: Optional[str]) -> Iterable[str]:
    if not content:
        return
    for line in (content + "\n").splitlines():
        yield line


# ============================================================================
# 5. SQLite 原生层 — 对应 historysearch.Search 的 C++ 实现
# ============================================================================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS items (
    itemid     TEXT PRIMARY KEY,        -- 格子: 'YYYYMMDD'  / 事件: union id
    type       INTEGER NOT NULL,        -- 0=格子 1=重复 2=提醒 3=倒数日 4=课程表
    title      TEXT,
    content    TEXT,                    -- 富文本/Markdown/课程表 JSON
    date       TEXT,                    -- 'YYYYMMDD' (格子); 事件可空
    has_completed INTEGER DEFAULT 0,    -- 事件类是否已完成
    completed_dates TEXT,               -- 重复类: 'YYYYMMDD,YYYYMMDD,...'
    color_ref  INTEGER DEFAULT 0,       -- 行/段落级颜色; 0 表示任意
    group_id   TEXT,                    -- 'thirdcal' 表示第三方
    subaccount_id   INTEGER DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    itemid UNINDEXED, type UNINDEXED, title, content,
    content='items', content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, itemid, type, title, content)
    VALUES (new.rowid, new.itemid, new.type, new.title, new.content);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, itemid, type, title, content)
    VALUES('delete', old.rowid, old.itemid, old.type, old.title, old.content);
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, itemid, type, title, content)
    VALUES('delete', old.rowid, old.itemid, old.type, old.title, old.content);
    INSERT INTO items_fts(rowid, itemid, type, title, content)
    VALUES (new.rowid, new.itemid, new.type, new.title, new.content);
END;
"""


def open_db(path: str = ":memory:") -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL)
    return con


# ============================================================================
# 6. SearchEngine — 1:1 对应 history_search.lua::click_search + is_data_match_filters
# ============================================================================
@dataclass
class SearchOptions:
    keyword: str = ""
    completion: int = 0          # 0=全部 / 1=已完成 / 2=未完成
    type_filter: int = 0         # 0=全部 / 1=普通任务 / 2=重复 / 3=提醒 / 4=倒数日
    text_color: int = 0          # 0=任意 / COLORREF
    range_months: Optional[int] = 0  # None/0=全部 / 正数=过去 N 月内 / -1=自定义
    range_start: Optional[str] = None  # 'YYYY-MM-DD' 当 range_months == -1
    range_end: Optional[str] = None


@dataclass
class SearchHit:
    itemid: str
    type: int
    title: Optional[str]
    content: Optional[str]
    date: Optional[str]
    has_completed: int
    completed_dates: Optional[str]
    color_ref: int
    group_id: Optional[str]
    subaccount_id: int
    matched_line: Optional[str] = None     # 格子型: 命中行

    def icon(self) -> Optional[str]:
        return TYPE_ICONS.get(self.type)


class SearchEngine:
    """复刻 historysearch.Search + is_data_match_filters。"""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    # — 原生层: 取候选集 ——
    def _fts_candidates(self, keyword: str) -> list[sqlite3.Row]:
        kw = normalize_keyword(keyword)
        if not kw:
            cur = self.con.execute(
                "SELECT * FROM items WHERE type != 4"
            )
        else:
            # FTS5 MATCH。FTS5 把 query 中特殊字符当操作符,
            # 用双引号包住强制为 phrase -> 等价于 DesktopCal 里的
            # "plain=true 子串匹配"。
            escaped = '"' + kw.replace('"', '""') + '"'
            cur = self.con.execute(
                "SELECT i.* FROM items i JOIN items_fts f ON i.rowid = f.rowid"
                " WHERE items_fts MATCH ? AND i.type != 4",
                (escaped,),
            )
        return cur.fetchall()

    # — 过滤层: 范围 ——
    def filter_by_range(
        self,
        row: sqlite3.Row,
        range_months: Optional[int],
        now_epoch: int,
        range_start: Optional[str] = None,
        range_end: Optional[str] = None,
    ) -> bool:
        """对应 history_search.lua:771-896 is_data_in_range。"""
        if range_months is None or range_months == 0:
            return True
        date_str = row["date"]
        if not date_str or len(date_str) != 8:
            return True  # 事件类无 date, 不参与范围

        try:
            year = int(date_str[0:4])
            month = int(date_str[4:6])
            day = int(date_str[6:8])
        except ValueError:
            return True

        if range_months == -1:
            # 自定义区间
            if not range_start or not range_end:
                return True
            try:
                sy, sm, sd = (int(x) for x in range_start.split("-"))
                ey, em, ed = (int(x) for x in range_end.split("-"))
            except ValueError:
                return True
            target = (year, month, day)
            return (sy, sm, sd) <= target <= (ey, em, ed)

        # N 月内 (回看)。Lua 行为: 在 [now-N月, now] 内。
        from datetime import datetime, timedelta
        try:
            target_dt = datetime(year, month, day)
        except ValueError:
            return True
        now_dt = datetime.fromtimestamp(now_epoch)
        delta = now_dt - target_dt
        return timedelta(0) <= delta <= timedelta(days=range_months * 30)

    # — 过滤层: 多维联合 ——
    def is_data_match_filters(
        self,
        row: sqlite3.Row,
        completion: int,
        type_filter: int,
        text_color: int,
        keyword: str,
    ) -> bool:
        """对应 history_search.lua:444-515 is_data_match_filters。"""
        t = row["type"] or 0
        is_third = (row["group_id"] or "") == "thirdcal"
        content = row["content"] or ""

        # 课程表排除
        if t == 4:
            return False

        # 类型筛选
        if type_filter == 1:                # 普通任务
            if is_third or t != 0:
                return False
        elif type_filter == 2:              # 重复
            if t != 1:
                return False
        elif type_filter == 3:              # 提醒
            if t != 2:
                return False
        elif type_filter == 4:              # 倒数日
            if t != 3:
                return False
        # 0 = 全部 (含第三方)

        # 格子 (type=0 且非第三方): 逐行匹配
        if t == 0 and not is_third:
            return any(
                grid_line_matches(line, completion, text_color, keyword)
                for line in each_content_line(content)
            )

        # 事件/第三方
        if completion == 1 and t in (1, 2, 3):
            if (row["has_completed"] or 0) != 1:
                return False
        elif completion == 1:
            return False  # 第三方等在 "只看已完成" 下不显示

        # 颜色 + 关键字
        if text_color:
            if keyword and not is_content_match_keyword(content, keyword):
                return False
            return ColorMatcher(text_color).match_color_and_keyword(content, keyword)

        if keyword:
            return is_content_match_keyword(content, keyword)
        return True

    # — 顶层入口: click_search ——
    def search(
        self,
        opts: SearchOptions,
        now_epoch: Optional[int] = None,
    ) -> list[SearchHit]:
        """对应 history_search.lua::click_search。

        流程: 原生候选 -> 时间范围 -> 多维过滤。
        格子型结果会在 matched_line 上携带命中行 (供 UI 高亮)。
        """
        import time as _time
        now = now_epoch if now_epoch is not None else int(_time.time())
        hits: list[SearchHit] = []
        for row in self._fts_candidates(opts.keyword):
            if not self.filter_by_range(
                row, opts.range_months, now,
                opts.range_start, opts.range_end,
            ):
                continue
            if not self.is_data_match_filters(
                row,
                opts.completion,
                opts.type_filter,
                opts.text_color,
                opts.keyword,
            ):
                continue

            hit = SearchHit(
                itemid=row["itemid"],
                type=row["type"],
                title=row["title"],
                content=row["content"],
                date=row["date"],
                has_completed=row["has_completed"] or 0,
                completed_dates=row["completed_dates"],
                color_ref=row["color_ref"] or 0,
                group_id=row["group_id"],
                subaccount_id=row["subaccount_id"] or 0,
            )
            # 格子: 附上第一条命中行 (供 UI 高亮 / 限定显示)
            if hit.type == 0 and (row["group_id"] or "") != "thirdcal":
                for line in each_content_line(row["content"]):
                    if grid_line_matches(
                        line, opts.completion, opts.text_color, opts.keyword
                    ):
                        hit.matched_line = line
                        break
            hits.append(hit)
        return hits


# ============================================================================
# 7. 入口分发 — 对应 search.lua::onreturn_search
# ============================================================================
@dataclass
class DispatcherConfig:
    search_in_app: bool = True
    default_search_engine: str = "https://www.bing.com/search?q={q}"
    open_web: Callable[[str], None] = field(default=webbrowser.open)


def _url_encode(template: str, text: str) -> str:
    safe = urllib.parse.quote(text, safe="")
    return template.replace("{q}", safe).replace("%s", safe)


class SearchDispatcher:
    """复刻 search.lua 里的双模式入口。

    用法:
        d = SearchDispatcher(DispatcherConfig(search_in_app=True,
                  default_search_engine="https://www.google.com/search?q={q}"),
                            engine=SearchEngine(con))
        d.dispatch("24")
    """

    def __init__(
        self,
        config: DispatcherConfig,
        engine: Optional[SearchEngine] = None,
    ):
        self.config = config
        self.engine = engine

    def dispatch(self, text: str, opts: Optional[SearchOptions] = None) -> Any:
        """对应 onreturn_search — 按 search.searchDesktopCal 切流。"""
        if self.config.search_in_app and self.engine is not None:
            return self.engine.search(opts or SearchOptions(keyword=text))
        # Web 模式: 同 search.ClickAndOpenExplorer(UrlEncode(...))
        url = _url_encode(self.config.default_search_engine, text)
        self.config.open_web(url)
        return None


# ============================================================================
# 8. 即时重搜 hook — 对应 try_auto_search
# ============================================================================
class LiveSearch:
    """把"任一筛选变化即重搜"接到回调, 对应 history_search.lua::try_auto_search.

    用法:
        live = LiveSearch(engine)
        live.set_filters(completion=1, type_filter=2)
        results = live.run("keyword")  # 自动 SearchOptions(keyword=...)
    """

    def __init__(self, engine: SearchEngine):
        self.engine = engine
        self.opts = SearchOptions()

    def set_filters(self, **kwargs) -> "LiveSearch":
        for k, v in kwargs.items():
            if hasattr(self.opts, k):
                setattr(self.opts, k, v)
        return self

    def run(self, keyword: Optional[str] = None) -> list[SearchHit]:
        if keyword is not None:
            self.opts.keyword = keyword
        # 任一非默认值就允许重搜 — 对应 is_search_condition_ready
        if not any([
            normalize_keyword(self.opts.keyword),
            self.opts.completion,
            self.opts.type_filter,
            self.opts.text_color,
            self.opts.range_months and self.opts.range_months > 0,
            self.opts.range_months == -1,
        ]):
            return []
        return self.engine.search(self.opts)


__all__ = [
    "SEARCH_TEXT_COLOR_PALETTE",
    "TYPE_ICONS",
    "markdown_to_plain_text",
    "normalize_keyword",
    "parse_content_for_display",
    "is_content_match_keyword",
    "text_has_keyword",
    "ColorMatcher",
    "grid_line_matches",
    "each_content_line",
    "open_db",
    "SearchEngine",
    "SearchOptions",
    "SearchHit",
    "SearchDispatcher",
    "DispatcherConfig",
    "LiveSearch",
]
