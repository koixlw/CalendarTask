#!/usr/bin/env python
"""PyCal 构建脚本 — 一条命令打包成 exe。

用法:
  python build.py                # onedir (默认, 启动快)
  python build.py --onefile      # 单文件 exe (分发方便, 启动慢)
  python build.py --install      # 打包后自动装自启动 + 桌面快捷方式
  python build.py --clean        # 先清 build/ dist/
  python build.py --icon-only    # 只生成图标

产物:
  dist/PyCal/PyCal.exe          (onedir)
  dist/PyCal.exe                 (onefile)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent


def log(msg: str) -> None:
    print(f"\033[36m[build]\033[0m {msg}", flush=True)


def err(msg: str) -> None:
    print(f"\033[31m[build] ERROR:\033[0m {msg}", flush=True)


def run(cmd: list[str], **kw) -> int:
    log(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=PROJECT, **kw).returncode


def ensure_pyinstaller() -> bool:
    try:
        import PyInstaller  # noqa: F401
        log(f"PyInstaller {PyInstaller.__version__}")
        return True
    except ImportError:
        log("安装 PyInstaller ...")
        return run([sys.executable, "-m", "pip", "install", "pyinstaller"]) == 0


def make_icon() -> Path | None:
    icon = PROJECT / "assets" / "pycal.ico"
    if icon.exists():
        log(f"图标已存在: {icon}")
        return icon
    log("生成图标 ...")
    sys.path.insert(0, str(PROJECT))
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from pycal.installer import make_icon as _mk
        icon.parent.mkdir(parents=True, exist_ok=True)
        _mk(str(icon))
        return icon if icon.exists() else None
    except Exception as e:
        err(f"图标生成失败: {e}")
        return None


def clean() -> None:
    log("清理 build/ 和 dist/ ...")
    for d in ("build", "dist"):
        p = PROJECT / d
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    # PyInstaller 缓存
    for d in (Path.home() / ".cache" / "pyinstaller",):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    spec_cache = PROJECT / "__pycache__"
    for f in spec_cache.glob("pycal.spec*"):
        f.unlink(missing_ok=True)


def build(onefile: bool) -> int:
    if not ensure_pyinstaller():
        err("PyInstaller 安装失败")
        return 1
    icon = make_icon()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(PROJECT / "pycal.spec"),
        "--noconfirm",
    ]
    # 模式由 spec 读 PY_CAL_ONEFILE 环境变量决定 (spec 文件模式下
    # --onefile/--onedir 命令行参数会被 PyInstaller 拒绝)
    env = os.environ.copy()
    env["PYCAL_ONEFILE"] = "1" if onefile else "0"
    if not icon:
        env["PYCAL_NO_ICON"] = "1"

    log("开始打包 (可能需要 1-3 分钟) ...")
    rc = run(cmd, env=env)
    if rc != 0:
        err(f"打包失败 (exit {rc})")
        return rc

    exe = PROJECT / "dist" / (Path("PyCal.exe") if onefile
                          else Path("PyCal") / "PyCal.exe")
    if exe.exists():
        size_mb = exe.stat().st_size / (1024 * 1024)
        log(f"✓ 完成: {exe}  ({size_mb:.1f} MB)")
        return 0
    err(f"未找到产物: {exe}")
    return 1


def post_install() -> int:
    """打包后: 装自启动 + 桌面快捷方式。"""
    log("配置自启动 + 桌面快捷方式 ...")
    # 找到刚打出来的 exe
    candidates = [
        PROJECT / "dist" / "PyCal.exe",
        PROJECT / "dist" / "PyCal" / "PyCal.exe",
    ]
    exe = next((c for c in candidates if c.exists()), None)
    if exe is None:
        err("找不到 exe, 跳过安装配置")
        return 1

    icon = PROJECT / "assets" / "pycal.ico"
    sys.path.insert(0, str(PROJECT))
    try:
        import winreg
        from pycal.desktop.autostart import (
            APP_NAME, RUN_KEY, create_desktop_shortcut, shortcut_exists,
        )
        # 自启动
        cmdline = f'"{exe}" gui'
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmdline)
        log(f"✓ 自启动: {cmdline}")

        # 桌面快捷方式
        lnk = create_desktop_shortcut(
            target=str(exe), arguments="gui",
            icon=str(icon) if icon.exists() else None,
        )
        log(f"✓ 桌面快捷方式: {lnk}" if lnk else "✗ 快捷方式创建失败")
        return 0
    except Exception as e:
        err(f"安装配置失败: {e}")
        return 1


def main() -> int:
    p = argparse.ArgumentParser(prog="build")
    p.add_argument("--onefile", action="store_true",
                   help="单文件 exe (启动慢, 分发方便)")
    p.add_argument("--install", action="store_true",
                   help="打包后自动装自启动 + 桌面快捷方式")
    p.add_argument("--clean", action="store_true")
    p.add_argument("--icon-only", action="store_true")
    args = p.parse_args()

    if args.icon_only:
        make_icon()
        return 0

    if args.clean:
        clean()

    rc = build(args.onefile)
    if rc != 0:
        return rc

    if args.install:
        return post_install()

    print()
    log("下一步:")
    log("  运行:      dist\\PyCal\\PyCal.exe   (onedir)")
    log("  或安装:    python build.py --install")
    log("  状态检查:  python -m pycal.installer --status")
    log("  卸载:      python -m pycal.installer --uninstall")
    return 0


if __name__ == "__main__":
    sys.exit(main())