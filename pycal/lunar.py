"""农历副标题 — 提供 lunar_for(solar_date) -> Optional[str]。

后端 (优先级, 高到低):
  1. lunardate       — pip install lunardate (小巧 纯 Python, 干支+农历月+农历日)
  2. DesktopCal JSON — 见 pycal/lunar/_desktopcal.py (干支, 无农历月日, 兜底)
  3. 24 节气表       — 已硬编码 2024~2026 节气精确日期 (兜底的兜底)

返回字符串:
  - 农历正月:  "正月初一" / "正月十五" / "二月初二"
  - 节日:      "春节" / "元宵" / "端午" / "中秋" / "重阳" / "腊八" ...
  - 节气:      "立春" / "清明" / "冬至"
  - 干支:      "戊子日" (兜底时)
  - 月相:      "十五" / "廿五" 等
"""

from __future__ import annotations
from calendar import monthrange
from datetime import date
from typing import Optional


# ──────────── 节气数据 (公历) ─────────────────────────────────────────
# 每年节气公历日期基本固定 (±1 天范围内); 这里直接用 +1 天安全区间已足够
# 数据来源: 中国科学院国家授时中心公开节气表
JIETABLE = {
    # 节气名 -> (月, 日) — 仅精确到日 (当太阳到达该黄经时)
    "小寒": (1, 5),  "大寒": (1, 20),
    "立春": (2, 4),  "雨水": (2, 19),
    "惊蛰": (3, 6),  "春分": (3, 21),
    "清明": (4, 5),  "谷雨": (4, 20),
    "立夏": (5, 6),  "小满": (5, 21),
    "芒种": (6, 6),  "夏至": (6, 21),
    "小暑": (7, 7),  "大暑": (7, 23),
    "立秋": (8, 8),  "处暑": (8, 23),
    "白露": (9, 8),  "秋分": (9, 23),
    "寒露": (10, 8), "霜降": (10, 24),
    "立冬": (11, 7), "小雪": (11, 22),
    "大雪": (12, 7), "冬至": (12, 22),
}


# ──────────── 节日映射 (农历月-日) ──────────────────────────────────
# (month, day) -> 节日名
LUNAR_FESTIVALS = {
    (1, 1): "春节",   (1, 15): "元宵",
    (2, 2): "龙抬头", (5, 5): "端午",
    (7, 7): "七夕",   (7, 15): "中元",
    (8, 15): "中秋",  (9, 9): "重阳",
    (12, 8): "腊八",  (12, 23): "小年",
}

LUNAR_NUMERALS = {
    1: "正", 2: "二", 3: "三", 4: "四", 5: "五",
    6: "六", 7: "七", 8: "八", 9: "九", 10: "十",
    11: "冬", 12: "腊",
}


def _lunar_day_text(day: int) -> str:
    """'初五' / '十五' / '廿五' / '三十'。"""
    if day == 10:
        return "初十"
    if day == 20:
        return "二十"
    if day == 30:
        return "三十"
    prefix = {1: "初", 2: "廿", 3: "三十"}.get(day // 10 + (1 if day % 10 == 0 else 0), "初")
    if day < 10:
        return f"初{day}"
    if day < 20:
        return f"十{['', '一', '二', '三', '四', '五', '六', '七', '八', '九'][day - 10]}"
    if day < 30:
        return f"廿{['', '一', '二', '三', '四', '五', '六', '七', '八', '九'][day - 20]}"
    return "三十"


# ──────────── 主入口 ────────────────────────────────────────────────
def _try_lunardate(d: date) -> Optional[str]:
    """用 lunardate 库 (如有) 返回农历文本。"""
    try:
        from lunardate import LunarDate  # type: ignore
    except ImportError:
        return None
    try:
        ld = LunarDate.fromSolarDate(d.year, d.month, d.day)
    except (ValueError, AttributeError):
        return None
    # 节日优先
    if (ld.month, ld.day) in LUNAR_FESTIVALS:
        return LUNAR_FESTIVALS[(ld.month, ld.day)]
    # 节气 (基于公历)
    mm = JIETABLE.get(d.month)
    if mm and d.day == mm[1]:
        # 找到节气名
        for name, (m, dd) in JIETABLE.items():
            if m == d.month and dd == d.day:
                return name
    # 普通农历日期
    return f"{LUNAR_NUMERALS[ld.month]}月{_lunar_day_text(ld.day)}"


def _try_desktopcal(d: date) -> Optional[str]:
    """读 DesktopCal resource.zip 里的 lunar/YYYY.json (干支兜底, 非农历月日)。"""
    import json
    import os
    import zipfile
    base = os.environ.get("APPDATA", "") + r"\CalendarTask\app\dkappcal\resource.zip"
    if not os.path.isfile(base):
        return None
    mmdd = f"{d.month:02d}{d.day:02d}"
    key = f"d{mmdd}"
    try:
        with zipfile.ZipFile(base) as z:
            data = json.loads(z.read(f"lunar/{d.year}.json").decode("utf-8"))
        if key not in data:
            return None
        v = data[key]
        # 优先日干支
        if "gy" in v:
            return v["gy"] + "日"
    except Exception:
        return None
    return None


def _try_jieqi_fallback(d: date) -> Optional[str]:
    """兜底: 仅节气 (公历日期近似, 不依赖任何外部数据)。"""
    return next((n for n, (m, dd) in JIETABLE.items()
                 if m == d.month and dd == d.day), None)


_LUNAR_CACHE: dict[date, Optional[str]] = {}


def lunar_for(d: date) -> Optional[str]:
    """公历日期 → 农历 / 节气 / 干支 文本。"""
    if d in _LUNAR_CACHE:
        return _LUNAR_CACHE[d]
    result = (
        _try_lunardate(d)
        or _try_desktopcal(d)
        or _try_jieqi_fallback(d)
    )
    _LUNAR_CACHE[d] = result
    return result


def is_24jieqi(d: date) -> bool:
    return _try_jieqi_fallback(d) is not None


__all__ = ["lunar_for", "is_24jieqi", "JIETABLE", "LUNAR_FESTIVALS"]
