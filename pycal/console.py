"""控制台输出兼容性。

Windows 中文版的控制台默认编码是 GBK (cp936), 打印 ✓ ✗ ◀ ☐ 这类
字符会直接抛 UnicodeEncodeError, 把整个 CLI 打断。

在所有 CLI 入口最开头调用 setup_stdout() 就行。
"""

from __future__ import annotations
import sys


def setup_stdout() -> None:
    """把 stdout/stderr 切到 UTF-8, 遇到编不出来的字符就替换掉。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # 已经被别的库重定向过了, 或者不是 TextIOWrapper —— 算了
            pass
