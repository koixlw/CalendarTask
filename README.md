# PyCal — 桌面日历复刻

独立、开放的 DesktopCal 替代品。技术栈：**Python 3.11 + PySide6 + SQLite**。

跨平台计划：Win / macOS / Linux。Win 上额外提供「贴在桌面壁纸」能力
（生成带日历的 PNG，`SystemParametersInfoW` 设为壁纸）。

## Phase 0 地基已完成

- ✅ 独立 SQLite schema（与 DesktopCal 的 calendar.db **并存不冲突**）
- ✅ `pycal.importer` —— 从 DesktopCal `calendar.db` 自动识别 schema 列名后导入
- ✅ `pycal.search` —— 1:1 复刻 DesktopCal 双层搜索（SQLite FTS5 + Python 富匹配）
- ✅ `pycal.ui` —— 月视图 + 主窗口骨架（Frameless、OnBottom、透明）
- ✅ `pycal.desktop.wallpaper` —— 用 QPainter 渲染日历面板并设为壁纸
- ✅ `pycal.notifier` —— 30 秒扫描 reminders 表
- ✅ `pycal.notes` —— 桌面便签 (Frameless 浮贴纸)
- ✅ `pycal.mcp` —— MCP server 暴露搜索/日程给 AI 代理（使用已装好的 `mcp` 2.3 SDK）
- ✅ `tools/import_from_desktopcal.py` —— CLI 导入器
- ✅ `tests/test_smoke.py` —— 单元测试

## 目录结构

```
e:\todo\
├── pyproject.toml
├── README.md                       ← 你正在读
├── SEARCH_ANALYSIS.md              ← DesktopCal 搜索机制的证据拆解
├── desktopcal_search.py            ← 搜索原语 + 独立 FTS5 实现 (Phase 0 前的版本)
├── demo_search.py                  ← 9 个搜索场景的演示
├── run.py                          ← 一键启动
│
├── pycal/
│   ├── __init__.py
│   ├── __main__.py                 ← CLI: pycal {gui,import,search}
│   ├── db/
│   │   ├── schema.py               ← 新 schema (calendar_items + FTS5)
│   │   ├── connection.py           ← SQLite 连接 (WAL, thread-local)
│   │   ├── models.py               ← CalendarItem / Note / RepeatRule / ...
│   │   └── repository.py           ← CRUD
│   ├── importer/
│   │   └── __init__.py             ← 自动识别 DesktopCal schema 并导入
│   ├── search/
│   │   └── __init__.py             ← 复刻 DesktopCal 双层搜索 (基于新 schema)
│   ├── ui/
│   │   ├── main_window.py          ← Frameless OnBottom 主窗口 + 系统托盘
│   │   ├── month_view.py           ← 6×7 月视图 (农历/节气副标题)
│   │   ├── editor.py               ← 4 类型编辑器
│   │   ├── search_dialog.py        ← 5 下拉多维筛选面板 (Phase 1A)
│   │   └── color_picker.py         ← 22 色调色板 + 自定义 BGR
│   ├── desktop/
│   │   └── wallpaper.py            ← 读取原壁纸 + 叠加月历 + SPI_SETDESKWALLPAPER
│   ├── notifier/
│   │   └── __init__.py             ← QTimer 扫描 reminders + 接 TrayIcon
│   ├── notes/
│   │   └── __init__.py             ← 便签浮贴纸
│   ├── lunar.py                    ← lunardate + 节气 + 节日 (Phase 1B)
│   └── mcp/
│       ├── __init__.py             ← FastMCP server + 4 tools
│       └── __main__.py
│
├── tests/
│   └── test_smoke.py
│
└── tools/
    └── import_from_desktopcal.py   ← CLI 导入器
```

## 安装

```bash
pip install -e .
# 或直接装依赖:
pip install PySide6 mcp
```

## 命令

```bash
# 1) 启动 GUI
python -m pycal gui

# 2) 从 DesktopCal 导入数据到 PyCal
python -m pycal import
# 等价: python -m tools.import_from_desktopcal
# 自定义源/目标:
python -m tools.import_from_desktopcal --source "X:\path\calendar.db" --target "X:\path\pycal.db"

# 3) 命令行搜索 (PyCal 数据)
python -m pycal search "周报"

# 4) 启动 MCP server (stdio)
python -m pycal.mcp
# 客户端配置示例 (Claude Desktop):
#   "pycal": {
#     "command": "python",
#     "args": ["-m", "pycal.mcp"]
#   }

# 5) 一键启动脚本
python run.py
```

## 桌面集成（贴在桌面上）

`pycal.desktop.wallpaper.make_and_set_wallpaper()` 会：
1. 用 QPainter 在屏幕右下角渲染本月日历面板
2. 保存到 `%TEMP%/pycal_wallpaper_YYYYMM.png`
3. 调 Windows API `SystemParametersInfoW(SPI_SETDESKWALLPAPER)` 设为新壁纸

调试时可以只生成不设置：
```python
from pycal.desktop.wallpaper import render_calendar_panel
img = render_calendar_panel(2026, 10)
img.save("out.png")
```

## 数据兼容性

PyCal 与 DesktopCal 共存时：
- PyCal 默认 DB 在 `%APPDATA%/PyCal/db/pycal.db`
- DesktopCal DB 在 `%APPDATA%/CalendarTask/db/calendar.db`（只读，不要 PyCal 写它）

`pycal.importer` 通过 `PRAGMA table_info` 自动适配多种 schema
（旧版 `item_table`，新版 `item`，甚至自定义名都能识别），
只读取 `type != 4`（课程表）的条目导入到 PyCal。

## 后续 Phase

| Phase | 范围 | 状态 |
|---|---|---|
| 0: 地基 | schema + 导入 + 搜索 + UI 占位 + MCP + wallpaper + notifier + 笔记 | ✅ |
| 1: 编辑器 | 4 类型条目编辑器、22 色调色板、点击/双击/右键 UX、颜色条、类型图标 | ✅ |
| 1A: 搜索面板 | 5 下拉多维筛选面板（复刻 history_search.lua 全套）+ 即时重搜 + 双击跳转 | ✅ |
| 1B: 农历副标题 | lunardate 后端 + 24 节气 + 节日（春节/端午/中秋 等） | ✅ |
| 1C: 通知调度 | QSystemTrayIcon + 30s 轮询 + 左键切换显隐 + 右键菜单 + 提醒二次确认 | ✅ |
| 1D: 壁纸叠加 | 读 Windows 注册表的当前壁纸 + QPainter 叠月历 + 一键还原 `restore_original_wallpaper` | ✅ |
| **3: UX 细节** | **可拖动主窗 + 八向 resize + 4 种皮肤 + 月份切换 slide 动画 + 置顶切换 + 不透明度调节** | **✅ 本轮** |
| 2: 同步 | Google / Outlook / iCloud CalDAV | ⏳ |
| 4: 发布 | PyInstaller 打包、MSIX、Linux .deb / AppImage | ⏳ |

## 已知限制

1. **`pycal.mcp` 没注册到系统的 MCP client**，需要在 Claude Desktop / IDE 中手动配置。
2. **自动启动**未集成（HKCU Run 注册可加）。
3. **壁纸原图**：当 Windows 没设置壁纸时，`make_and_set_wallpaper()` 会落回纯日历面板 (`overlay=False`)。
