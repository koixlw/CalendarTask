"""测试公用工具。

造数据的逻辑放这里, pytest 的 conftest.py 和脚本式测试都从这里拿,
免得两处各写一份、改了一处忘了另一处。
"""

from __future__ import annotations
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def seed_report_fixture(con) -> "date":
    """造一周的测试数据 —— 日报/周报统计用。

    基准日 2026-10-19 (周一), 7 天:
      - 每天一个格子条目, 内容 4 行, 其中 2 行已完成
      - 隔天一个重复条目
      - 第 3 天一个倒数日条目
    """
    from pycal.db.models import CalendarItem
    from pycal.db.repository import upsert_item

    base = date(2026, 10, 19)      # 周一
    for i in range(7):
        d = base + timedelta(days=i)
        iso = d.isoformat()
        # 周一：4 行，2 完成
        upsert_item(con, CalendarItem(
            item_id=f"m{i}", date=iso, type=0, title=f"第{i+1}天计划",
            content="[+] 晨会\n[ ] 写文档\n[ ] code review\n[+] 买咖啡"))
        if i % 2 == 0:
            upsert_item(con, CalendarItem(
                item_id=f"r{i}", date=iso, type=1, title="每天跑步",
                content="[ ] 3km", is_completed=1 if i < 2 else 0))
        if i == 2:
            upsert_item(con, CalendarItem(
                item_id=f"c{i}", date=iso, type=3, title="项目截止"))
            con.execute(
                "INSERT INTO countdowns(item_id,target_date,repeat_yearly) "
                "VALUES (?,?,0)", (f"c{i}", "2026-11-11"))
    return base


def make_temp_db() -> str:
    """一个干净的临时 .db 路径 (调用方负责删)。"""
    return tempfile.NamedTemporaryFile(suffix=".db", delete=False).name


def cleanup_db(path: str) -> None:
    """删掉 .db 和它的 WAL/SHM 伴生文件。"""
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except OSError:
            pass
