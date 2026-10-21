"""数据层。

- schema.sql    PyCal 独立 schema
- connection    SQLite 连接管理 (per-thread)
- models        dataclass 镜像
"""

from pycal.db.connection import open_default, open_connection

__all__ = ["open_default", "open_connection"]
