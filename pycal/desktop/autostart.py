"""Windows 自启动 + 桌面快捷方式 + 图标。

自启动:
  写 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
  (HKCU 而非 HKLM — 不需要管理员权限, 也不会被 UAC 拦)

桌面快捷方式:
  用 COM (IShellLink) 建 .lnk, 可指定图标和启动参数

卸载:
  自启项 + 快捷方式 + 可选数据目录
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Optional

IS_WINDOWS = sys.platform == "win32"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "PyCal"


# ============================================================================
# 自启动
# ============================================================================
def _run_key_path() -> str:
    return RUN_KEY


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _run_key_path()
        ) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(val)
    except (FileNotFoundError, OSError):
        return False


def autostart_command() -> str:
    """当前注册的自启动命令 (没有则空串)。"""
    if not IS_WINDOWS:
        return ""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _run_key_path()
        ) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
            return str(val)
    except (FileNotFoundError, OSError):
        return ""


def enable_autostart(extra_args: str = "") -> bool:
    """写 HKCU Run 自启动。返回 True/False。"""
    if not IS_WINDOWS:
        return False
    exe = _resolve_executable()
    if not exe:
        return False
    cmd = f'"{exe}" gui{" " + extra_args if extra_args else ""}'
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _run_key_path(), 0,
            winreg.KEY_SET_VALUE,
        ) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
        return True
    except OSError as e:
        print(f"[autostart] 写入失败: {e}")
        return False


def disable_autostart() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _run_key_path(), 0,
            winreg.KEY_SET_VALUE,
        ) as k:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass
        return True
    except OSError as e:
        print(f"[autostart] 删除失败: {e}")
        return False


# ============================================================================
# 可执行文件解析 (兼容 PyInstaller onedir / onefile / 源码运行)
# ============================================================================
def _resolve_executable() -> Optional[str]:
    """找到应该写进自启动的 exe 路径。

    优先级:
      1. PyInstaller frozen 时的 sys.executable
      2. 开发时: python -m pycal 的启动器 (写 python + 参数)
      3. PATH 里的 pycal.exe
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    # 开发模式: 用 python 解释器 + -m pycal
    return None


def build_autostart_cmd(extra_args: str = "") -> str:
    """构造完整命令行 (开发/打包通用)。"""
    if getattr(sys, "frozen", False):
        exe = sys.executable
    else:
        exe = sys.executable     # python.exe
        extra_args = f"-m pycal {extra_args}".strip()
    cmd = f'"{exe}" {extra_args}'.strip()
    return cmd


# ============================================================================
# 桌面快捷方式 (IShellLink COM)
# ============================================================================
class _IShellLinkW(ctypes.Structure):
    _fields_ = [
        ("vtable", ctypes.c_void_p),
        ("fFlags", ctypes.c_uint),
        ("fIconIndex", ctypes.c_int),
        ("fShowCmd", ctypes.c_int),
        ("fHotKey", ctypes.c_ushort),
        ("fReserved", ctypes.c_ushort),
        ("pszName", wintypes.LPWSTR),
        ("pszIconLocation", wintypes.LPWSTR),
        ("pszArguments", wintypes.LPWSTR),
        ("pszWorkingDirectory", wintypes.LPWSTR),
        ("pszDescription", wintypes.LPWSTR),
    ]


class _IShellLinkW(ctypes.Structure):
    """保留定义以便将来需要裸 COM 调用 (当前用 PowerShell 路线)。"""
    _fields_ = [
        ("vtable", ctypes.c_void_p),
        ("fFlags", ctypes.c_uint),
        ("fIconIndex", ctypes.c_int),
        ("fShowCmd", ctypes.c_int),
        ("fHotKey", ctypes.c_ushort),
        ("fReserved", ctypes.c_ushort),
        ("pszName", wintypes.LPWSTR),
        ("pszIconLocation", wintypes.LPWSTR),
        ("pszArguments", wintypes.LPWSTR),
        ("pszWorkingDirectory", wintypes.LPWSTR),
        ("pszDescription", wintypes.LPWSTR),
    ]


def _shortcut_dir() -> Path:
    return Path.home() / "Desktop"


def create_desktop_shortcut(
    target: Optional[str] = None,
    arguments: str = "gui",
    icon: Optional[str] = None,
    description: str = "PyCal 桌面日历",
) -> Optional[Path]:
    """在桌面创建 .lnk。返回 lnk 路径 (失败 None)。

    实现: PowerShell 的 WScript.Shell COM — 比裸 IShellLink vtable 稳。
    """
    if not IS_WINDOWS:
        return None
    if target is None:
        target = sys.executable
        if not getattr(sys, "frozen", False):
            arguments = f"-m pycal {arguments}".strip()
    return _create_shortcut_via_powershell(
        target, arguments, icon, description
    )


def _create_shortcut_via_powershell(
    target: str, arguments: str,
    icon: Optional[str], description: str,
) -> Optional[Path]:
    """用 PowerShell 的 WScript.Shell COM 创建快捷方式 (最稳)。"""
    import subprocess
    lnk = _shortcut_dir() / f"{APP_NAME}.lnk"
    # 图标: 优先 exe 内嵌图标, 否则用默认
    icon_line = f"$s.IconLocation='{icon}',0;" if icon else ""
    ps = (
        "$s=(New-Object -ComObject WScript.Shell)"
        f".CreateShortcut('{lnk}');"
        f"$s.TargetPath='{target}';"
        f"$s.Arguments='{arguments}';"
        f"$s.WorkingDirectory='{Path(target).parent}';"
        f"$s.Description='{description}';"
        f"{icon_line}"
        "$s.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            check=True, capture_output=True, timeout=15,
        )
        return lnk if lnk.exists() else None
    except (subprocess.SubprocessError, OSError) as e:
        print(f"[shortcut] 创建失败: {e}")
        return None


def remove_desktop_shortcut() -> bool:
    if not IS_WINDOWS:
        return False
    lnk = _shortcut_dir() / f"{APP_NAME}.lnk"
    try:
        if lnk.exists():
            lnk.unlink()
        return True
    except OSError:
        return False


def shortcut_exists() -> bool:
    return (_shortcut_dir() / f"{APP_NAME}.lnk").exists()


# ============================================================================
# 卸载
# ============================================================================
def uninstall(delete_data: bool = False) -> None:
    """完整卸载: 自启 + 快捷方式 + (可选) 数据目录。"""
    ok_autostart = disable_autostart()
    ok_shortcut = remove_desktop_shortcut()
    print(f"[uninstall] 自启动: {'已移除' if ok_autostart else '失败/无'}")
    print(f"[uninstall] 桌面快捷方式: {'已移除' if ok_shortcut else '无'}")
    if delete_data:
        import winreg
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PyCal",
                0, winreg.KEY_SET_VALUE,
            ) as k:
                winreg.DeleteValue(k, "UninstallString")
        except (FileNotFoundError, OSError):
            pass
        base = os.environ.get("APPDATA", "")
        data_dir = Path(base) / "PyCal"
        if data_dir.exists():
            shutil.rmtree(data_dir, ignore_errors=True)
            print(f"[uninstall] 数据目录已删除: {data_dir}")
    else:
        print("[uninstall] 数据目录保留 (加 --purge 才删)")


__all__ = [
    "autostart_enabled", "enable_autostart", "disable_autostart",
    "autostart_command", "build_autostart_cmd",
    "create_desktop_shortcut", "remove_desktop_shortcut",
    "shortcut_exists", "uninstall", "APP_NAME", "IS_WINDOWS",
]