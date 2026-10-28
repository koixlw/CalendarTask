"""桌面壁纸集成。

技术路径 (Phase 1 D):
  1. 取用户当前壁纸 (Windows 注册表 / TranscodedWallpaper)
  2. 用 QPainter 在其底部右下角叠加本月日历 (render_calendar_panel)
  3. 保存到临时文件
  4. 通过 SystemParametersInfoW(SPI_SETDESKWALLPAPER) 设为新壁纸
  5. (可选) 还原: restore_original_wallpaper()

参考 Microsoft 文档 SPI_SETDESKWALLPAPER (0x0014)。
"""

from __future__ import annotations
import ctypes
import ctypes.wintypes as wt
import os
import sys
import tempfile
from pathlib import Path
from datetime import date
from typing import Optional

from PySide6.QtCore import Qt, QSize, QRect
from PySide6.QtGui import QImage, QPainter, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import QApplication


SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02


def _set_windows_wallpaper(path: str) -> bool:
    """Windows-specific: 调用 SystemParametersInfoW 设置壁纸。

    在非 Windows 平台是 no-op。
    """
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    user32.SystemParametersInfoW.restype = wt.BOOL
    res = user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER,
        0,
        ctypes.c_wchar_p(str(Path(path).absolute())),
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
    )
    return bool(res)


def _read_windows_original_wallpaper() -> Optional[Path]:
    """读 Windows 当前壁纸路径 — 优先 TranscodedImageCache, fallback 注册表。

    TranscodedWallpaper 更可靠 (Windows 缩放过的版本, 适合作为画布)。
    """
    if sys.platform != "win32":
        return None

    # 1. Transcoded (实际渲染到桌面的图, Windows 自动缩放)
    roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    candidates = [
        Path(roaming) / "Microsoft" / "Windows" / "Themes" / "TranscodedWallpaper",
        Path(roaming) / "Microsoft" / "Windows" / "Themes" / "Transcoded100",
    ]
    for c in candidates:
        if c.exists():
            return c

    # 2. 注册表 (HKCU\Control Panel\Desktop\WallPaper)
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\Desktop",
        ) as k:
            value, _ = winreg.QueryValueEx(k, "WallPaper")
            if value and Path(value).exists():
                return Path(value)
    except (ImportError, FileNotFoundError, OSError):
        pass

    return None


# ── 壁纸亮度采样 (桌面挂件靠它决定字色) ──
_wallpaper_cache: dict[str, QImage] = {}


