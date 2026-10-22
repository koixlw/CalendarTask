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
│   │   ├── sync_dialog.py          ← 云同步账号管理 (Phase 2)
│   │   ├── floating_window.py      ← 可拖动 + 八向 resize
│   │   ├── skin.py                 ← 4 种皮肤 + 不透明度
│   │   └── color_picker.py         ← 22 色调色板 + 自定义 BGR
│   ├── desktop/
│   │   ├── wallpaper.py            ← 读取原壁纸 + 叠加月历 + SPI_SETDESKWALLPAPER
│   │   └── autostart.py            ← HKCU Run + WScript.Shell .lnk
│   ├── notifier/
│   │   └── __init__.py             ← QTimer 扫描 reminders + 接 TrayIcon
│   ├── notes/
│   │   └── __init__.py             ← 便签浮贴纸
│   ├── lunar.py                    ← lunardate + 节气 + 节日 (Phase 1B)
│   ├── report/
│   │   ├── __init__.py             ← 日报/周报聚合 + Markdown/HTML 渲染
│   │   └── __main__.py             ← CLI: --day/--week/--html/--auto-*
│   ├── installer.py                ← 自启动 / 快捷方式 / 图标生成 / 卸载
│   ├── sync/
│   │   ├── presets.py              ← Google/iCloud/Outlook/通用 endpoint
│   │   ├── accounts.py             ← 账号 CRUD + 凭据混淆 + sync_links
│   │   ├── ical_convert.py         ← CalendarItem <-> VEVENT 双向转换
│   │   ├── client.py               ← CaldavClient (连接/列举/拉取/推送/删除)
│   │   ├── engine.py               ← SyncEngine 三方对比 + 冲突解决
│   │   └── __main__.py             ← CLI: python -m pycal.sync
│   └── mcp/
│       ├── __init__.py             ← FastMCP server + 4 tools
│       └── __main__.py
│
├── tests/
│   ├── test_smoke.py               ← 基础 CRUD + 搜索
│   ├── test_sync.py                ← 46 项 (iCal round-trip / 冲突检测)
│   └── test_report.py              ← 36 项 (日报/周报/HTML)
│
├── assets/
│   └── pycal.ico                   ← 7 尺寸应用图标 (运行时生成)
│
├── pycal.spec                      ← PyInstaller 配置
├── build.py                        ← 一键打包脚本
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

# 3) 命令行搜索
python -m pycal search "周报"

# 4) 日报 / 周报
python -m pycal report                    # 今天日报 (stdout)
python -m pycal report --week             # 本周周报
python -m pycal report --day 2026-10-21 --html --out D:/reports
# 完整 CLI:
python -m pycal.report --yesterday --html --open
python -m pycal.report --last-week --out D:/reports
python -m pycal.report --auto-daily       # 常驻, 每天 18:00 生成
python -m pycal.report --auto-weekly      # 常驻, 每周五 17:00 生成

# 5) 云同步 CLI
python -m pycal sync list
python -m pycal.sync --list
python -m pycal.sync --add-google              # 交互式 (打印应用专用密码步骤)
python -m pycal.sync --test 1
python -m pycal.sync --sync-all --policy newest_wins

# 6) 安装 / 卸载
python -m pycal install                       # 状态查询
python -m pycal install autostart-on          # 开机自启动 (HKCU Run)
python -m pycal install autostart-off
python -m pycal install shortcut              # 桌面快捷方式
python -m pycal install uninstall             # 卸载 (保留数据)
# 完整 CLI:
python -m pycal.installer --status
python -m pycal.installer --autostart on
python -m pycal.installer --shortcut create
python -m pycal.installer --uninstall --purge   # 连数据一起删

# 7) 启动 MCP server (stdio)
python -m pycal.mcp

