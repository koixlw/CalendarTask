"""全局配置读写 (settings 表)。

主窗和桌面挂件都要读写同一张 settings 表, 所以放一个公共地方 ——
之前读写函数写在 main_window 里, 挂件那边 import 就会绕成环
(main_window 要 import 挂件, 挂件又要 import main_window)。
"""

from __future__ import annotations


def get_setting(key: str, default: str = "") -> str:
    """读一项配置。读不到 / 出错都返回 default —— 配置坏了不该把界面带崩。"""
    try:
        from pycal.db.connection import open_default
        row = open_default().execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        return row["value"] or default
    except Exception:
        return default


def set_setting(key: str, value: str) -> None:
    """写一项配置。写失败只打印 —— 存不进去不该中断操作。"""
    try:
        from pycal.db.connection import open_default
        open_default().execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
            (key, value),
        )
    except Exception as e:
        print(f"[setting] save failed: {e}")


def get_float(key: str, default: float) -> float:
    """读一项浮点配置 — 存的值被手改坏了就退回默认值。"""
    try:
        return float(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


def get_int(key: str, default: int) -> int:
    """读一项整数配置。"""
    try:
        return int(float(get_setting(key, str(default))))
    except (TypeError, ValueError):
        return default


__all__ = ["get_setting", "set_setting", "get_float", "get_int"]
