"""快捷键与功能说明 — 新手入口 (工具栏「？ 帮助」/ F1)。

之前工具栏上是一排没有文字的 emoji 图标, 新用户根本不知道哪个是干嘛的。
这个对话框把「格子怎么用」「每个功能在哪」「所有快捷键」一次讲清楚。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTabWidget, QWidget,
    QGridLayout, QAbstractItemView, QHeaderView,
)
from PySide6.QtGui import QFont


# (功能, 在哪里, 快捷键, 说明)
_FEATURES: list[tuple[str, str, str, str]] = [
    ("新增条目", "双击日期格子", "Ctrl+N",
     "在今天新建一条。格子右键也有「新建」分类: 格子 / 重复任务 / 提醒 / 倒数日。"),
    ("编辑条目", "双击条目文字", "—",
     "双击格子里的某一行文字打开编辑器, 可改内容、颜色、类型。"),
    ("勾选完成", "单击左侧 ☐", "—",
     "多行条目里点哪一行就勾哪一行, 计数和日报会跟着变。"),
    ("更多操作", "右键日期格子", "—",
     "编辑 / 删除 / 复制 / 新建各类条目。"),
    ("切换月份", "工具栏 ◀ ▶", "—",
     "也可以点「今天」回到当月。"),
    ("缩放窗口", "工具栏「□ 铺满屏幕 / □ 收成窗口」", "—",
     "点一下在「铺满屏幕」和「普通窗口」之间切换。"
     "收成窗口之后拖四边和四角就能自由缩放 (最小 616×405), "
     "窗口太窄时工具栏会自动收成纯图标, 悬停仍有完整说明。"),
    ("年视图", "工具栏「📆 年视图」", "Y",
     "一屏摆下全年 12 个月的小格子 (每周从星期一开始), "
     "下面是选中月的详情, 勾选/编辑跟月视图一样。"
     "点月份标题选中那个月, 点某一天直接跳到那天。"),
    ("搜索", "工具栏「🔍 搜索」", "Ctrl+F",
     "在所有日期的所有条目里搜关键词, 双击结果直接跳过去编辑。"),
    ("日报 / 周报", "工具栏「📑 日报」", "Ctrl+R",
     "预览后复制, 或导出 MD / HTML / PDF。每天 18:00 后还会自动生成一份。"),
    ("AI 润色", "工具栏「✨ AI 日报」", "Ctrl+Shift+R",
     "用 Claude 把日报/周报改写得更通顺。需要先配好 API key。"),
    ("配 API key", "⋯ 更多 → 🔑 AI 润色 API key", "Ctrl+,",
     "把 Anthropic API key 填进去就能用 AI 润色 —— 不用再改环境变量, "
     "存好立刻生效, 不用重启。"),
    ("云同步", "⋯ 更多 → 云同步", "Ctrl+S",
     "CalDAV 双向同步, 支持 Google / iCloud / Outlook / 自建服务器。"),
    ("便签", "⋯ 更多 → 便签", "—", "开一个随手记的小窗口, 单独保存在本机。"),
    ("导入 DesktopCal", "⋯ 更多 → 从 DesktopCal 导入", "—",
     "把 DesktopCal 的历史条目一次性搬过来。"),
    ("刷新", "⋯ 更多 → 重新拉取数据", "F5", "从数据库重新读一遍本月内容。"),
    ("皮肤 / 不透明度", "⋯ 更多 → 皮肤 / 不透明度", "—",
     "浅色 / 暖色 / 深色 / 玻璃四种, 外加 60%~100% 不透明度。"),
    ("字体大小", "⋯ 更多 → 字体大小", "—",
     "字看着太小就调这里: 五档预设, 也能用 A+ / A− 一档一档微调。"
     "工具栏 / 月视图 / 状态栏一起变, 调完记住, 下次打开还是这个大小。"),
    ("窗口置顶", "⋯ 更多 → 窗口置顶", "—", "让 PyCal 始终浮在其它窗口之上。"),
    ("月份切换动画", "⋯ 更多 → 月份切换动画", "—", "翻月时的左右滑动, 可关。"),
    ("生成壁纸", "⋯ 更多 → 生成月历壁纸", "—",
     "把当月月视图导成图片并设为桌面壁纸。"),
    ("收起窗口", "工具栏「— 收起」", "—",
     "窗口藏到右下角托盘, 程序继续在后台跑, 提醒照常。"),
    ("快捷待办小窗", "单击右下角托盘图标", "Esc 收起",
     "弹一个小窗口出来, 输入框直接打字回车就存成今天的待办。\n"
     "面板能随便拖, 挪到哪下次就在哪; 里面勾选和月视图格子同步。"),
    ("最大化 / 还原", "工具栏「□ 铺满屏幕」", "—",
     "也可双击工具栏空白处。还原成普通窗口后可以自由拖边缩放。"),
    ("退出程序", "工具栏「× 退出」", "—",
     "彻底结束程序。只想收起窗口请用「— 收起」。"),
]

_SHORTCUTS: list[tuple[str, str]] = [
    ("F1", "打开本帮助"),
    ("Y", "切换年视图 / 月视图"),
    ("Ctrl+N", "在今天新建条目"),
    ("Ctrl+F", "搜索"),
    ("Ctrl+R", "生成日报 / 周报"),
    ("Ctrl+Shift+R", "AI 润色日报"),
    ("Ctrl+S", "打开云同步"),
    ("Ctrl+T", "回到今天"),
    ("Ctrl+,", "打开设置 (配 API key)"),
    ("F5", "重新拉取数据"),
    ("Esc", "关闭窗口 (同 Alt+F4, 行为可在「⋯ 更多」里改)"),
]


class HelpDialog(QDialog):
    """三页说明: 基本用法 / 功能清单 / 快捷键。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PyCal 使用说明")
        self.setMinimumSize(760, 520)

        root = QVBoxLayout(self)
        intro = QLabel(
            "<b>PyCal</b> 是一个贴在桌面上的月历本 —— 记事、提醒、倒数日，"
            "还能自动出日报/周报，同步到 Google / iCloud 日历。"
        )
        intro.setWordWrap(True)
        intro.setFont(QFont(intro.font().family(), 11))
        root.addWidget(intro)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_basics(), "基本用法")
        tabs.addTab(self._build_features(), "功能清单")
        tabs.addTab(self._build_shortcuts(), "快捷键")
        root.addWidget(tabs, 1)

    # ------------------------------------------------------------------
    def _build_basics(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setSpacing(10)
        for title, body in [
            ("① 记一件事",
             "双击任意日期格子就能写一条。多行内容直接换行，"
             "每一行前面会出现 ☐，点一下就勾成完成。"),
            ("② 分清条目类型",
             "格子右键 →「新建」里可以选 <b>格子</b>（普通记事）、"
             "<b>重复任务</b>（每天/每周…自动重复）、<b>提醒</b>（到点弹通知）、"
             "<b>倒数日</b>（显示「还有 N 天」）。"),
            ("③ 出日报 / 周报",
             "点工具栏「📑 日报」自动汇总当天（或本周）完成的事，"
             "可以预览、复制，或导出成 Markdown / HTML / PDF。"
             "在「⋯ 更多 → 🔑 AI 润色 API key」里填一次 API key，"
             "之后还能用「✨ AI 日报」让 Claude 润色一遍。"),
            ("④ 关窗口不等于退出",
             "点「— 收起」或按 Alt+F4，窗口会藏到右下角托盘，"
             "<b>程序仍在后台运行</b>，提醒和自动同步照常。"
             "<b>单击</b>托盘图标会弹出「快捷待办」小窗（随手记一条待办），"
             "<b>双击</b>托盘图标唤回主窗口，要彻底结束请点「× 退出」。"),
            ("⑤ 找不到功能？",
             "工具栏上没显示的功能都在「⋯ 更多」菜单里，"
             "每一项都有一句话说明。菜单底部的「关闭窗口时」可以改"
             "关窗行为（收起到托盘 / 直接退出）。"),
        ]:
            head = QLabel(f"<b>{title}</b>")
            body_lbl = QLabel(body)
            body_lbl.setWordWrap(True)
            lay.addWidget(head)
            lay.addWidget(body_lbl)
        lay.addStretch(1)
        return page

    def _build_features(self) -> QWidget:
        return _feature_table(self)

    def _build_shortcuts(self) -> QWidget:
        page = QWidget(self)
        grid = QGridLayout(page)
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(24)
        r = 0
        for key, desc in _SHORTCUTS:
            k = QLabel(f"<b>{key}</b>")
            d = QLabel(desc)
            d.setWordWrap(True)
            # 两列都顶对齐, 否则说明会贴到行底, 和快捷键对不齐
            grid.addWidget(k, r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(d, r, 1, Qt.AlignmentFlag.AlignTop)
            r += 1
        grid.setColumnStretch(1, 1)
        return page


def _feature_table(parent) -> QWidget:
    """功能清单表 — 4 列: 功能 / 入口 / 快捷键 / 说明。"""
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
    table = QTableWidget(len(_FEATURES), 4, parent)
    table.setHorizontalHeaderLabels(["功能", "在哪里", "快捷键", "说明"])
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(
        QAbstractItemView.SelectionBehavior.SelectRows)
    table.setWordWrap(True)
    table.verticalHeader().setVisible(False)
    for r, (name, where, key, desc) in enumerate(_FEATURES):
        for c, text in enumerate((name, where, key, desc)):
            table.setItem(r, c, QTableWidgetItem(text))
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    # 「在哪里」和「说明」都设 Stretch 共分余量, 否则前者会被说明挤到截断
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setDefaultSectionSize(34)
    return table