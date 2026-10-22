"""AI 润色 CLI。

用法:
  # 先看有没有配 key
  python -m pycal.report.ai_polish_cli --check

  # 润色今天日报 (默认简洁)
  python -m pycal.report.ai_polish_cli

  # 润色本周周报, 多风格 + 复盘
  python -m pycal.report.ai_polish_cli --week --style concise,analysis

  # 加补充说明
  python -m pycal.report.ai_polish_cli --week \\
      --note "周报里的评审延到下周三, 因为等设计稿"

  # 换模型 / 输出
  python -m pycal.report.ai_polish_cli --week --model claude-haiku-4-5
  python -m pycal.report.ai_polish_cli --week --html --out D:/reports
  python -m pycal.report.ai_polish_cli --week --stream
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import date
from pathlib import Path

from pycal.report.ai_polish import (
    STYLES, PolishError, ClaudePolishClient, has_api_key,
    api_key_hint, MODEL_DEFAULT, MODEL_FAST, MODEL_SMART,
)


def _setup_stdout() -> None:
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if s is None:
            continue
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _build_report(args) -> tuple[str, str]:
    """返回 (markdown, kind)。"""
    from pycal.report import render_daily_markdown, render_weekly_markdown
    if args.week:
        return render_weekly_markdown(date.today()), "周报"
    if args.last_week:
        from datetime import timedelta
        return render_weekly_markdown(
            date.today() - timedelta(days=7)), "周报"
    if args.day:
        return render_daily_markdown(date.fromisoformat(args.day)), "日报"
    return render_daily_markdown(date.today()), "日报"


def main() -> int:
    _setup_stdout()
    p = argparse.ArgumentParser(prog="pycal-report-polish")
    p.add_argument("--week", action="store_true")
    p.add_argument("--last-week", action="store_true")
    p.add_argument("--day", metavar="YYYY-MM-DD")
    p.add_argument(
        "--style", default="concise",
        help=f"风格, 逗号分隔。可选: {','.join(STYLES)}",
    )
    p.add_argument("--note", default="", help="额外说明 / 背景")
    p.add_argument(
        "--model", default=MODEL_DEFAULT,
        help=f"模型 ID (默认 {MODEL_DEFAULT}; 快: {MODEL_FAST})",
    )
    p.add_argument("--stream", action="store_true", help="流式输出")
    p.add_argument("--html", action="store_true")
    p.add_argument("--out", metavar="PATH")
    p.add_argument("--open", action="store_true")
    p.add_argument("--check", action="store_true", help="只测连通性")
    p.add_argument("--all-styles", action="store_true",
                   help="同报告用所有风格各润色一次 (成本 x4)")
    args = p.parse_args()

    # 连通性检查
    if args.check:
        if not has_api_key():
            print(api_key_hint())
            return 1
        ok, msg = ClaudePolishClient(args.model).check_connection()
        print(f"{'✓' if ok else '✗'} {msg}")
        return 0 if ok else 1

    if not has_api_key():
        print(api_key_hint())
        return 1

    styles = [s.strip() for s in args.style.split(",") if s.strip()]
    bad = [s for s in styles if s not in STYLES]
    if bad:
        print(f"未知风格: {', '.join(bad)}")
        print(f"可选: {', '.join(STYLES)}")
        return 1

    try:
        report_md, kind = _build_report(args)
    except Exception as e:
        print(f"生成原始报告失败: {e}")
        return 1

    if not report_md.strip():
        print("报告内容为空, 无法润色")
        return 1

    client = ClaudePolishClient(args.model)

    # 多风格对比
    if args.all_styles:
        from pycal.report.ai_polish import polish_multiple_styles
        print(f"正在用 {len(STYLES)} 种风格润色 (成本 ×{len(STYLES)})...\n")
        results = polish_multiple_styles(report_md, list(STYLES), kind,
                                         args.model)
        for key, res in results.items():
            label = STYLES[key].label
            print("=" * 70)
            print(f"### {label}")
            print("=" * 70)
            if isinstance(res, Exception):
                print(f"失败: {res}")
            else:
                print(res.text)
                print(f"\n[成本 ~{res.cost_estimate}, "
                      f"{res.input_tokens} in / {res.output_tokens} out]")
            print()
        return 0

    # 单风格 (可选流式)
    try:
        if args.stream:
            sys.stderr.write(f"正在润色{kind} ({args.model})...\n")
            chunks: list[str] = []
            for chunk in client.polish_stream(
                report_md, styles, kind, args.note
            ):
                chunks.append(chunk)
                sys.stderr.write(f"\r已生成 {len(''.join(chunks))} 字")
                sys.stderr.flush()
            sys.stderr.write("\n")
            result_text = "".join(chunks)
            usage = PolishResultDummy(result_text, args.model)
        else:
            sys.stderr.write(f"正在润色{kind} ({args.model})...\n")
            r = client.polish(report_md, styles, kind, args.note)
            result_text = r.text
            usage = r
    except PolishError as e:
        print(f"✗ {e.message}")
        if e.hint:
            print(f"  提示: {e.hint}")
        return 1

    # 输出
    content = result_text
    if args.html:
        from pycal.report import render_html
        content = render_html(result_text, f"PyCal {kind} (AI 润色)")

    if args.out:
        base = Path(args.out)
        if base.suffix in (".md", ".html", ".htm"):
            base.parent.mkdir(parents=True, exist_ok=True)
            path = base
        else:
            base.mkdir(parents=True, exist_ok=True)
            ext = "html" if args.html else "md"
            today = date.today()
            path = base / f"pycal_{args.week or args.last_week and 'weekly' or 'daily'}_{today:%Y%m%d}_ai.{ext}"
        path.write_text(content, encoding="utf-8")
        print(f"✓ 已保存: {path}")
        if args.open:
            webbrowser.open(path.as_uri())
    else:
        sys.stdout.buffer.write(content.encode("utf-8"))
        print()

    cost = getattr(usage, "cost_estimate", "")
    if cost:
        print(f"\n[成本 ~{cost}]", file=sys.stderr)
    return 0


class PolishResultDummy:
    """流式模式的轻量结果包装 (只有文本, 无精确 token 数)。"""

    def __init__(self, text: str, model: str):
        self.text = text
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0
        # 粗略按字符数估 (~4 字符/token)
        self._est = len(text) // 4
        self._est_in = 0

    @property
    def cost_estimate(self) -> str:
        cost = (self._est * 15.0) / 1_000_000
        return f"${cost:.4f} (估算)"


if __name__ == "__main__":
    sys.exit(main())