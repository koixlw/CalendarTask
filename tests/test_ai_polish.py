"""AI 润色模块测试 — 不调 API, 验证 prompt 构造与错误处理。

跑: python tests/test_ai_polish.py
"""

from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pycal.console import setup_stdout
# Windows 中文控制台默认 GBK, 打印 ✓/◀/☐ 会抛 UnicodeEncodeError
setup_stdout()


PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = ""):
    if cond:
        PASS.append(name)
        print(f"  [OK]   {name}")
    else:
        FAIL.append(f"{name} {extra}")
        print(f"  [FAIL] {name} {extra}")


def test_styles():
    print("\n=== 1. 风格定义 ===")
    from pycal.report.ai_polish import STYLES, DEFAULT_STYLES
    check("4 种风格", len(STYLES) == 4, f"got {len(STYLES)}")
    for key in ("concise", "professional", "friendly", "analysis"):
        check(f"风格 {key} 存在", key in STYLES)
        if key in STYLES:
            check(f"风格 {key} 有 instruction",
                  len(STYLES[key].instruction) > 20)
            check(f"风格 {key} 有 label", bool(STYLES[key].label))
    check("默认 4 种都启用", len(DEFAULT_STYLES) == 4)


def test_system_prompt():
    print("\n=== 2. System prompt 反编造约束 ===")
    from pycal.report.ai_polish import SYSTEM_PROMPT
    # 必须明确禁止编造
    check("提到不编造", "绝不编造" in SYSTEM_PROMPT or "不编造" in SYSTEM_PROMPT)
    check("提到保留数字", "数字" in SYSTEM_PROMPT)
    check("提到 Markdown 结构", "Markdown" in SYSTEM_PROMPT)
    check("提到保留条目", "条目" in SYSTEM_PROMPT)
    check("禁止前言后语", "不要" in SYSTEM_PROMPT and "前言" in SYSTEM_PROMPT)


def test_user_prompt():
    print("\n=== 3. User prompt 构造 ===")
    from pycal.report.ai_polish import build_user_prompt, STYLES

    md = "# 📋 日报 · 2026-10-21\n\n- [x] 晨会\n- [ ] 写文档"

    # 单风格
    p = build_user_prompt(md, ["concise"], "日报")
    check("含任务说明", "润色这份日报" in p)
    check("含原始报告", "写文档" in p)
    check("单风格不列编号", "1." not in p.split("## 原始报告")[0])
    check("含分隔标记", "---" in p)

    # 多风格
    p2 = build_user_prompt(md, ["concise", "analysis"], "周报")
    check("多风格有编号", "1." in p2 and "2." in p2)
    check("多风格有标签名", "简洁精炼" in p2 and "带复盘分析" in p2)
    check("周报类型正确", "润色这份周报" in p2)

    # 补充说明
    p3 = build_user_prompt(md, ["concise"], "日报", "评审延期是因为等设计稿")
    check("含补充说明", "评审延期" in p3)
    check("补充说明有标注", "用户补充说明" in p3)

    # 未知风格回退到 concise 的 instruction
    p4 = build_user_prompt(md, ["nonexistent"], "日报")
    check("未知风格回退到简洁",
          STYLES["concise"].instruction in p4)
    check("回退后仍含原始报告", "写文档" in p4)

    # 空风格回退
    p5 = build_user_prompt(md, [], "日报")
    check("空风格列表不崩", len(p5) > 0)


def test_no_api_key():
    print("\n=== 4. 无 API key 处理 ===")
    from pycal.report.ai_polish import (
        has_api_key, api_key_hint, ClaudePolishClient, PolishError,
    )
    # 模拟未配置 (不改真实环境, 用 monkeypatch 式的局部检查)
    # 注意: 现在 key 可能存在本机 settings 表里 (界面里填的),
    # 所以要把「解析 key」整个换掉, 光 pop 环境变量不够 ——
    # 否则用户配过 key 之后这个测试就红了。
    import pycal.report.ai_polish as mod
    old_env = mod.os.environ.get("ANTHROPIC_API_KEY")
    old_resolve = mod.resolve_api_key
    mod.os.environ.pop("ANTHROPIC_API_KEY", None)
    mod.resolve_api_key = lambda: ""
    try:
        check("has_api_key 返回 False", has_api_key() is False)
        hint = api_key_hint()
        check("hint 提到环境变量名", "ANTHROPIC_API_KEY" in hint)
        check("hint 提到获取链接", "console.anthropic.com" in hint)
        check("hint 提到界面入口", "⋯ 更多" in hint or "API key" in hint)

        # 调用应该抛 PolishError 而不是别的
        try:
            ClaudePolishClient().polish("test markdown", ["concise"])
            check("无 key 时抛 PolishError", False, "没有抛异常")
        except PolishError as e:
            check("无 key 时抛 PolishError", True)
            check("错误信息可读", "ANTHROPIC_API_KEY" in e.message or
                  "key" in e.message.lower())
        except Exception as e:
            check("无 key 时抛 PolishError", False,
                  f"抛了 {type(e).__name__}")
    finally:
        mod.resolve_api_key = old_resolve
        if old_env is not None:
            mod.os.environ["ANTHROPIC_API_KEY"] = old_env


