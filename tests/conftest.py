"""pytest 全局配置 —— 给需要数据库连接的测试提供 `con` fixture。

只跟测试有关的代码放这里, 应用代码不依赖。
"""

from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# GUI 测试跑在 offscreen 平台上, 不然 CI 上会开真窗口
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def con():
    """一个带好测试数据的临时 SQLite 连接。

    用完自动关掉并删掉临时文件 —— 绝不碰用户真实的 %APPDATA% 数据。
    """
    from pycal.db.connection import _make_conn
    from helpers import seed_report_fixture, make_temp_db, cleanup_db

    path = make_temp_db()
    conn = _make_conn(path)
    try:
        seed_report_fixture(conn)
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass
        cleanup_db(path)
