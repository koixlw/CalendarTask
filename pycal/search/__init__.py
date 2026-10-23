"""搜索模块 — 兼容 DesktopCal Lua 实现的语义 + PyCal 新 schema。

搜索架构:
  原生层  : SQLite FTS5 (calendar_items_fts)         → 取候选集
  过滤层  : Python (颜色+完成度+类型+关键字+时间)     → 富匹配

匹配算法直接复用 desktopcal_search.py 的原语:
  - text_has_keyword            : 大小写不敏感, plain text
  - is_content_match_keyword    : MarkdownParse 后可见正文
  - ColorMatcher                : 防色值数字误命中 (RGB/BGR, 带#/不带#)
  - grid_line_matches           : 逐行 AND (完成度+颜色+关键字)
"""

from __future__ import annotations
import re
import sqlite3
import time as _time
from dataclasses import dataclass
from typing import Optional

# 复用已有原语
from desktopcal_search import (
    normalize_keyword,
    text_has_keyword,
    is_content_match_keyword,
    parse_content_for_display,
    ColorMatcher,
    grid_line_matches,
    each_content_line,
    SEARCH_TEXT_COLOR_PALETTE,
)


@dataclass
class SearchOptions:
    keyword: str = ""
    completion: int = 0          # 0=全部 / 1=已完成 / 2=未完成
    type_filter: int = 0         # 0=全部 / 1=格子 2=重复 3=提醒 4=倒数日
    text_color: int = 0
    range_months: Optional[int] = 0
    range_start: Optional[str] = None
    range_end: Optional[str] = None


@dataclass
class SearchHit:
    item_id: str
    type: int
    title: Optional[str]
    content: Optional[str]
    date: Optional[str]
    matched_line: Optional[str] = None


class SearchEngine:
    """搜索主类。

    区别于 DesktopCal 原生层 (C++ 进程内调用),
    这里 SQLite FTS5 是同进程内, 但严格分层 (候选 → 过滤) 与 Lua 逻辑一致。
    """

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def _fts_candidates(self, keyword: str) -> list[sqlite3.Row]:
        """候选集 — 分两层策略。

        FTS5 的 unicode61 tokenizer 把连续的中英数字当成**一个** token,
        例如 "24分钟" 会被切成单个 token, 导致 `MATCH '"24"'` 匹配不到 ——
        而 DesktopCal 的原始实现是 Python `string.find()` 子串匹配, 是能
        匹配到的。所以 FTS 只能做**粗筛** (宁可多召回, 不能漏)。

        策略:
          - 关键字能干净地作为 phrase 匹配 -> 用 FTS (快)
          - 关键字含中英混排 / 分词边界可能切开 -> 退回 SQL LIKE (准)
        最终精确匹配一律由 Python 过滤层 (is_content_match_keyword) 负责。
        """
        kw = normalize_keyword(keyword)
        if not kw:
            cur = self.con.execute(
                "SELECT * FROM calendar_items WHERE type != 4",
            )
            return cur.fetchall()

        # FTS 分词只在「内容本身不含 CJK 粘连」时才可靠。