# 8) 打包成 exe
python build.py                # onedir → dist/PyCal/PyCal.exe (155MB)
python build.py --onefile      # 单文件 exe
python build.py --install      # 打包 + 自动装自启动/快捷方式
python build.py --icon-only    # 只生成 assets/pycal.ico
```

## 打包发布

```bash
pip install pyinstaller
python build.py            # → dist/PyCal/PyCal.exe + dist/PyCal/_internal/
python build.py --install  # 顺便写 HKCU Run 自启动 + 桌面快捷方式
```

| 项 | 说明 |
|---|---|
| 模式 | onedir (启动 ~0.3s) / `--onefile` 单文件 (启动 ~2s, 155MB) |
| 图标 | `assets/pycal.ico` — 7 尺寸 (16→256) PNG-compressed 32bpp，运行时用 PySide6 现生成 |
| 自启动 | HKCU `Run` 键（不需要管理员权限，不触发 UAC）|
| 卸载 | `python -m pycal install uninstall` 移除自启+快捷方式；加 `--purge` 连数据目录 |
| 体积优化 | `pycal.spec` 里 EXCLUDES 排掉 QtWebEngine/QtQuick/科学计算等，450MB → 155MB |

## 日报 / 周报

数据来自 `calendar_items`，自动按 `[+]` 完成标记统计行级完成率。

**日报**：日期+农历+星期 → 概览表（条目数/总行/完成/未完成/完成率进度条）→ 按类型分组（格子/重复/提醒/倒数日）→ 30 天内倒数日

**周报**：ISO 周编号 → 本周总览 → 每日完成矩阵（含完成率条 + 🟢/🟡/🔴 评级）→ 类型分布 → 高频关键词（TF 提取）→ 逐日明细 → 下周预告 → 倒数日

| 入口 | 位置 |
|---|---|
| GUI | 工具栏 📑 → 预览/复制/保存 MD/导出 HTML/打印 PDF |
| 自动 | 主窗每 30 分钟检查：日报每天 18:00 后生成一次、周报每周五 17:00 后生成一次 → `%APPDATA%\PyCal\reports\` |
| CLI | `python -m pycal report [--day D] [--week] [--html] [--out PATH] [--open]` |
| 常驻 | `python -m pycal.report --auto-daily` / `--auto-weekly` |

HTML 导出带内联 CSS + `@media print`，浏览器直接 Ctrl+P 就是 PDF。

**修了一个真 bug**：完成标记正则在三个文件里都写成了 `\[+\]`（`+` 被当成量词，只匹配 `[]`），DesktopCal 的 Lua 是 `%[%+%]` = 字面 `[+]`。已统一改成 `\s*\[\s*\+\s*\]\s*`。

## 云同步说明 (CalDAV)

| 项目 | 说明 |
|---|---|
| 协议 | CalDAV (RFC 4791) — Google / iCloud / Outlook / Nextcloud 都支持 |
| 认证 | **应用专用密码**（不是主账号密码）。各服务商的申请链接在 UI 和 CLI 里都会打印 |
| 冲突策略 | `newest_wins`（默认，按 LAST-MODIFIED 时间戳）/ `local_wins` / `remote_wins` |
| 只读模式 | 勾选后该账号只做 PULL，绝不修改云端 |
| 方向 | `both` / `pull`（只云→本地）/ `push`（只本地→云） |
| 自动同步 | 主窗启动 10 秒后首跑，之后每 15 分钟一次；无账号时静默跳过 |
| 凭据存储 | base64 + XOR 打乱落本地 DB。**这是防"随手打开 DB 看明文"，不是加密**。生产环境应换 Windows Credential Manager |
| 私有扩展 | `X-PYCAL-COLOR` / `X-PYCAL-TYPE` / `X-PYCAL-COUNTDOWN` / `X-PYCAL-PRIORITY`，其他客户端会忽略 |

**OAuth 尚未实现**：三家都需要 client_id/secret 申请流程，MVP 先走应用专用密码路线。

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
| **3: UX 细节** | **可拖动主窗 + 八向 resize + 4 种皮肤 + 月份切换 slide 动画 + 置顶切换 + 不透明度调节** | **✅** |
| **2: 云同步** | **CalDAV 双向同步 (Google / iCloud / Outlook / 通用) + 三方冲突解决 + 后台自动同步 + CLI + 管理 UI** | ✅ |
| **日报/周报** | **从 calendar_items 聚合生成 Markdown/HTML，含完成率、每日矩阵、类型分布、关键词、倒数日；GUI 预览 + CLI + 定时自动生成** | **✅ 本轮** |
| **4: 发布** | **PyInstaller 打包 (155MB onedir) + 多尺寸 .ico 图标 + HKCU Run 自启动 + 桌面快捷方式 + 卸载器** | **✅ 本轮** |

## 已知限制

1. **`pycal.mcp` 没注册到系统的 MCP client**，需要在 Claude Desktop / IDE 中手动配置。
2. **自动启动**未集成（HKCU Run 注册可加）。
3. **壁纸原图**：当 Windows 没设置壁纸时，`make_and_set_wallpaper()` 会落回纯日历面板 (`overlay=False`)。
