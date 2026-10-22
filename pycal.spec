# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — PyCal 打包配置。

模式由环境变量控制 (build.py 传入):
  PYCAL_ONEFILE=1   -> 单文件 exe (分发方便, 启动慢 ~2s)
  PYCAL_ONEFILE=0   -> 单目录 (启动快 ~0.3s, 便于调试)
  PYCAL_NO_ICON=1   -> 无图标 (图标生成失败时)

用法:
  python build.py                # onedir (默认)
  python build.py --onefile      # 单文件 exe
"""

import os
import sys
from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve() if "SPECPATH" in dir() else Path(".").resolve()
PROJECT = SPEC_DIR

ONEFILE = os.environ.get("PYCAL_ONEFILE", "0") == "1"
NO_ICON = os.environ.get("PYCAL_NO_ICON", "0") == "1"

block_cipher = None

# PySide6 排除用不到的重型模块 — 体积能从 ~450MB 压到 ~90MB
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
    "PySide6.QtSql", "PySide6.QtNetworkAuth",
    # 标准库用不到的重型模块
    "tkinter", "unittest", "pydoc_data",
    "distutils", "setuptools", "pip", "xmlrpc", "pydoc",
    "multiprocessing", "asyncio.test_utils", "curses", "distutils",
    # 桌面日历不需要的科学计算 (CalDAV 链依赖少)
    "numpy", "pandas", "matplotlib", "scipy", "PIL",
    "IPython", "jupyter", "notebook", "sympy",
]

# 动态导入的模块 (PyInstaller 静态分析抓不到的)
HIDDEN_IMPORTS = [
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    "PySide6.QtPlatforms",              # Qt 平台插件 — 打包必需

    # UI (运行时才 import)
    "pycal.ui.main_window", "pycal.ui.month_view", "pycal.ui.editor",
    "pycal.ui.search_dialog", "pycal.ui.sync_dialog", "pycal.ui.report_dialog",
    "pycal.ui.color_picker", "pycal.ui.floating_window", "pycal.ui.skin",

    # 功能模块
    "pycal.notes", "pycal.notifier", "pycal.report", "pycal.lunar",
    "pycal.desktop.wallpaper", "pycal.desktop.autostart", "pycal.installer",
    "pycal.sync", "pycal.sync.__main__", "pycal.report.__main__",

    # CalDAV / iCal
    "caldav", "icalendar", "recurring_ical_events",
    "recurring_ical_events.of", "x_wr_timezone", "wassima",
    "caldav.elements", "caldav.objects",

    # MCP
    "mcp", "mcp.server.fastmcp", "mcp.server.stdio",

    # 农历
    "lunardate",
]

datas = []

# 图标
ICON = PROJECT / "assets" / "pycal.ico"
if not ICON.exists() and not NO_ICON:
    try:
        sys.path.insert(0, str(PROJECT))
        from pycal.installer import make_icon
        ICON.parent.mkdir(parents=True, exist_ok=True)
        make_icon(str(ICON))
    except Exception:
        pass
ICON_ARG = str(ICON) if (ICON.exists() and not NO_ICON) else None

a = Analysis(
    [str(PROJECT / "run.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=datas,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,        # onefile 时直接把二进制塞进 EXE
    name="PyCal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_ARG,
)

if ONEFILE:
    # 单文件: 二进制 + 数据全塞进同一个 EXE
    exe.binaries = a.binaries
    exe.datas = a.datas
else:
    # 单目录: EXE 只放启动器, 依赖放 _internal/
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="PyCal",
    )