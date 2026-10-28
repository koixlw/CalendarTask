"""AI 润色 — 用 Claude 把日报/周报改写得更通顺、更有洞察。

设计原则:
  - **不编造事实** — 严格保留原始数据 (数字、日期、条目名、完成状态)
  - **可选风格** — 简洁 / 专业 / 友好 / 复盘分析, 可组合
  - **可离线** — 没配 API key 时模块可用, 只是不能润色
  - **流式输出** — 长报告边生成边显示, 不卡 UI

安全:
  - API key 可以在界面里填 (存 settings 表, base64 + XOR 打乱,
    只防"随手打开 DB 看到明文"), 也认环境变量 ANTHROPIC_API_KEY
  - key 不写日志, 不进异常信息
  - 原始报告可能含私人日程, 调用前提示用户

成本参考 (Sonnet 5 / Haiku 4.5, 2026 定价):
  - 输入 ~$3/M tokens, 输出 ~$15/M tokens
  - 一份日报 ~1500 in + ~2000 out ≈ $0.035
  - 一份周报 ~4000 in + ~5000 out ≈ $0.09
"""

from __future__ import annotations

import base64
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

# 默认模型 — 按任务量分级
MODEL_DEFAULT = "claude-sonnet-5"      # 日报/周报润色: 质量优先
MODEL_FAST = "claude-haiku-4-5"       # 只想快速通顺一下
MODEL_SMART = "claude-opus-5-5"       # 深度复盘分析


# ============================================================================
# 风格
# ============================================================================
@dataclass
class Style:
    key: str
    label: str
    instruction: str


STYLES: dict[str, Style] = {
    "concise": Style(
        key="concise",
        label="简洁精炼",
        instruction=(
            "把每条表述压缩到最短但仍准确。删掉冗余修饰语、重复词、"
            "「进行」「相关」「一些」这类空词。保留所有数字和事实不变。"
        ),
    ),
    "professional": Style(
        key="professional",
        label="专业正式",
        instruction=(
            "用正式的商业/技术书面语。避免口语、俚语、网络用语。"
            "结构调整清晰 (总-分-总), 适合发给上级或团队。"
        ),
    ),
    "friendly": Style(
        key="friendly",
        label="轻松友好",
        instruction=(
            "用自然亲切的第一人称语气, 像在跟同事聊今天干了什么。"
            "句子长短交错, 可以适度用感叹。但不要夸张或灌水。"
        ),
    ),
    "analysis": Style(
        key="analysis",
        label="带复盘分析",
        instruction=(
            "在润色之外增加一个「复盘」小节: 分析完成率高低的原因、"
            "指出做得好的地方、给出具体的改进建议。"
            "建议要可执行 (不要写「要更努力」这种空话)。"
            "如果数据不足以支撑结论, 明确说「数据不足」而不是编造。"
        ),
    ),
}

DEFAULT_STYLES = ["concise", "professional", "friendly", "analysis"]


# ============================================================================
# 错误
# ============================================================================
class PolishError(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint


# ============================================================================
# API key — 界面里填的存 settings 表, 环境变量当后备
# ============================================================================
# 界面里填的 key 存在 settings 表 (跟皮肤/字号放一起), 用 base64 + XOR
# 打乱一下 —— 只防"随手打开 DB 看到明文", 不是强加密。
# 想要更强的保护得走 Windows 凭据管理器, 那是另一个量级的工程。
KEY_SETTING = "anthropic_api_key"
_XOR_KEY = b"pycal-api-v1"


def _obfuscate(plain: str) -> str:
    if not plain:
        return ""
    data = plain.encode("utf-8")
    xored = bytes(b ^ _XOR_KEY[i % len(_XOR_KEY)] for i, b in enumerate(data))
    return base64.b64encode(xored).decode("ascii")


def _deobfuscate(encoded: str) -> str:
    if not encoded:
        return ""
    try:
        xored = base64.b64decode(encoded.encode("ascii"))
        data = bytes(b ^ _XOR_KEY[i % len(_XOR_KEY)] for i, b in enumerate(xored))
        return data.decode("utf-8")
    except Exception:
        return ""


def _setting_get(key: str) -> str:
    """读 settings 表 — 数据库还没建好/坏了都当没配。"""
    try:
        from pycal.db.connection import open_default
        row = open_default().execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return (row["value"] if row else "") or ""
    except Exception:
        return ""


def _setting_set(key: str, value: str) -> None:
    from pycal.db.connection import open_default
    open_default().execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
        (key, value),
    )


