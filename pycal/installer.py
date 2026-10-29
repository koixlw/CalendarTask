"""安装器 CLI — python -m pycal.installer

负责:
  - 自启动开关 (HKCU Run)
  - 桌面快捷方式创建 / 删除
  - 应用图标生成 (.ico)
  - 卸载

用法:
  python -m pycal.installer --autostart on
  python -m pycal.installer --autostart off
  python -m pycal.installer --autostart status
  python -m pycal.installer --shortcut create
  python -m pycal.installer --shortcut remove
  python -m pycal.installer --status
  python -m pycal.installer --uninstall
  python -m pycal.installer --uninstall --purge     # 连数据目录一起删
  python -m pycal.installer --make-icon out.ico
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _setup_stdout() -> None:
    """Windows 控制台默认 GBK, 遇到 ✓/✗ 会崩。强制 UTF-8。"""
    from pycal.console import setup_stdout
    setup_stdout()


def _app_icon_path() -> str | None:
    """找 PyCal 的 .ico (打包后 exe 图标 / 源码目录 pycal/assets/)。"""
    if getattr(sys, "frozen", False):
        return sys.executable       # exe 内嵌图标
    here = Path(__file__).resolve().parent
    for cand in (here / "assets" / "pycal.ico",
                 here.parent / "assets" / "pycal.ico"):
        if cand.exists():
            return str(cand)
    return None


def make_icon(out_path: str) -> int:
    """生成多尺寸 .ico。"""
    _setup_stdout()
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
        from PySide6.QtCore import Qt
    except ImportError:
        print("需要 PySide6: pip install PySide6")
        return 1

    app = QApplication.instance() or QApplication([])

    # 画一个 256px 的日历图标
    base = QPixmap(256, 256)
    base.fill(Qt.GlobalColor.transparent)
    p = QPainter(base)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # 圆角底
    p.setBrush(QColor(252, 252, 250))
    p.setPen(QColor(90, 90, 90))
    p.drawRoundedRect(8, 8, 240, 240, 28, 28)
    # 红头
    p.setBrush(QColor(219, 68, 55))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(8, 8, 240, 58, 28, 28)
    p.drawRect(8, 40, 240, 26)
    # 两个挂环
    p.setBrush(QColor(140, 40, 32))
    p.drawRoundedRect(56, 0, 22, 34, 8, 8)
    p.drawRoundedRect(178, 0, 22, 34, 8, 8)
    # 数字 "21"
    p.setPen(QColor(45, 55, 72))
    f = QFont("Segoe UI", 96, QFont.Weight.Bold)
    p.setFont(f)
    p.drawText(base.rect().adjusted(0, 56, 0, -8),
               Qt.AlignmentFlag.AlignCenter, "21")
    p.end()

    # 导出多尺寸 ico
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [base.scaled(s, s,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
              for s in sizes]
    # PySide6 的 QPixmap.save 支持 ico 但只写单尺寸, 用 256 即可 (Win 会缩放)
    img = images[-1]
    # 手动构造 ICO (PNG 压缩格式, 多尺寸)
    import struct
    data = _build_multi_ico([img.toImage() for img in images])
    Path(out_path).write_bytes(data)
    print(f"✓ 图标已生成: {out_path}  ({len(data)} bytes, {len(sizes)} 尺寸)")
    return 0


def _build_multi_ico(qimages) -> bytes:
    """把多个 QImage 打包成 Windows .ico (PNG-compressed entries)。"""
    import struct
    n = len(qimages)
    entries = []
    blobs = []
    offset = 6 + 16 * n          # header + directory
    for img in qimages:
        # QImage -> PNG bytes
        from PySide6.QtCore import QBuffer, QByteArray
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        buf.close()
        png = bytes(ba)
        w = img.width()
        h = img.height()
        entries.append((w, h, len(png), offset))
        blobs.append(png)
        offset += len(png)

    out = bytearray()
    out += struct.pack("<HHH", 0, 1, n)
    for w, h, size, off in entries:
        out += struct.pack(
            "<BBBBHHII",
            0 if w >= 256 else w, 0 if h >= 256 else h,
            0, 0, 1, 32, size, off,
        )
    for b in blobs:
        out += b
    return bytes(out)


def cmd_status() -> int:
    from pycal.desktop.autostart import (
        autostart_enabled, autostart_command,
        shortcut_exists, IS_WINDOWS,
    )
    print(f"平台: {'Windows' if IS_WINDOWS else sys.platform}")
    print(f"运行模式: {'打包 exe' if getattr(sys, 'frozen', False) else '源码'}")
    print(f"图标: {_app_icon_path() or '(未找到)'}")
    print(f"自启动: {'✓ 已启用' if autostart_enabled() else '✗ 未启用'}")
    cmd = autostart_command()
    if cmd:
        print(f"  命令: {cmd}")
    print(f"桌面快捷方式: {'✓ 存在' if shortcut_exists() else '✗ 不存在'}")
    base = os.environ.get("APPDATA", "")
    data = Path(base) / "PyCal"
    print(f"数据目录: {data} ({'存在' if data.exists() else '不存在'})")
    if data.exists():
        for sub in ("db", "reports", "cache"):
            p = data / sub
            if p.exists():
                n = len(list(p.glob('*')))
                print(f"  {sub}/: {n} 个文件")
    return 0


def cmd_uninstall(purge: bool) -> int:
    from pycal.desktop.autostart import uninstall
    uninstall(delete_data=purge)
    return 0


def main() -> int:
    _setup_stdout()
    p = argparse.ArgumentParser(prog="pycal.installer")
    p.add_argument("--autostart", choices=["on", "off", "status"])
    p.add_argument("--shortcut", choices=["create", "remove", "status"])
    p.add_argument("--status", action="store_true")
    p.add_argument("--uninstall", action="store_true")
    p.add_argument("--purge", action="store_true",
                   help="连数据目录一起删除")
    p.add_argument("--make-icon", metavar="OUT.ICO")
    args = p.parse_args()

    if args.make_icon:
        return make_icon(args.make_icon)

    did_something = False

    if args.autostart:
        from pycal.desktop.autostart import (
            enable_autostart, disable_autostart, autostart_enabled,
            autostart_command,
        )
        did_something = True
        if args.autostart == "on":
            if enable_autostart():
                print(f"✓ 已启用自启动: {autostart_command()}")
            else:
                print("✗ 启用自启动失败")
                return 1
        elif args.autostart == "off":
            disable_autostart()
            print("✓ 已关闭自启动")
        else:
            print("✓ 自启动已启用" if autostart_enabled() else "✗ 自启动未启用")
            cmd = autostart_command()
            if cmd:
                print(f"  {cmd}")

    if args.shortcut:
        from pycal.desktop.autostart import (
            create_desktop_shortcut, remove_desktop_shortcut,
            shortcut_exists,
        )
        did_something = True
        if args.shortcut == "create":
            lnk = create_desktop_shortcut(icon=_app_icon_path())
            print(f"✓ 桌面快捷方式已创建: {lnk}" if lnk
                  else "✗ 创建失败")
            if not lnk:
                return 1
        elif args.shortcut == "remove":
            remove_desktop_shortcut()
            print("✓ 桌面快捷方式已删除")
        else:
            print("✓ 存在" if shortcut_exists() else "✗ 不存在")

    if args.uninstall:
        cmd_uninstall(args.purge)
        did_something = True

    if args.status:
        cmd_status()
        did_something = True

    if not did_something:
        p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())