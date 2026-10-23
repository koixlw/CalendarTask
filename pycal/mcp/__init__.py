"""MCP server — 把 PyCal 的搜索/日程暴露给 AI 代理。

桌面版 dkappaimcp.dll 用 MCP over stdio 暴露。本期我们用相同的传输
(mcp.server.Server + stdio) 提供一组精简的工具, 后续扩展:
  - search(keyword, filters)
  - today()
  - week()
  - add_item(...)
  - update_item(...)
"""

from __future__ import annotations
import asyncio
import json
import sys
from typing import Any

from pycal.db.connection import open_default
from pycal.db.repository import list_items_by_date, upsert_item
from pycal.search import SearchEngine, SearchOptions
from pycal.db.models import CalendarItem


def _serialize_item(it: CalendarItem) -> dict[str, Any]:
    return {
        "item_id": it.item_id,
        "date": it.date,
        "type": it.type,
        "title": it.title,
        "content": it.content,
        "color_ref": it.color_ref,
        "is_completed": it.is_completed,
    }


def build_server():
    """构造并配置 MCP server。

    用 mcp SDK 的 FastMCP (high-level helper) — 比 Server 简单。
    """
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("pycal")

    @mcp.tool()
    def search(keyword: str = "", completion: int = 0,
               type_filter: int = 0, text_color: int = 0,
               range_months: int = 0) -> str:
        """搜索 PyCal 日历/事件。

        - keyword    : 关键字 (大小写不敏感, plain text)
        - completion : 0=全部 / 1=已完成 / 2=未完成
        - type_filter: 0=全部 / 1=格子 2=重复 3=提醒 4=倒数日
        - text_color : 0=任意 / COLORREF (BGR)
        """
        engine = SearchEngine(open_default())
        opts = SearchOptions(
            keyword=keyword, completion=completion,
            type_filter=type_filter, text_color=text_color,
            range_months=range_months if range_months > 0 else None,
        )
        hits = engine.search(opts)
        return json.dumps(
            [_serialize_item_h(h) for h in hits],
            ensure_ascii=False, indent=2,
        )

    @mcp.tool()
    def today() -> str:
        """今天的日程列表。"""
        from datetime import date
        items = list_items_by_date(open_default(), date.today().isoformat())
        return json.dumps(
            [_serialize_item(it) for it in items],
            ensure_ascii=False, indent=2,
        )

    @mcp.tool()
    def list_day(date_iso: str) -> str:
        """指定日期的日程列表 (YYYY-MM-DD)。"""
        items = list_items_by_date(open_default(), date_iso)
        return json.dumps(
            [_serialize_item(it) for it in items],
            ensure_ascii=False, indent=2,
        )

    @mcp.tool()
    def add_item(item_id: str, date_iso: str, title: str = "",
                 content: str = "", type: int = 0,
                 color_ref: int = 0) -> str:
        """新增一个日历条目。"""
        item = CalendarItem(
            item_id=item_id, date=date_iso, type=type,
            title=title, content=content, color_ref=color_ref,
        )
        upsert_item(open_default(), item)
        return f"ok: added {item_id}"

    return mcp


def _serialize_item_h(h) -> dict[str, Any]:
    return {
        "item_id": h.item_id, "date": h.date, "type": h.type,
        "title": h.title, "matched_line": h.matched_line,
    }


def run_mcp_server() -> None:
    """CLI 入口 — python -m pycal.mcp"""
    server = build_server()
    server.run()


if __name__ == "__main__":
    run_mcp_server()
