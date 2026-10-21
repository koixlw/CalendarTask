"""皮肤系统 — 主题切换 + 不透明度 + 字体缩放。

内置 4 种皮肤:
  - light_default   透明白底, 蓝点缀
  - warm_paper      浅米黄, 棕点缀
  - dark_mode       深灰底, 浅色文字
  - glass_blur      玻璃磨砂 (用 translucent + 较低不透明度)

皮肤字典结构:
  {
    "name": "light_default",
    "window_bg": "rgba(...)",           # 整体背景色
    "border": "1px solid rgba(...)",     # 边框
    "title_fg": "#2C3E50",              # 标题颜色
    "btn_bg": "white",                   # 按钮背景
    "btn_hover": "#EAF3FF",
    "btn_border": "#BBB",
    "status_fg": "#555",
  }
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Skin:
    name: str
    window_bg: str
    border: str
    title_fg: str
    btn_bg: str
    btn_hover: str
    btn_pressed: str
    btn_border: str
    btn_fg: str
    status_fg: str
    opacity: float            # 0..1, 主窗不透明度


SKINS: dict[str, Skin] = {
    "light_default": Skin(
        name="light_default",
        window_bg="rgba(245, 245, 245, 200)",
        border="1px solid rgba(120, 120, 120, 100)",
        title_fg="#2C3E50",
        btn_bg="white",
        btn_hover="#EAF3FF",
        btn_pressed="#CCE0FF",
        btn_border="#BBB",
        btn_fg="#222",
        status_fg="#555",
        opacity=1.0,
    ),
    "warm_paper": Skin(
        name="warm_paper",
        window_bg="rgba(252, 245, 230, 220)",
        border="1px solid rgba(180, 160, 120, 140)",
        title_fg="#5D4037",
        btn_bg="#FFF8DC",
        btn_hover="#FFEFD5",
        btn_pressed="#FFE4B5",
        btn_border="#D2B48C",
        btn_fg="#5D4037",
        status_fg="#7B5E3C",
        opacity=1.0,
    ),
    "dark_mode": Skin(
        name="dark_mode",
        window_bg="rgba(35, 35, 40, 220)",
        border="1px solid rgba(80, 80, 90, 200)",
        title_fg="#ECEFF4",
        btn_bg="#3B4252",
        btn_hover="#4C566A",
        btn_pressed="#5E81AC",
        btn_border="#4C566A",
        btn_fg="#ECEFF4",
        status_fg="#88C0D0",
        opacity=1.0,
    ),
    "glass_blur": Skin(
        name="glass_blur",
        window_bg="rgba(255, 255, 255, 80)",
        border="1px solid rgba(255, 255, 255, 100)",
        title_fg="#1A1A1A",
        btn_bg="rgba(255, 255, 255, 180)",
        btn_hover="rgba(220, 235, 255, 220)",
        btn_pressed="rgba(190, 215, 255, 240)",
        btn_border="rgba(180, 200, 230, 180)",
        btn_fg="#1A1A1A",
        status_fg="#444",
        opacity=0.92,
    ),
}


def current_skin(name: str = "light_default") -> Skin:
    return SKINS.get(name, SKINS["light_default"])


def stylesheet_for(skin: Skin) -> str:
    """生成主窗 QSS — 用于 setStyleSheet。"""
    return f"""
        QMainWindow {{
            background: {skin.window_bg};
        }}
        QWidget#Central {{
            background: {skin.window_bg};
            border: {skin.border};
        }}
        QLabel[role="title"] {{
            color: {skin.title_fg};
            font-weight: bold;
            font-size: 12pt;
        }}
        QToolButton {{
            border: 1px solid {skin.btn_border};
            border-radius: 4px;
            background: {skin.btn_bg};
            color: {skin.btn_fg};
            padding: 4px 8px;
            font-weight: normal;
        }}
        QToolButton:hover {{
            background: {skin.btn_hover};
            border: 1px solid #4A90E2;
        }}
        QToolButton:pressed {{
            background: {skin.btn_pressed};
        }}
        QLabel[role="month_label"] {{
            font-size: 14pt;
            font-weight: bold;
            color: {skin.title_fg};
        }}
        QStatusBar {{
            color: {skin.status_fg};
        }}
        QStatusBar > QLabel {{
            color: {skin.status_fg};
        }}
    """


__all__ = ["Skin", "SKINS", "current_skin", "stylesheet_for"]
