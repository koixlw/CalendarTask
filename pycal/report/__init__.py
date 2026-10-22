"""日报 / 周报生成器。

数据来源: calendar_items (+ repeat_rules / countdowns / reminders)

日报 (daily):
  - 日期 + 农历 + 星期
  - 今日条目按类型分组 (格子/重复/提醒/倒数日)
  - 完成度统计 (总行数 / 已完成 / 未完成 / 完成率)
  - 逐条清单, [+] 已完成打勾
  - 高亮重点 (priority >= 2) 和逾期项

周报 (weekly):
  - 周范围 (周一~周日, 可切换 ISO 周编号)
  - 7 天每日完成情况矩阵
  - 本周新增 / 完成 / 未完成统计
  - 类型分布 + 完成率趋势
  - 下周预告 (含倒数日临近提醒)
  - Top 关键词 (从标题/内容提取)

导出: Markdown / HTML (带内联 CSS, 可打印成 PDF)
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from pycal.db.connection import open_default
from pycal.lunar import lunar_for, is_24jieqi


# ============================================================================
# 数据结构
# ============================================================================
@dataclass
class ReportLine:
    text: str
    completed: bool
    priority: int = 0


@dataclass
class ItemBucket:
    """一天的条目聚合。"""
    date_iso: str
    type_id: int
    type_label: str
    title: str = ""
    lines: list[ReportLine] = field(default_factory=list)
    is_completed: bool = False
    countdown_target: Optional[str] = None

    @property
    def total(self) -> int:
        return len(self.lines) if self.lines else (1 if self.title else 0)

    @property
    def done(self) -> int:
        if self.lines:
            return sum(1 for x in self.lines if x.completed)
        return 1 if self.is_completed else 0


@dataclass
class DayStats:
    date_iso: str
    buckets: list[ItemBucket] = field(default_factory=list)

    @property
    def total_lines(self) -> int:
        return sum(b.total for b in self.buckets)

    @property
    def done_lines(self) -> int:
        return sum(b.done for b in self.buckets)

    @property
    def pending_lines(self) -> int:
        return self.total_lines - self.done_lines

    @property
    def rate(self) -> float:
        return (self.done_lines / self.total_lines * 100) if self.total_lines else 0.0

    @property
    def has_items(self) -> bool:
        return self.total_lines > 0


# ============================================================================
# 查询
# ============================================================================
_LINE_RE = re.compile(r"^\s*\[\s*\+\s*\]\s*(.*)$")


def _parse_content(content: str) -> list[ReportLine]:
    out: list[ReportLine] = []
    for raw in (content or "").splitlines():
        if not raw.strip():
            continue
        m = _LINE_RE.match(raw)
        if m:
            out.append(ReportLine(text=m.group(1).strip(), completed=True))
        else:
            out.append(ReportLine(text=raw.strip(), completed=False))
    return out


def _strip_html_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")


_TYPE_LABEL = {0: "格子", 1: "重复", 2: "提醒", 3: "倒数日"}


def _load_day(day_iso: str, con: sqlite3.Connection) -> DayStats:
    rows = con.execute(
        "SELECT * FROM calendar_items WHERE date = ? ORDER BY type, priority DESC, item_id",
        (day_iso,),
    ).fetchall()
    stats = DayStats(date_iso=day_iso)
    for r in rows:
        t = r["type"] or 0
        bucket = ItemBucket(
            date_iso=day_iso,
            type_id=t,
            type_label=_TYPE_LABEL.get(t, "其他"),
            title=_strip_html_tags(r["title"] or ""),
            lines=_parse_content(r["content"]),
            is_completed=bool(r["is_completed"]),
        )
        # 倒数日补目标日期
        if t == 3:
            cd = con.execute(
                "SELECT target_date FROM countdowns WHERE item_id = ?",
                (r["item_id"],),
            ).fetchone()
            if cd:
                bucket.countdown_target = cd["target_date"]
        stats.buckets.append(bucket)
    return stats


def load_day(day: date, con: Optional[sqlite3.Connection] = None) -> DayStats:
    c = con or open_default()
    return _load_day(day.isoformat(), c)


def load_week(
    any_day: date, con: Optional[sqlite3.Connection] = None,
    week_starts_monday: bool = True,
) -> list[DayStats]:
    """返回该周 7 天的 DayStats (周一~周日, 或周日起算)。"""
    c = con or open_default()
    if week_starts_monday:
        offset = any_day.weekday()          # Mon=0
        start = any_day - timedelta(days=offset)
    else:
        start = any_day - timedelta(days=(any_day.weekday() + 1) % 7)
    return [
        _load_day((start + timedelta(days=i)).isoformat(), c)
        for i in range(7)
    ]


def week_bounds(any_day: date, week_starts_monday: bool = True) -> tuple[date, date]:
    if week_starts_monday:
        start = any_day - timedelta(days=any_day.weekday())
    else:
        start = any_day - timedelta(days=(any_day.weekday() + 1) % 7)
    return start, start + timedelta(days=6)


# ============================================================================
# 倒数日 / 逾期
# ============================================================================
def upcoming_countdowns(
    within_days: int = 30, con: Optional[sqlite3.Connection] = None,
) -> list[tuple[str, str, int]]:
    """返回 [(title, target_date, days_left), ...] 按 days_left 升序。"""
    c = con or open_default()
    today = date.today()
    rows = c.execute(
        """SELECT i.item_id, i.title, i.content, cd.target_date, cd.repeat_yearly
           FROM countdowns cd JOIN calendar_items i ON i.item_id = cd.item_id""",
    ).fetchall()
    out = []
    for r in rows:
        tgt = r["target_date"]
        try:
            td = date.fromisoformat(tgt)
        except (ValueError, AttributeError):
            continue
        if r["repeat_yearly"]:
            td = td.replace(year=today.year)
            if td < today:
                td = td.replace(year=today.year + 1)
        days_left = (td - today).days
        if 0 <= days_left <= within_days:
            title = _strip_html_tags(r["title"] or "") or \
                _strip_html_tags(r["content"] or "").splitlines()[0]
            out.append((title, tgt, days_left))
    return sorted(out, key=lambda x: x[2])


# ============================================================================
# 关键词提取
# ============================================================================
_STOPWORDS = {
    "的", "了", "和", "与", "或", "在", "是", "有", "为", "对", "我", "你",
    "他", "她", "它", "们", "这", "那", "就", "也", "都", "还", "要", "会",
    "一个", "一些", "什么", "怎么", "可以", "需要", "准备", "完成", "会议",
    "the", "and", "for", "with", "from", "that", "this", "have", "will",
}


def extract_keywords(days: list[DayStats], top_n: int = 8) -> list[tuple[str, int]]:
    """从标题/内容提取高频词 (2-6 字的连续中文串 或 英文单词)。"""
    counter: Counter[str] = Counter()
    for day in days:
        for b in day.buckets:
            texts = [b.title] + [l.text for l in b.lines]
            for t in texts:
                t = _strip_html_tags(t)
                # 英文单词
                for w in re.findall(r"[A-Za-z][A-Za-z0-9\-_]{2,}", t):
                    wl = w.lower()
                    if wl not in _STOPWORDS:
                        counter[wl] += 1
                # 中文连续段 (2-6 字)
                for seg in re.findall(r"[一-鿿]{2,6}", t):
                    for n in (4, 3, 2):
                        for i in range(len(seg) - n + 1):
                            frag = seg[i:i + n]
                            if frag not in _STOPWORDS:
                                counter[frag] += 1
    # 去掉被更长词完全包含的短词 (减少噪声)
    words = set(counter.keys())
    result = [
        (w, c) for w, c in counter.most_common(top_n * 4)
        if not any(w != o and w in o for o in words if len(o) > len(w))
    ]
    return result[:top_n]


# ============================================================================
# Markdown 渲染
# ============================================================================
_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _fmt_lunar(d: date) -> str:
    t = lunar_for(d)
    if t:
        prefix = "⚡ " if is_24jieqi(d) else ""
        return f"（{prefix}{t}）"
    return ""


def render_daily_markdown(day: date, con: Optional[sqlite3.Connection] = None) -> str:
    c = con or open_default()
    stats = load_day(day, c)
    wd = _WEEKDAY_CN[day.weekday()]
    out: list[str] = []

    out.append(f"# 📋 日报 · {day.isoformat()} {_fmt_lunar(day)} {wd}")
    out.append("")
    out.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    out.append("")

    # 概览
    out.append("## 📊 概览")
    out.append("")
    if not stats.has_items:
        out.append("今日暂无日程安排。")
        out.append("")
    else:
        out.append("| 指标 | 数值 |")
        out.append("|---|---|")
        out.append(f"| 条目数 | {len(stats.buckets)} |")
        out.append(f"| 总行数 | {stats.total_lines} |")
        out.append(f"| 已完成 | {stats.done_lines} |")
        out.append(f"| 未完成 | {stats.pending_lines} |")
        rate_bar = "█" * int(stats.rate / 10) + "░" * (10 - int(stats.rate / 10))
        out.append(f"| 完成率 | {stats.rate:.0f}% `{rate_bar}` |")
        out.append("")

        # 按类型分组
        grouped: dict[str, list[ItemBucket]] = defaultdict(list)
        for b in stats.buckets:
            grouped[b.type_label].append(b)
        for type_label in ("格子", "重复", "提醒", "倒数日"):
            if type_label not in grouped:
                continue
            out.append(f"### {type_label} ({len(grouped[type_label])})")
            out.append("")
            for b in grouped[type_label]:
                if b.type_id == 3 and b.countdown_target:
                    try:
                        left = (date.fromisoformat(b.countdown_target) - day).days
                        extra = f" — 还有 **{left}** 天" if left >= 0 else \
                                f" — 已过 **{-left}** 天"
                    except ValueError:
                        extra = ""
                else:
                    extra = ""
                head = b.title or b.type_label
                out.append(f"#### {head}{extra}")
                out.append("")
                for ln in b.lines:
                    mark = "x" if ln.completed else " "
                    # GFM task list
                    out.append(f"- [{mark}] {ln.text}")
                if not b.lines and b.is_completed:
                    out.append("- [x] (已完成)")
                elif not b.lines:
                    out.append("- [ ] (未完成)")
                out.append("")

    # 倒数日预告
    cds = upcoming_countdowns(30, c)
    if cds:
        out.append("## ⏳ 30 天内倒数日")
        out.append("")
        for title, tgt, left in cds:
            out.append(f"- **{title}** — {tgt} — 还有 {left} 天")
        out.append("")

    # 页脚
    out.append("---")
    out.append("")
    out.append("*由 PyCal 自动生成*")
    return "\n".join(out)


def render_weekly_markdown(
    any_day: date, con: Optional[sqlite3.Connection] = None,
    week_starts_monday: bool = True,
) -> str:
    c = con or open_default()
    start, end = week_bounds(any_day, week_starts_monday)
    days = load_week(any_day, c, week_starts_monday)

    # ISO 周编号
    iso_year, iso_week, _ = start.isocalendar()
    out: list[str] = []

    out.append(f"# 📈 周报 · {start.isoformat()} ~ {end.isoformat()}")
    out.append("")
    out.append(f"> {iso_year} 年第 {iso_week} 周 | "
               f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    out.append("")

    # 总览
    total = sum(d.total_lines for d in days)
    done = sum(d.done_lines for d in days)
    pending = total - done
    rate = (done / total * 100) if total else 0.0
    active_days = sum(1 for d in days if d.has_items)

    out.append("## 📊 本周总览")
    out.append("")
    out.append("| 指标 | 数值 |")
    out.append("|---|---|")
    out.append(f"| 有安排天数 | {active_days} / 7 |")
    out.append(f"| 总行数 | {total} |")
    out.append(f"| 已完成 | {done} |")
    out.append(f"| 未完成 | {pending} |")
    rate_bar = "█" * int(rate / 10) + "░" * (10 - int(rate / 10))
    out.append(f"| 完成率 | {rate:.0f}% `{rate_bar}` |")
    out.append("")

    # 每日矩阵
    out.append("## 📅 每日完成情况")
    out.append("")
    out.append("| 日期 | 星期 | 总数 | 完成 | 未完成 | 完成率 | 状态 |")
    out.append("|---|---|---|---|---|---|---|")
    for d in days:
        dd = date.fromisoformat(d.date_iso)
        wd = _WEEKDAY_CN[dd.weekday()]
        r = d.rate
        bar = "█" * int(r / 20) + "·" * (5 - int(r / 20))
        if not d.has_items:
            status = "—"
        elif r >= 80:
            status = "🟢 优秀"
        elif r >= 50:
            status = "🟡 一般"
        else:
            status = "🔴 待改进"
        lunar = lunar_for(dd) or ""
        out.append(
            f"| {dd.isoformat()}{' ' + lunar if lunar else ''} | {wd} | "
            f"{d.total_lines} | {d.done_lines} | {d.pending_lines} | "
            f"{r:.0f}% `{bar}` | {status} |"
        )
    out.append("")

    # 类型分布
    type_counter: Counter[str] = Counter()
    for d in days:
        for b in d.buckets:
            type_counter[b.type_label] += b.total
    if type_counter:
        out.append("## 🗂 类型分布")
        out.append("")
        out.append("| 类型 | 行数 | 占比 |")
        out.append("|---|---|---|")
        for label, n in type_counter.most_common():
            pct = n / total * 100 if total else 0
            out.append(f"| {label} | {n} | {pct:.0f}% |")
        out.append("")

    # 关键词
    kws = extract_keywords(days, top_n=8)
    if kws:
        out.append("## 🔑 高频关键词")
        out.append("")
        max_n = kws[0][1] or 1
        for word, n in kws:
            bar = "▇" * max(1, int(n / max_n * 10))
            out.append(f"- `{word}` {bar} ({n})")
        out.append("")

    # 逐日明细
    out.append("## 📝 逐日明细")
    out.append("")
    for d in days:
        dd = date.fromisoformat(d.date_iso)
        wd = _WEEKDAY_CN[dd.weekday()]
        lunar = lunar_for(dd) or ""
        if not d.has_items:
            out.append(f"### {dd.isoformat()} {wd} {lunar}")
            out.append("")
            out.append("_无安排_")
            out.append("")
            continue
        out.append(f"### {dd.isoformat()} {wd} {lunar} — "
                   f"{d.done_lines}/{d.total_lines} 完成")
        out.append("")
        for b in d.buckets:
            head = b.title or b.type_label
            out.append(f"**{head}** ({b.type_label})")
            out.append("")
            for ln in b.lines:
                mark = "x" if ln.completed else " "
                out.append(f"- [{mark}] {ln.text}")
            if not b.lines:
                out.append(f"- [{'x' if b.is_completed else ' '}] "
                           f"({'已完成' if b.is_completed else '未完成'})")
            out.append("")

    # 下周预告
    next_start = end + timedelta(days=1)
    next_days = load_week(next_start, c, week_starts_monday)
    next_total = sum(x.total_lines for x in next_days)
    if next_total:
        out.append("## 🔮 下周预告")
        out.append("")
        out.append(f"下周一: {next_start.isoformat()}")
        out.append("")
        for x in next_days:
            if not x.has_items:
                continue
            dd = date.fromisoformat(x.date_iso)
            out.append(f"- **{dd.isoformat()} {_WEEKDAY_CN[dd.weekday()]}** — "
                       f"{x.total_lines} 项")
            for b in x.buckets[:3]:
                head = b.title or b.type_label
                out.append(f"    - {head}")
        out.append("")

    # 倒数日
    cds = upcoming_countdowns(30, c)
    if cds:
        out.append("## ⏳ 30 天内倒数日")
        out.append("")
        for title, tgt, left in cds:
            out.append(f"- *lt;{title}&gt; — {tgt} — 还有 {left} 天".replace("&lt;", "**").replace("&gt;", "**"))
        out.append("")

    out.append("---")
    out.append("")
    out.append("*由 PyCal 自动生成*")
    return "\n".join(out)


# ============================================================================
# HTML 导出 (可打印成 PDF)
# ============================================================================
_HTML_CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  max-width: 860px; margin: 0 auto; padding: 32px 24px;
  color: #2c3e50; line-height: 1.7; background: #fff;
}
h1 { font-size: 24px; border-bottom: 3px solid #3498db; padding-bottom: 12px; }
h2 { font-size: 19px; margin-top: 32px; color: #2980b9; }
h3 { font-size: 16px; margin-top: 22px; }
h4 { font-size: 14px; margin: 14px 0 6px; color: #555; }
blockquote {
  margin: 12px 0; padding: 8px 14px;
  background: #f8f9fa; border-left: 4px solid #adb5bd; color: #6c757d;
}
table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 13px; }
th, td { border: 1px solid #dee2e6; padding: 7px 10px; text-align: left; }
th { background: #f1f3f5; font-weight: 600; }
tr:nth-child(even) td { background: #fafbfc; }
code {
  background: #f1f3f5; padding: 2px 6px; border-radius: 3px;
  font-family: Consolas, monospace; font-size: 12px;
}
ul.task-list { list-style: none; padding-left: 4px; }
ul.task-list li { padding: 3px 0; }
ul.task-list input { margin-right: 8px; }
hr { border: none; border-top: 1px solid #dee2e6; margin: 28px 0; }
@media print {
  body { padding: 0; }
  h2 { page-break-after: avoid; }
  tr { page-break-inside: avoid; }
}
"""