def _wallpaper_for_screen(screen) -> Optional[QImage]:
    """壁纸铺满该屏幕之后的样子。

    跟 Windows 的「填充」一致: 等比放大到盖住屏幕, 再居中裁掉多出来的部分。
    结果缓存起来 — 挂件拖动时每帧都问一次亮度, 不缓存会卡。
    """
    if screen is None:
        return None
    geo = screen.geometry()
    key = f"{screen.name()}:{geo.width()}x{geo.height()}"
    cached = _wallpaper_cache.get(key)
    if cached is not None:
        return cached
    path = _read_windows_original_wallpaper()
    if path is None:
        return None
    img = QImage(str(path))
    if img.isNull():
        return None
    scaled = img.scaled(
        geo.size(),
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - geo.width()) // 2)
    y = max(0, (scaled.height() - geo.height()) // 2)
    cropped = scaled.copy(x, y, geo.width(), geo.height())
    _wallpaper_cache[key] = cropped
    return cropped


def clear_wallpaper_cache() -> None:
    """壁纸换了就清一下缓存 — 下次采样会重新读。"""
    _wallpaper_cache.clear()


def sample_brightness(screen, rect: QRect) -> float:
    """屏幕某块区域的壁纸平均亮度 (0 = 全黑, 1 = 全白)。

    rect 用全局坐标。取不到壁纸就返回 0.5 (中性) —— 挂件据此决定
    用深色字还是浅色字。
    """
    if screen is None or rect is None:
        return 0.5
    img = _wallpaper_for_screen(screen)
    if img is None:
        return 0.5
    geo = screen.geometry()
    local = rect.translated(-geo.topLeft()).intersected(
        QRect(0, 0, geo.width(), geo.height()))
    if local.isEmpty():
        return 0.5
    region = img.copy(local)
    if region.isNull():
        return 0.5
    # 缩成一小块再算平均 —— 12×12 足够代表「这块地方是亮是暗」
    small = region.scaled(
        12, 12,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    total = 0.0
    n = 0
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y)
            total += 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()
            n += 1
    if not n:
        return 0.5
    return max(0.0, min(1.0, total / (n * 255.0)))


def _orig_panel_size(base_w: int, base_h: int) -> tuple[int, int]:
    """根据原壁纸尺寸决定面板大小 (4%~6% 的宽度)。"""
    w = max(280, min(420, int(base_w * 0.22)))
    h = int(w * 1.05)   # 几乎正方
    return w, h


def _composite_on_wallpaper(
    base: QImage, panel: QImage, margin_ratio: float = 0.02,
) -> QImage:
    """将 panel 贴到 base 的右下角。返回新图 (base 副本)。

    margin_ratio 与右下边距成比例 (默认 base_w * 2%)。
    """
    out = base.copy()
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin_x = int(out.width() * margin_ratio)
    margin_y = int(out.height() * margin_ratio)
    target_w, target_h = _orig_panel_size(out.width(), out.height())
    panel_scaled = panel.scaled(
        target_w, target_h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = out.width() - panel_scaled.width() - margin_x
    y = out.height() - panel_scaled.height() - margin_y
    p.drawImage(x, y, panel_scaled)
    p.end()
    return out


def _render_full_screen_calendar(
    year: int, month: int,
    items_by_day: Optional[dict[str, list[str]]] = None,
    target_size: Optional[tuple[int, int]] = None,
) -> QImage:
    """透明背景的全屏月历 — 透明度 0, 仅面板不透明, 适合叠加到任意背景。"""
    if target_size is None:
        screen = QGuiApplication.primaryScreen().geometry()
        target_size = (screen.width(), screen.height())
    img = QImage(QSize(*target_size), QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # panel 占据屏幕右下角一块
    panel_w, panel_h = _orig_panel_size(target_size[0], target_size[1])
    margin = int(target_size[0] * 0.02)
    rect = img.rect().adjusted(
        target_size[0] - panel_w - margin,
        target_size[1] - panel_h - margin,
        0, 0,
    )
    painter.fillRect(rect, QColor(255, 255, 255, 235))
    return _draw_calendar_into_painter(painter, rect, year, month, items_by_day)


def _draw_calendar_into_painter(
    painter: QPainter,
    rect,
    year: int,
    month: int,
    items_by_day: Optional[dict[str, list[str]]] = None,
) -> QImage:
    """复用 render_calendar_panel 的画法, 但接受外部 painter + rect。"""
    img = painter.device()
    painter.setPen(QColor(40, 40, 40))
    painter.setFont(QFont("Segoe UI", max(8, rect.width() // 20), QFont.Weight.Bold))
    painter.drawText(
        rect.adjusted(rect.width() // 20, 8, 0, 0),
        Qt.AlignmentFlag.AlignLeft, f"{year} - {month:02d}",
    )
    cell_w = (rect.width() - 20) / 7
    cell_h = (rect.height() - 50) / 7
    headers = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    painter.setFont(QFont("Segoe UI", max(6, rect.width() // 28)))
    for i, hd in enumerate(headers):
        x = rect.left() + 10 + i * cell_w
        y = rect.top() + 35
        painter.setPen(QColor(120, 120, 120))
        painter.drawText(int(x), int(y + 12), hd)
    first = date(year, month, 1)
    from datetime import timedelta
    start = first - timedelta(days=first.weekday())
    painter.setFont(QFont("Segoe UI", max(7, rect.width() // 32)))
    today = date.today()
    for w_idx in range(6):
        for d_idx in range(7):
            d = start + timedelta(days=w_idx * 7 + d_idx)
            x = rect.left() + 10 + d_idx * cell_w
            y = rect.top() + 50 + w_idx * cell_h
            txt = f"{d.day}"
            if d.month != month:
                painter.setPen(QColor(180, 180, 180))
            elif d == today:
                painter.setBrush(QColor(255, 220, 100))
                painter.setPen(QColor(40, 40, 40))
                painter.drawEllipse(int(x), int(y), 22, 22)
            else:
                painter.setPen(QColor(40, 40, 40))
            painter.drawText(int(x + 4), int(y + 16), txt)
            # 副标题 (农历/节气)
            try:
                from pycal.lunar import lunar_for, is_24jieqi
                sub = lunar_for(d)
                if sub:
                    if is_24jieqi(d):
                        painter.setPen(QColor(192, 57, 43))
                        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold, italic=True))
                    elif sub in ("春节", "中秋", "端午"):
                        painter.setPen(QColor(192, 57, 43))
                    else:
                        painter.setPen(QColor(150, 150, 150))
                        painter.setFont(QFont("Segoe UI", 7))
                    painter.drawText(int(x + 4), int(y + 30), sub[:6])
                    painter.setFont(QFont("Segoe UI", 7))
            except Exception:
                pass
    painter.end()
    return img


def render_calendar_panel(
    year: int,
    month: int,
    items_by_day: dict[str, list[str]] | None = None,
    screen_size: tuple[int, int] | None = None,
) -> QImage:
    """把月历渲染成 PNG, 准备贴到壁纸上。

    返回 QImage (RGBA8888), 屏幕尺寸或指定尺寸。
    """
    if screen_size is None:
        screen = QGuiApplication.primaryScreen().geometry()
        screen_size = (screen.width(), screen.height())
    w, h = screen_size

    img = QImage(QSize(w, h), QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 右下角面板
    panel_w, panel_h = 380, 380
    margin = 20
    rect = img.rect().adjusted(
        w - panel_w - margin, h - panel_h - margin, 0, 0,
    )
    painter.fillRect(rect, QColor(255, 255, 255, 230))

    # 标题
    painter.setPen(QColor(40, 40, 40))
    painter.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
    from calendar import monthrange
    title = f"{year} - {month:02d}"
    painter.drawText(rect.adjusted(20, 15, -20, 0), Qt.AlignmentFlag.AlignLeft, title)

    # 7 列 6 行
    cell_w = (panel_w - 40) / 7
    cell_h = (panel_h - 80) / 7
    headers = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    painter.setFont(QFont("Segoe UI", 10))
    for i, hd in enumerate(headers):
        x = rect.left() + 20 + i * cell_w
        y = rect.top() + 60
        painter.setPen(QColor(120, 120, 120))
        painter.drawText(int(x), int(y + 12), hd)

    # 日期
    first = date(year, month, 1)
    from datetime import timedelta
    start = first - timedelta(days=first.weekday())
    painter.setFont(QFont("Segoe UI", 11))
    today = date.today()
    for w_idx in range(6):
        for d_idx in range(7):
            d = start + timedelta(days=w_idx * 7 + d_idx)
            x = rect.left() + 20 + d_idx * cell_w
            y = rect.top() + 80 + w_idx * cell_h
            txt = f"{d.day}"
            if d.month != month:
                painter.setPen(QColor(180, 180, 180))
            elif d == today:
                painter.setBrush(QColor(255, 220, 100))
                painter.setPen(QColor(40, 40, 40))
                painter.drawEllipse(int(x), int(y), 22, 22)
            else:
                painter.setPen(QColor(40, 40, 40))
            painter.drawText(int(x + 4), int(y + 16), txt)
            # 日程预览行 (待 UI 完全接入)

    painter.end()
    return img


def make_and_set_wallpaper(
    year: int | None = None, month: int | None = None,
    base_wallpaper: str | None = None,
    overlay: bool = True,
) -> Path:
    """生成日历面板叠加图, 设置为桌面壁纸。

    参数:
      year, month  — 显示的年/月 (默认今天)
      base_wallpaper — 自定义底图路径 (默认从 Windows 取当前壁纸)
      overlay       — True=叠加在底图右下角 / False=只画面板 (透明背景)

    返回生成的文件路径。
    """
    if QApplication.instance() is None:
        from PySide6.QtWidgets import QApplication as _QA
        _QA(sys.argv)

    today = date.today()
    year = year or today.year
    month = month or today.month

    out = Path(tempfile.gettempdir()) / f"pycal_wallpaper_{year}{month:02d}.png"

    # 解析底图
    if overlay:
        if base_wallpaper is None:
            base_path = _read_windows_original_wallpaper()
        else:
            base_path = Path(base_wallpaper) if Path(base_wallpaper).exists() else None

        if base_path is not None:
            base = QImage(str(base_path))
            if not base.isNull():
                # 屏幕尺寸化 (以便 panel 大小算出来合适)
                screen = QGuiApplication.primaryScreen().geometry()
                if base.width() != screen.width() or base.height() != screen.height():
                    base = base.scaled(
                        screen.width(), screen.height(),
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                # 透明背景的整屏日历
                panel = _render_full_screen_calendar(year, month)
                final = _composite_on_wallpaper(base, panel)
                final.save(str(out))
                if _set_windows_wallpaper(str(out)):
                    return out
                return out

    # 兜底: 只画面板
    panel = render_calendar_panel(year, month, items_by_day={})
    panel.save(str(out))
    if _set_windows_wallpaper(str(out)):
        return out
    return out


def restore_original_wallpaper() -> bool:
    """还原用户原始壁纸 (从注册表读, 重设 SPI_SETDESKWALLPAPER)。"""
    if sys.platform != "win32":
        return False
    orig = _read_windows_original_wallpaper()
    if orig is None:
        return False
    return _set_windows_wallpaper(str(orig))


__all__ = [
    "render_calendar_panel", "make_and_set_wallpaper",
    "restore_original_wallpaper", "sample_brightness",
    "clear_wallpaper_cache",
]