def test_key_store():
    """界面里填的 key —— 存 / 取 / 清除, 以及跟环境变量的优先级。"""
    print("\n=== 4b. API key 存取 ===")
    import pycal.report.ai_polish as mod
    import pycal.db.connection as conn_mod
    from pathlib import Path
    import tempfile

    # 换成临时数据库, 别把测试 key 写进用户真的库
    tmp_db = Path(tempfile.mkdtemp(prefix="pycal_key_")) / "t.db"
    old_open = conn_mod.open_default
    conn_mod.open_default = lambda: conn_mod.open_connection(tmp_db)
    old_env = mod.os.environ.get("ANTHROPIC_API_KEY")
    old_resolve = mod.resolve_api_key
    try:
        mod.os.environ.pop("ANTHROPIC_API_KEY", None)

        check("初始没有 key", mod.has_api_key() is False)
        check("初始来源为空", mod.api_key_source() == "")

        mod.save_api_key("sk-ant-test-123")
        check("保存后能读回来", mod.load_api_key() == "sk-ant-test-123")
        check("保存后 has_api_key 为真", mod.has_api_key() is True)
        check("来源标记为 saved", mod.api_key_source() == "saved")
        check("明文没直接进 settings",
              "sk-ant-test-123" not in mod._setting_get(mod.KEY_SETTING))

        # 环境变量也有 key 时, 界面里存的优先
        mod.os.environ["ANTHROPIC_API_KEY"] = "sk-ant-from-env"
        check("界面里的 key 优先于环境变量",
              mod.resolve_api_key() == "sk-ant-test-123",
              f"got {mod.resolve_api_key()}")
        check("来源仍是 saved", mod.api_key_source() == "saved")

        # 界面里删掉 -> 退回环境变量
        mod.clear_api_key()
        check("清除后读不到", mod.load_api_key() == "")
        check("清除后退回环境变量",
              mod.resolve_api_key() == "sk-ant-from-env",
              f"got {mod.resolve_api_key()}")
        check("来源变成 env", mod.api_key_source() == "env")

        # 环境变量也没有时就是彻底没配
        mod.os.environ.pop("ANTHROPIC_API_KEY", None)
        check("都没配时 has_api_key 为假", mod.has_api_key() is False)
    finally:
        conn_mod.open_default = old_open
        mod.resolve_api_key = old_resolve
        if old_env is not None:
            mod.os.environ["ANTHROPIC_API_KEY"] = old_env


def test_empty_report():
    print("\n=== 5. 空报告处理 ===")
    from pycal.report.ai_polish import ClaudePolishClient, PolishError
    try:
        ClaudePolishClient().polish("   ", ["concise"])
        check("空报告抛错", False, "没有抛异常")
    except PolishError as e:
        check("空报告抛 PolishError", True)
        check("空报告提示可读", "空" in e.message)
    except Exception as e:
        check("空报告抛 PolishError", False, f"抛了 {type(e).__name__}")


def test_error_wrapping():
    print("\n=== 6. SDK 异常分类 ===")
    from pycal.report.ai_polish import _wrap_error, PolishError

    cases = [
        ("RateLimitError: 429 too many requests", "速率限制"),
        ("AuthenticationError: 401 invalid x-api-key", "API key 无效"),
        ("PermissionDeniedError: 403 forbidden", "权限不足"),
        ("NotFoundError: 404 model not found", "模型不存在"),
        ("APIStatusError: 529 overloaded", "繁忙"),
    ]
    for raw, expect in cases:
        cls = type(raw.split(":")[0].replace(" ", ""), (Exception,), {})
        exc = cls(raw)
        wrapped = _wrap_error(exc)
        check(f"{expect} 被识别",
              isinstance(wrapped, PolishError) and expect in wrapped.message,
              f"got {wrapped.message[:40]}")


def test_cost_estimate():
    print("\n=== 7. 成本估算 ===")
    from pycal.report.ai_polish import PolishResult
    r = PolishResult(text="x" * 8000, model="claude-sonnet-5",
                     input_tokens=1500, output_tokens=2000)
    check("cost_estimate 有值", "$" in r.cost_estimate)
    # 1500*3 + 2000*15 = 4500+30000 = 34500 / 1e6 = $0.0345
    check("成本数值合理", 0.03 < float(r.cost_estimate[1:]) < 0.04,
          f"got {r.cost_estimate}")


def test_imports():
    print("\n=== 8. 模块导入 ===")
    import importlib
    for m in ["pycal.report.ai_polish", "pycal.report.ai_polish_cli"]:
        try:
            importlib.import_module(m)
            check(f"import {m}", True)
        except Exception as e:
            check(f"import {m}", False, str(e)[:80])
    try:
        importlib.import_module("pycal.ui.ai_polish_dialog")
        check("import pycal.ui.ai_polish_dialog", True)
    except Exception as e:
        check("import pycal.ui.ai_polish_dialog", False, str(e)[:80])


def main() -> int:
    print("=" * 70)
    print("PyCal AI 润色模块测试 (不需要 API key)")
    print("=" * 70)
    test_styles()
    test_system_prompt()
    test_user_prompt()
    test_no_api_key()
    test_key_store()
    test_empty_report()
    test_error_wrapping()
    test_cost_estimate()
    test_imports()

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("\n失败项:")
        for f in FAIL:
            print(f"  x {f}")
        return 1
    print("全部通过")
    print("\n注意: 这些测试不调 API。要测真实润色, 设置 ANTHROPIC_API_KEY 后跑:")
    print("  python -m pycal.report.ai_polish_cli --check")
    print("  python -m pycal.report.ai_polish_cli --week --style concise,analysis")
    return 0


if __name__ == "__main__":
    sys.exit(main())