# 判断方法: 查一下 FTS 索引里有多少行含该关键字。如果索引命中数
# 明显少于 LIKE 子串命中数, 说明分词把关键字切碎了, 退回 LIKE。
        use_fts = kw.isascii()
        if use_fts:
            escaped = '"' + kw.replace('"', '""') + '"'
            try:
                cur = self.con.execute(
                    "SELECT i.* FROM calendar_items i"
                    " JOIN items_fts f ON i.rowid = f.rowid"
                    " WHERE items_fts MATCH ? AND i.type != 4",
                    (escaped,),
                )
                rows = cur.fetchall()
                if rows:
                    # 交叉验证: LIKE 粗筛的召回必须 >= FTS, 否则说明 FTS 漏了
                    # (分词把关键字切碎了, 如 "24分钟" 被当成一个 token)
                    like_kw_0 = (
                        kw.replace("\\", "\\\\").replace("%", "\\%")
                          .replace("_", "\\_")
                    )
                    like_rows = self.con.execute(
                        "SELECT item_id FROM calendar_items"
                        " WHERE type != 4"
                        "   AND (title LIKE ? ESCAPE '\\'"
                        "     OR content LIKE ? ESCAPE '\\')",
                        (f"%{like_kw_0}%", f"%{like_kw_0}%"),
                    ).fetchall()
                    if len(like_rows) > len(rows):
                        pass          # FTS 漏了 -> 落到下面的 LIKE 分支
                    else:
                        return rows
            except sqlite3.OperationalError:
                pass

        # 含 CJK 或 FTS 没命中 -> 用 LIKE 子串匹配兜底
        # 先转义 LIKE 通配符, 否则用户搜 "50%" 或 "a_b" 会误召回
        like_kw = (
            kw.replace("\\", "\\\\")
              .replace("%", "\\%")
              .replace("_", "\\_")
        )
        pattern = f"%{like_kw}%"
        cur = self.con.execute(
            "SELECT * FROM calendar_items"
            " WHERE type != 4"
            "   AND (title LIKE ? ESCAPE '\\'"
            "     OR content LIKE ? ESCAPE '\\')",
            (pattern, pattern),
        )
        return cur.fetchall()

    def filter_by_range(
        self,
        row: sqlite3.Row,
        range_months: Optional[int],
        now_epoch: int,
        range_start: Optional[str] = None,
        range_end: Optional[str] = None,
    ) -> bool:
        if range_months is None or range_months == 0:
            return True
        date_str = row["date"]
        if not date_str:
            return True
        try:
            y, m, d = (int(x) for x in date_str.split("-"))
        except ValueError:
            return True
        from datetime import datetime, timedelta
        try:
            target = datetime(y, m, d)
        except ValueError:
            return True

        if range_months == -1:
            if not range_start or not range_end:
                return True
            try:
                sy = (int(x) for x in range_start.split("-"))
                ey = (int(x) for x in range_end.split("-"))
                s_tuple = tuple(sy)
                e_tuple = tuple(ey)
            except ValueError:
                return True
            return s_tuple <= (y, m, d) <= e_tuple

        now = datetime.fromtimestamp(now_epoch)
        delta = now - target
        return timedelta(0) <= delta <= timedelta(days=range_months * 30)

    def is_data_match_filters(
        self,
        row: sqlite3.Row,
        completion: int,
        type_filter: int,
        text_color: int,
        keyword: str,
    ) -> bool:
        t = row["type"] or 0
        is_third = (row["group_id"] or "") == "thirdcal"
        content = row["content"] or ""

        if t == 4:
            return False
        if type_filter == 1 and (is_third or t != 0):
            return False
        if type_filter == 2 and t != 1:
            return False
        if type_filter == 3 and t != 2:
            return False
        if type_filter == 4 and t != 3:
            return False

        # 格子逐行
        if t == 0 and not is_third:
            return any(
                grid_line_matches(line, completion, text_color, keyword)
                for line in each_content_line(content)
            )

        if completion == 1 and t in (1, 2, 3):
            if (row["is_completed"] or 0) != 1:
                return False
        elif completion == 1:
            return False

        if text_color:
            if keyword and not is_content_match_keyword(content, keyword):
                return False
            return ColorMatcher(text_color).match_color_and_keyword(content, keyword)
        if keyword:
            return is_content_match_keyword(content, keyword)
        return True

    def search(
        self,
        opts: SearchOptions,
        now_epoch: Optional[int] = None,
    ) -> list[SearchHit]:
        now = now_epoch if now_epoch is not None else int(_time.time())
        hits: list[SearchHit] = []
        for row in self._fts_candidates(opts.keyword):
            if not self.filter_by_range(
                row, opts.range_months, now,
                opts.range_start, opts.range_end,
            ):
                continue
            if not self.is_data_match_filters(
                row, opts.completion, opts.type_filter,
                opts.text_color, opts.keyword,
            ):
                continue

            hit = SearchHit(
                item_id=row["item_id"],
                type=row["type"],
                title=row["title"],
                content=row["content"],
                date=row["date"],
            )
            if hit.type == 0 and (row["group_id"] or "") != "thirdcal":
                for line in each_content_line(row["content"]):
                    if grid_line_matches(
                        line, opts.completion, opts.text_color, opts.keyword
                    ):
                        hit.matched_line = line
                        break
            hits.append(hit)
        return hits


__all__ = [
    "SearchOptions", "SearchHit", "SearchEngine",
    "normalize_keyword", "text_has_keyword", "is_content_match_keyword",
    "ColorMatcher", "grid_line_matches", "each_content_line",
    "parse_content_for_display", "SEARCH_TEXT_COLOR_PALETTE",
]