def env_api_key() -> str:
    """环境变量里的 key (没设就是空串)。"""
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def load_api_key() -> str:
    """读界面里保存的 key (已解密)。"""
    return _deobfuscate(_setting_get(KEY_SETTING)).strip()


def save_api_key(key: str) -> None:
    """保存 key —— 界面上「保存」按钮调的。"""
    _setting_set(KEY_SETTING, _obfuscate(key.strip()))


def clear_api_key() -> None:
    """删掉界面里保存的 key (环境变量不受影响)。"""
    _setting_set(KEY_SETTING, "")


def resolve_api_key() -> str:
    """当前生效的 key —— 界面里存的优先, 其次环境变量。

    界面里存的排前面: 用户明明在框里填了一个新 key, 结果还是拿环境变量
    里那个旧的跑, 那才叫莫名其妙。
    """
    return load_api_key() or env_api_key()


def api_key_source() -> str:
    """key 从哪来 —— "saved" (界面里存的) / "env" (环境变量) / "" (没有)。"""
    if load_api_key():
        return "saved"
    if env_api_key():
        return "env"
    return ""


def has_api_key() -> bool:
    return bool(resolve_api_key())


def api_key_hint() -> str:
    if has_api_key():
        src = api_key_source()
        if src == "saved":
            return "✓ 已配置 API key (保存在本机)"
        return "✓ 已配置 ANTHROPIC_API_KEY (环境变量)"
    return (
        "未配置 API key。\n\n"
        "最快的方式 — 在 PyCal 界面里填:\n"
        "  点工具栏「⋯ 更多」→「🔑 AI 润色 API key」, 粘进去保存即可。\n\n"
        "命令行方式 (PowerShell, 当前会话有效):\n"
        "  $env:ANTHROPIC_API_KEY = \"sk-ant-...\"\n\n"
        "永久设置 (用户级):\n"
        "  [Environment]::SetEnvironmentVariable(\n"
        "      \"ANTHROPIC_API_KEY\", \"sk-ant-...\", \"User\")\n\n"
        "获取 key: https://console.anthropic.com/settings/keys"
    )


# ============================================================================
# Prompt
# ============================================================================
SYSTEM_PROMPT = """\
你是一位专业的个人效率顾问, 帮用户润色他们的工作报告 (日报 / 周报)。

## 绝对约束 (违反即视为失败)

1. **绝不编造事实。** 所有数字、日期、百分比、条目名称、完成状态都必须
   与原文完全一致。不得新增原文没有的任务、成就、问题或数字。
2. **不改变事实的语义。** 「完成率 60%」不能变成「完成率不错」或「大部分完成」。
3. **不添加原文没有的结论。** 如果数据不足以支撑某个判断, 明确写「数据不足」。
4. **保留全部 Markdown 结构。** 标题层级、表格、任务列表语法 ([ ] / [x])、
   引用块、分隔线都要原样保留对应的语义。
5. **保留所有条目。** 原文有的每一条都不能丢。

## 可以做的

- 改写语句, 让表达更通顺、清晰、专业
- 调整段落顺序, 让逻辑更连贯
- 统一术语和标点风格
- 添加过渡句, 连接原本跳跃的内容
- (仅当启用「复盘分析」风格时) 基于已有数据做分析并提建议

## 输出格式

直接输出润色后的 Markdown, 不要任何前言后语 (不要说「以下是润色后的版本」),
不要用代码块包裹 (除非原文整体就是代码块)。保留原文的一级标题。
"""


