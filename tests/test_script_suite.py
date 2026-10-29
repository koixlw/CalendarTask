"""把脚本式测试当子进程跑一遍。

tests/ 里有两套东西:
  - **pytest 风格** —— test_smoke / test_sync / test_report / test_ai_polish,
    pytest 直接收集, `pytest tests/` 就能跑。
  - **脚本风格** —— 剩下的文件自己带 main(), 跑一遍打印 [OK]/[FAIL] 清单。
    GUI 交互那套断言全在这边 (真发鼠标事件、回车写回、八向缩放…),
    但 pytest 收集不到 —— 少了它们, CI 上等于什么都没测。

所以这里用子进程把脚本风格那套也拉起来: 退出码非 0 就算失败。
每个子进程用独立的临时 APPDATA, 绝不碰用户真实数据。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 脚本风格的测试文件 —— 每个都有 main(), 自己跑自己
SCRIPT_TESTS = [
    "test_checkbox_toggle.py",
    "test_close_behavior.py",
    "test_font_scale.py",
    "test_quick_panel.py",
    "test_resize.py",
    "test_toolbar_clicks.py",
    "test_ui_smoke.py",
    "test_year_view.py",
]


@pytest.mark.parametrize("script", SCRIPT_TESTS)
def test_script_suite(script: str) -> None:
    path = ROOT / "tests" / script
    assert path.exists(), f"找不到测试脚本: {path}"

    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"     # 不开真窗口
    env["PYTHONIOENCODING"] = "utf-8"        # 别被 GBK 控制台噎死
    env["APPDATA"] = tempfile.mkdtemp(prefix="pycal_suite_")
    env["PYTHONPATH"] = str(ROOT)

    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )

    if proc.returncode != 0:
        pytest.fail(
            f"{script} 退出码 {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout[-4000:]}\n"
            f"--- stderr ---\n{proc.stderr[-4000:]}"
        )
