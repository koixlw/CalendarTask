# DesktopCal 搜索机制 — 证据复盘

> 引用的两个文件位于 DesktopCal 安装目录的 `app/dkappcal/resource.zip` 内部，
> 即 `config/search.lua` 和 `config/history_search.lua`。压缩包外的其他 dkappcal 资源（frame.xml / subs_func.lua 等）也参与渲染，
> 但搜索逻辑完全集中在这两个 Lua 文件中。

---

## 1. 搜索架构全景

```
                ┌────────────────────────────────────┐
                │  EditControl 输入 (回顾栏 onreturn) │
                │  onreturn_search() — search.lua:1  │
                └────────────────┬───────────────────┘
                                 │
                search.searchDesktopCal == true ?
                       ┌─────────┴──────────┐
                       ▼                    ▼
        framework.window.open2(...)    ClickAndOpenExplorer(url)
        'frame.xml|HistorySearch'      (defaultSearchEngine URL 编码)
                       │
                       ▼
                HistorySearch 窗口加载
                       │
                       ▼
        onload_loading_history_search()
        — 初始化 4 个下拉、颜色按钮、自定义日期
                       │
                       ▼ (auto-call if keyword present)
                click_search() — history_search.lua:899
                       │
        ┌──────────────┴───────────────────────────────┐
        ▼                                               ▼
  ① 原生候选 (C++)                          ② Lua 富匹配
  historysearch.Search(text)                 for each 候选:
  → item_table / event_table                     ├─ 时间范围 (is_data_in_range)
  → SQLite FTS-like 查询                          ├─ 类型 / 完成度 (type & has_completed)
  → 返回命中集                                    ├─ 颜色 (<font color="#…"> hex 匹配)
                                                  └─ 关键字 (MarkdownParse 后的可见正文)
                                                 四维 AND，逐行 / 逐段
```

**为什么双层**：原生层负责快速命中候选（C++ 跑 SQL），Lua 层负责 SQL 表达不了的富语义
（如"同一行同时含未完成 + 关键字 + 红色字"）。

---

## 2. 关键代码片段 + 行号引用

| # | 引用 | 作用 |
|---|---|---|
| A | `search.lua:5-12` | `if search.searchDesktopCal` 切到站内搜索，传 `searchkey` |
| B | `search.lua:14-19` | 切到 Web 搜索：`UrlEncode(engine, text)` 后 `ClickAndOpenExplorer` |
| C | `search.lua:36-43` | "onclick_searchdesktopcal" 强制走站内搜（HotSearch 按钮旁边那个） |
| D | `history_search.lua:899-912` | `click_search` 入参；`historysearch.Search(text)` 取候选 |
| E | `history_search.lua:932-941` | 候选 → 时间范围 → 多维过滤 → 结果容器 |
| F | `history_search.lua:444-515` | `is_data_match_filters` 四维联合判断的核心 |
| G | `history_search.lua:335-360` | `grid_line_matches` —— 同一行完成度+颜色+关键字 |
| H | `history_search.lua:117-140` | 22 色调色板常量 `search_text_color_map` |
| I | `history_search.lua:73-80` | `normalize_keyword` —— trim 前后空白 |
| J | `history_search.lua:84-111` | `isContentMatchKeyword` —— MarkdownParse 后**纯文本**匹配 |
| K | `history_search.lua:277-332` | `content_match_color_and_keyword` —— 防色值数字误命中 |
| L | `history_search.lua:517-522` | `try_auto_search` —— 任意筛选变化即时重搜 |
| M | `history_search.lua:771-896` | 时间范围处理（含自定义区间） |
| N | `history_search.lua:1145-1193` | 双击结果：type=0 跳到日历格子，其他跳到事件编辑器 |

---

## 3. 重点机制 — 防"色值数字误命中"

> 原文注释 `history_search.lua:277-279`:
> *"颜色筛选：关键字非空时，关键字必须落在该颜色的 <font> 可见正文内（不能只靠色值里的数字）"*

示例：搜索关键字 = `24`，候选内容 = `买<font color="#F12424">生日蛋糕</font>`

朴素实现：
```python
"#F12424".find("24") != -1  # 命中 — 但其实是误命中！
```

DesktopCal 的做法（三道防线）：

1. **先做可见正文匹配** (J) —— `MarkdownParse` 取出 mkText，再 `string.find(lower(text), lower(kw), plain=true)`；
2. **再做颜色匹配** (K) —— hex 串准备两份：RGB 和 BGR，带 `#` 和不带 `#`；
3. **联动** —— 当且仅当「可见正文命中关键字」**AND**「`<font color=#XXX>` 中至少一个匹配 hex」才返回 true。

---

## 4. UX 即时筛选

`history_search.lua:517-558` 暴露给所有下拉框 / 颜色按钮 / 日期选择的回调都调用
`try_auto_search()`，只要任一筛选条件就 `click_search()`。零延迟的 live search。

---

## 5. 我的实现（`pycal/search/primitives.py`）

照双层架构 1:1 复刻：

| DesktopCal 原件 | 我的复刻 |
|---|---|
| `historysearch.Search` 原生引擎 | SQLite FTS5 虚表 `items_fts` 上的 `MATCH` 查询 |
| `is_data_in_range` | `SearchEngine.filter_by_range` |
| `is_data_match_filters` | `SearchEngine.is_data_match_filters` |
| `content_match_color_and_keyword` | `ColorMatcher.match_color_and_keyword` |
| `MarkdownParse` | `markdown_to_plain_text`（同义转换） |
| `<font color="#…">` 标签提取 | 正则 `<[Ff][Oo][Nn][Tt]\\s+([^>]*)>(.*?)</[Ff][Oo][Nn][Tt]>` |
| `try_auto_search` | `SearchEngine.search(...)` 自身就是无状态调用 |
| `search.searchDesktopCal` 双模式入口 | `SearchDispatcher` 类：分支 `search_in_app` vs `search_web` |

可独立运行：`python demo_search.py`