def build_user_prompt(
    report_md: str,
    styles: list[str],
    kind: str = "日报",
    extra_notes: str = "",
) -> str:
    """组装 user prompt。"""
    selected = [STYLES[s] for s in styles if s in STYLES]
    if not selected:
        selected = [STYLES["concise"]]

    parts: list[str] = []

    parts.append(f"# 任务\n\n润色这份{kind}。")

    if len(selected) == 1:
        parts.append(f"\n## 风格要求\n\n{selected[0].instruction}")
    else:
        parts.append("\n## 风格要求 (同时满足以下所有点)\n")
        for i, st in enumerate(selected, 1):
            parts.append(f"{i}. **{st.label}** — {st.instruction}")

    if extra_notes.strip():
        parts.append(
            "\n## 用户补充说明\n\n"
            f"{extra_notes.strip()}\n\n"
            "(这是用户的额外要求或背景, 请一并考虑)"
        )

    parts.append(
        "\n## 原始报告\n\n"
        "以下是待润色的内容。请完整保留其事实数据与结构, "
        "按上述风格改写:\n\n"
        "---\n"
        f"{report_md}\n"
        "---"
    )

    return "\n".join(parts)


# ============================================================================
# 客户端
# ============================================================================
@dataclass
class PolishResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""

    @property
    def cost_estimate(self) -> str:
        """粗略成本估算 (美元)。"""
        # 2026 年 Sonnet/Haiku 定价近似
        in_price, out_price = 3.0, 15.0      # per 1M tokens
        cost = (
            self.input_tokens * in_price + self.output_tokens * out_price
        ) / 1_000_000
        return f"${cost:.4f}"


