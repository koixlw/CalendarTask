"""字体缩放 — 主窗各处字号由一个比例统一算出来。

以前字号是散在各个控件里写死的 (条目行 8pt / 农历 7pt / 日期号 10pt …),
想调一处得翻遍 month_view 和 skin。这里收成一份: 给一个比例, 算出主窗需要的
全部字号 **以及配套的行高 / 格子最小尺寸** —— 只放大字号而不跟着长行高, 字
就会被裁掉, 所以这两件事必须绑在一起。

调字号的地方: 「⋯ 更多 → 字体大小」。
"""

from __future__ import annotations
from dataclasses import dataclass

# 菜单里给的档位 (比例, 文案)
SCALES: tuple[tuple[float, str], ...] = (
    (0.85, "小 — 85%"),
    (1.00, "标准 — 100%"),
    (1.15, "大 — 115%"),
    (1.30, "特大 — 130%"),
    (1.50, "巨大 — 150%"),
)

DEFAULT_SCALE = 1.0
MIN_SCALE, MAX_SCALE = 0.70, 1.80

# 一格里最多显示几条内容 (字号越大塞得越少 — 见 max_rows_for)
BASE_MAX_ROWS = 5


@dataclass(frozen=True)
class Fonts:
    """主窗所有字号 + 与之配套的固定尺寸 (单位: pt / px)。"""

    scale: float
    row: int          # 条目行文字 (基准 8pt)
    num: int          # 日期号 (基准 10pt)
    num_today: int    # 今天的日期号 (基准 11pt)
    sub: int          # 农历 / 节气 (基准 7pt)
    more: int         # "+N 更多" (基准 7pt)
    type_icon: int    # 类型图标 ↻/⏰/🎯 (基准 11pt)
    title: int        # 工具栏标题 (基准 12pt)
    month: int        # 月份标签 (基准 14pt)
    toolbar: int      # 工具栏按钮 (基准 9pt, 原来跟着系统默认)
    status: int       # 状态栏 (基准 9pt)
    # ── 配套尺寸 ──
    row_h: int        # 条目行高 (基准 15px)
    box_w: int        # ☐ 的宽度 (基准 13px)
    cell_min_w: int   # 格子最小宽 (基准 64px)
    cell_min_h: int   # 格子最小高 (基准 56px)
    bar_h: int        # 工具栏高度 (基准 36px)
    max_rows: int     # 一格最多显示几条内容
    # ── 年视图的小月 ──
    mini_title: int   # 小月标题 (基准 9pt)
    mini_head: int    # 小月的星期表头 (基准 7pt)
    mini_num: int     # 小月的日期号 (基准 8pt)
    mini_title_h: int # 小月标题行高 (基准 16px)
    mini_head_h: int  # 小月表头行高 (基准 12px)
    mini_h: int       # 整块小月的最小高 (基准 92px)


def clamp_scale(scale: float) -> float:
    """把比例夹到合理区间 — 存库被手改过也不至于把界面撑坏。"""
    return max(MIN_SCALE, min(MAX_SCALE, float(scale)))


def max_rows_for(scale: float) -> int:
    """字号越大, 一格里塞得下的条数越少 —— 至少留 2 条。"""
    return max(2, int(round(BASE_MAX_ROWS / clamp_scale(scale))))


def label_for_scale(scale: float) -> str:
    """给「不是档位值」的比例找一句人话 — 比如按住放大按钮连续调出来的 1.22。"""
    s = clamp_scale(scale)
    for value, text in SCALES:
        if abs(value - s) < 0.005:
            return text
    return f"自定义 — {int(round(s * 100))}%"


def _pt(base: float, scale: float, minimum: int = 6) -> int:
    """按比例缩一个基准字号, 并兜一个下限 (太小 Qt 会画糊)。"""
    return max(minimum, int(round(base * scale)))


def fonts_for(scale: float = DEFAULT_SCALE) -> Fonts:
    s = clamp_scale(scale)
    return Fonts(
        scale=s,
        row=_pt(8, s),
        num=_pt(10, s),
        num_today=_pt(11, s),
        sub=_pt(7, s),
        more=_pt(7, s),
        type_icon=_pt(11, s),
        title=_pt(12, s),
        month=_pt(14, s),
        toolbar=_pt(9, s),
        status=_pt(9, s),
        row_h=_pt(15, s, minimum=11),
        box_w=_pt(13, s, minimum=9),
        cell_min_w=_pt(64, s),
        cell_min_h=_pt(56, s),
        bar_h=_pt(36, s, minimum=24),
        max_rows=max_rows_for(s),
        mini_title=_pt(9, s),
        mini_head=_pt(7, s),
        mini_num=_pt(8, s),
        mini_title_h=_pt(16, s, minimum=11),
        mini_head_h=_pt(12, s, minimum=9),
        mini_h=_pt(104, s, minimum=60),
    )


def scale_from_setting(raw: str | None, default: float = DEFAULT_SCALE) -> float:
    """从 settings 表读出来的字符串还原成比例 — 读坏了就回默认值。"""
    try:
        return clamp_scale(float(raw))
    except (TypeError, ValueError):
        return default


__all__ = [
    "SCALES", "DEFAULT_SCALE", "MIN_SCALE", "MAX_SCALE", "BASE_MAX_ROWS",
    "Fonts", "clamp_scale", "max_rows_for", "label_for_scale",
    "fonts_for", "scale_from_setting",
]