def _md_to_html(md: str) -> str:
    """极简 Markdown -> HTML (覆盖本模块用到的语法子集)。"""
    import html as _html
    lines = md.split("\n")
    out: list[str] = []
    in_list = False
    in_table = False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    def close_table():
        nonlocal in_table
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    for ln in lines:
        stripped = ln.strip()

        # 表格
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue    # 分隔行
            if not in_table:
                close_list()
                out.append("<table><thead><tr>")
                out.append(
                    "".join(f"<th>{_inline(c)}</th>" for c in cells)
                )
                out.append("</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join(
                    f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        else:
            close_table()

        # 任务列表
        m_task = re.match(r"^-\s+\[([ xX])\]\s+(.*)$", stripped)
        if m_task:
            if not in_list:
                close_table()
                out.append('<ul class="task-list">')
                in_list = True
            checked = " checked" if m_task.group(1).lower() == "x" else ""
            out.append(
                f'<li><input type="checkbox" disabled{checked}>'
                f"{_inline(m_task.group(2))}</li>"
            )
            continue
        else:
            close_list()

        # 普通列表
        m_ul = re.match(r"^[-*]\s+(.*)$", stripped)
        if m_ul:
            if not in_list:
                close_table()
                out.append('<ul class="task-list">')
                in_list = True
            out.append(f"<li>{_inline(m_ul.group(1))}</li>")
            continue

        # 标题
        m_h = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m_h:
            close_list(); close_table()
            lvl = len(m_h.group(1))
            out.append(f"<h{lvl}>{_inline(m_h.group(2))}</h{lvl}>")
            continue

        # 引用
        if stripped.startswith(">"):
            close_list(); close_table()
            out.append(f"<blockquote>{_inline(stripped[1:].strip())}</blockquote>")
            continue

        # 分隔线
        if stripped in ("---", "***", "___"):
            close_list(); close_table()
            out.append("<hr>")
            continue

        # 空行
        if not stripped:
            continue

        # 段落
        out.append(f"<p>{_inline(stripped)}</p>")

    close_list(); close_table()
    return "\n".join(out)


def _inline(s: str) -> str:
    """行内 Markdown: **粗体** / `代码` / *斜体* / <font> 颜色。"""
    import html as _html
    s = _html.escape(s)
    # 保留 PyCal 的 font 颜色标签 (先转成 span, 在 escape 前做更安全 — 这里简化)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    return s


def render_html(markdown: str, title: str = "PyCal Report") -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{_HTML_CSS}</style>
</head>
<body>
{_md_to_html(markdown)}
</body>
</html>"""


__all__ = [
    "ReportLine", "ItemBucket", "DayStats",
    "load_day", "load_week", "week_bounds", "upcoming_countdowns",
    "extract_keywords",
    "render_daily_markdown", "render_weekly_markdown",
    "render_html",
]