class ClaudePolishClient:
    """调用 Claude 做润色。

    用法 (一次性):
        c = ClaudePolishClient()
        r = c.polish(report_md, ["concise", "analysis"])
        print(r.text)

    流式:
        for chunk in c.polish_stream(report_md, ["friendly"]):
            print(chunk, end="")
    """

    MAX_TOKENS = 8192

    def __init__(self, model: str = MODEL_DEFAULT, timeout: float = 120.0,
                 api_key: str = ""):
        self.model = model
        self.timeout = timeout
        # 显式传入的 key 优先 — 界面里"测试连接"就是这么试还没保存的那一串
        self._api_key = api_key.strip()
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        key = self._api_key or resolve_api_key()
        if not key:
            raise PolishError(
                "未配置 API key",
                "在「⋯ 更多 → 🔑 AI 润色 API key」里填一下, "
                "或设好 ANTHROPIC_API_KEY 环境变量。",
            )
        try:
            import anthropic
        except ImportError:
            raise PolishError(
                "缺少 anthropic SDK",
                "安装: pip install anthropic",
            )
        self._client = anthropic.Anthropic(
            api_key=key,
            timeout=self.timeout,
        )
        return self._client

    # ------------------------------------------------------------------
    def polish(
        self,
        report_md: str,
        styles: Optional[list[str]] = None,
        kind: str = "日报",
        extra_notes: str = "",
    ) -> PolishResult:
        """一次性润色。"""
        if not report_md.strip():
            raise PolishError("报告内容为空", "先选中一个有数据的日期")

        styles = styles or ["concise"]
        prompt = build_user_prompt(report_md, styles, kind, extra_notes)
        client = self._get_client()

        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=self.MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            raise _wrap_error(e, self.model)

        text = "".join(
            blk.text for blk in resp.content
            if getattr(blk, "type", None) == "text"
        )
        return PolishResult(
            text=text.strip(),
            model=self.model,
            input_tokens=getattr(resp.usage, "input_tokens", 0),
            output_tokens=getattr(resp.usage, "output_tokens", 0),
            stop_reason=getattr(resp, "stop_reason", ""),
        )

    # ------------------------------------------------------------------
    def polish_stream(
        self,
        report_md: str,
        styles: Optional[list[str]] = None,
        kind: str = "日报",
        extra_notes: str = "",
    ) -> Iterator[str]:
        """流式润色 — 逐段 yield 文本增量。"""
        if not report_md.strip():
            raise PolishError("报告内容为空", "先选中一个有数据的日期")

        styles = styles or ["concise"]
        prompt = build_user_prompt(report_md, styles, kind, extra_notes)
        client = self._get_client()

        try:
            with client.messages.stream(
                model=self.model,
                max_tokens=self.MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for chunk in stream.text_stream:
                    yield chunk
        except Exception as e:
            raise _wrap_error(e, self.model)

    # ------------------------------------------------------------------
    def check_connection(self) -> tuple[bool, str]:
        """测连通性 (发一个极小的请求)。返回 (ok, message)。"""
        try:
            client = self._get_client()
            resp = client.messages.create(
                model=self.model,
                max_tokens=16,
                messages=[{"role": "user", "content": "回复「ok」"}],
            )
            return True, "连接正常"
        except PolishError as e:
            return False, e.message
        except Exception as e:
            return False, str(e)[:200]


# ============================================================================
# 错误包装
# ============================================================================
def _wrap_error(e: Exception, model: str = "") -> PolishError:
    """把 SDK 异常转成带中文提示的 PolishError。"""
    msg = str(e)
    name = type(e).__name__

    if "RateLimit" in name or "429" in msg:
        return PolishError(
            "触发速率限制 (429)",
            "稍等几十秒再试, 或检查账户的用量限制。",
        )
    if "AuthenticationError" in name or "401" in msg:
        return PolishError(
            "API key 无效 (401)",
            "检查 key 是不是写错了/过期了 — "
            "可以在「⋯ 更多 → 🔑 AI 润色 API key」里重新填。",
        )
    if "PermissionDenied" in name or "403" in msg:
        return PolishError(
            "权限不足 (403)",
            "该模型可能需要更高的账户权限, 或 key 没有访问权。",
        )
    if "NotFoundError" in name or "404" in msg:
        return PolishError(
            f"模型不存在 (404)",
            f"模型 ID '{model or '(未知)'}' 可能已下线。"
            "试试 claude-sonnet-5 或 claude-haiku-4-5。",
        )
    if "Overloaded" in name or "529" in msg:
        return PolishError(
            "服务端繁忙 (529)",
            "Anthropic API 暂时过载, 稍后重试。",
        )
    if "timeout" in msg.lower() or "Timeout" in name:
        return PolishError(
            "请求超时",
            "报告可能太长, 试试只用 1 种风格, 或换更快的模型。",
        )
    if "max_tokens" in msg.lower():
        return PolishError(
            "输出超出 token 上限",
            "可以调大 MAX_TOKENS, 或缩短报告范围 / 减少风格数。",
        )
    return PolishError(f"API 调用失败: {msg[:200]}")


# ============================================================================
# 风格对比 (可选功能)
# ============================================================================
def polish_multiple_styles(
    report_md: str,
    styles: list[str],
    kind: str = "日报",
    model: str = MODEL_DEFAULT,
) -> dict[str, PolishResult | Exception]:
    """同一份报告用多种风格各润色一次, 方便用户挑。

    注意: 这是 N 次 API 调用, 成本 ×N。
    """
    out: dict[str, PolishResult | Exception] = {}
    for s in styles:
        if s not in STYLES:
            continue
        try:
            out[s] = ClaudePolishClient(model=model).polish(
                report_md, [s], kind,
            )
        except Exception as e:
            out[s] = e
    return out


__all__ = [
    "Style", "STYLES", "DEFAULT_STYLES",
    "PolishError", "PolishResult", "ClaudePolishClient",
    "has_api_key", "api_key_hint", "api_key_source",
    "load_api_key", "save_api_key", "clear_api_key",
    "resolve_api_key", "env_api_key",
    "build_user_prompt", "polish_multiple_styles",
    "MODEL_DEFAULT", "MODEL_FAST", "MODEL_SMART", "SYSTEM_PROMPT",
]