"""主题系统：日间/夜间双主题（QSS），并跟随 LifeSystem 的主题同步文件。

设计要点：
- 简洁平整（flat）风格：冷灰白底、纯白卡片、细边框、紫色渐变强调（与 LifeSystem / ApiCluster / Arxiver 同源）。
- 全局样式走 QSS；组件状态色用 objectName + 动态属性（cText 等），主题切换时全局 QSS 自动生效。
- 内嵌到 LifeSystem 时，通过轮询 ~/.life_system_theme 文件跟随其主题。
- 强调色采用 LifeSystem 同款紫色 #6c5ce7；主按钮/Logo/用户气泡用「#7c6cf0 → #6d5ce7」渐变，呼应 ApiCluster 的 grad-btn。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Signal

# ---------- 两套配色（语义化键，对齐 life_system 配色体系） ----------
LIGHT = {
    "bg": "#f4f5fa",
    "sidebar": "#ffffff",
    "card": "#ffffff",
    "card_hover": "#eef0f6",
    "border": "#e6e8f0",
    "border_strong": "#d6d9e4",
    "text": "#1b1e2b",
    "text_hi": "#10121b",
    "text_soft": "#8b8fa3",
    "text_faint": "#a6aabf",
    "accent": "#6c5ce7",
    "accent_hi": "#7a6bf0",
    "accent_soft": "#eeebff",
    "primary": "#6c5ce7",
    "primary_hover": "#7a6bf0",
    "on_primary": "#ffffff",
    "input": "#f4f5fa",
    "user_bubble": "#6c5ce7",
    "danger": "#f0435f",
    "danger_soft": "#fde9ed",
    "success": "#0db987",
    "success_soft": "#e2f8f1",
    "link": "#6c5ce7",
    # 语义状态色（与 life_system 一致）
    "green": "#0db987",
    "green_soft": "#e2f8f1",
    "red": "#f0435f",
    "red_soft": "#fde9ed",
    "amber": "#ed9a12",
    "amber_soft": "#fdf1dc",
    "blue": "#3f86e0",
    "blue_soft": "#e6f0fd",
}

DARK = {
    "bg": "#15151d",
    "sidebar": "#1b1b26",
    "card": "#20202d",
    "card_hover": "#292939",
    "border": "#31313f",
    "border_strong": "#3d3d4d",
    "text": "#e7e7f0",
    "text_hi": "#ffffff",
    "text_soft": "#8e8ea8",
    "text_faint": "#6b6b80",
    "accent": "#8b7cff",
    "accent_hi": "#9a8dff",
    "accent_soft": "#2a2548",
    "primary": "#7c6cf0",
    "primary_hover": "#8b7cff",
    "on_primary": "#ffffff",
    "input": "#17171f",
    "user_bubble": "#7c6cf0",
    "danger": "#ff5d7a",
    "danger_soft": "#3a1f27",
    "success": "#3dd68c",
    "success_soft": "#16382c",
    "link": "#8b7cff",
    "green": "#3dd68c",
    "green_soft": "#16382c",
    "red": "#ff5d7a",
    "red_soft": "#3a1f27",
    "amber": "#ffb020",
    "amber_soft": "#3a2d18",
    "blue": "#5fa0ff",
    "blue_soft": "#1d2c45",
}

# 紫色渐变（主按钮 / Logo / 用户气泡），深浅主题通用
_GRAD_PRIMARY = ("qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                 "stop:0 #7c6cf0, stop:1 #6d5ce7)")
_GRAD_PRIMARY_HI = ("qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                    "stop:0 #8b7cff, stop:1 #7a6bf0)")

# ---------- QSS 模板（@key@ 占位符） ----------
_QSS_TEMPLATE = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    outline: none;
}

QMainWindow, QWidget#Root { background: @bg@; color: @text@; }

/* ---------- 侧边栏 ---------- */
QFrame#Sidebar {
    background: @sidebar@;
    border-right: 1px solid @border@;
}
QLabel#AppTitle { font-size: 17px; font-weight: 800; color: @text_hi@; }
QLabel#AppSubtitle { font-size: 11px; color: @text_faint@; }
QLabel#LogoMark {
    background: _GRAD_PRIMARY_;
    color: #ffffff; border-radius: 10px;
    font-size: 15px; font-weight: 800; min-width: 34px; max-width: 34px;
    min-height: 34px; max-height: 34px;
}

QPushButton#NavButton {
    background: transparent; border: none; border-radius: 10px;
    padding: 8px 12px; text-align: left;
    font-size: 13.5px; color: @text_soft@;
}
QPushButton#NavButton:hover { background: @card_hover@; color: @text@; }
QPushButton#NavButton:checked {
    background: @accent_soft@; color: @accent@; font-weight: 700;
}
QPushButton#NavButton[collapsed="true"] { text-align: center; padding: 8px 0px; font-size: 16px; }

/* 侧边栏收起/展开手柄 */
QPushButton#SidebarHandle {
    background: @card@; border: 1px solid @border_strong@; border-radius: 9px;
    color: @text_soft@; font-size: 14px; font-weight: 700; padding: 0;
}
QPushButton#SidebarHandle:hover {
    border-color: @accent@; color: @accent@; background: @accent_soft@;
}

QPushButton#ThemeToggle {
    background: transparent; border: none; border-radius: 9px;
    min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
    font-size: 15px; color: @text_soft@;
}
QPushButton#ThemeToggle:hover { background: @card_hover@; }

QLabel#SideSection {
    font-size: 11px; color: @text_faint@; padding: 4px 6px 2px;
    font-weight: 600;
}

/* 对话列表项 */
QFrame#ConvItem, QPushButton#ConvItem {
    background: transparent; border: none; border-radius: 8px;
    padding: 5px 8px; text-align: left;
    font-size: 12.5px; color: @text_soft@;
}
QFrame#ConvRow { background: transparent; border: 1px solid transparent; border-radius: 9px; }
QFrame#ConvRow:hover { background: @card_hover@; }
QFrame#ConvRow[sel="true"] { background: @accent_soft@; border: 1px solid @accent@; }
QFrame#ConvRow:focus { border: 1px solid @accent_hi@; }
QLabel#ConvTitle { font-size: 12.5px; color: @text@; }
QFrame#ConvRow[sel="true"] QLabel#ConvTitle { color: @accent@; font-weight: 700; }
QLabel#ConvSub { font-size: 11px; color: @text_faint@; }
QPushButton#ConvAction {
    background: transparent; border: none; border-radius: 6px;
    min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px;
    font-size: 11px; color: @text_faint@; padding: 0;
}
QPushButton#ConvAction:hover { background: @card@; color: @text@; }
QPushButton#ConvAction[danger="true"]:hover { background: @red_soft@; color: @red@; }

/* 列表行（找文件结果 / 模型行 / 引用来源行） */
QFrame#FindRow, QFrame#ModelRow, QFrame#SourceRow {
    background: transparent; border: 1px solid transparent; border-radius: 10px;
}
QFrame#FindRow:hover, QFrame#ModelRow:hover { background: @card_hover@; border-color: @border@; }
QFrame#SourceRow { background: @bg@; border: 1px solid @border@; }
QFrame#SourceRow:hover { border-color: @accent@; }

/* 引用来源芯片 / 消息小工具条 */
QPushButton#SourceChip {
    background: @card@; border: 1px solid @border@; border-radius: 9px;
    padding: 3px 9px; font-size: 11.5px; color: @text_soft@;
}
QPushButton#SourceChip:hover { border-color: @accent@; color: @accent@; background: @accent_soft@; }
QPushButton#SourceChip:checked { border-color: @accent@; color: @accent@; background: @accent_soft@; }
QPushButton#MiniAction {
    background: transparent; border: none; border-radius: 6px;
    padding: 3px 7px; font-size: 11.5px; color: @text_faint@;
}
QPushButton#MiniAction:hover { background: @card_hover@; color: @text@; }

/* 卡片式分组（设置页 / 部署页） */
QLabel#CardTitle { font-size: 14px; font-weight: 800; color: @text_hi@; }
QLabel#CardHint { font-size: 11.5px; color: @text_faint@; }
QLabel#FieldLabel { font-size: 12.5px; color: @text_soft@; }
QFrame#RowSep { background: @border@; max-height: 1px; border: none; }
QPushButton#DangerSmall {
    background: transparent; color: @red@;
    border: 1px solid @red@; border-radius: 8px; padding: 5px 12px; font-size: 12px;
}
QPushButton#DangerSmall:hover { background: @red@; color: #ffffff; }
QProgressBar {
    background: @card_hover@; border: none; border-radius: 6px;
    height: 10px; text-align: center;
}
QProgressBar::chunk { background: _GRAD_PRIMARY_; border-radius: 6px; }
QSlider::groove:horizontal { height: 4px; background: @border_strong@; border-radius: 2px; }
QSlider::sub-page:horizontal { height: 4px; background: @accent@; border-radius: 2px; }
QSlider::handle:horizontal {
    background: @card@; border: 2px solid @accent@; width: 12px; height: 12px;
    margin: -6px 0; border-radius: 8px;
}
QSlider::handle:horizontal:hover { background: @accent_soft@; }

/* ---------- 卡片 ---------- */
QFrame#Card {
    background: @card@;
    border: 1px solid @border@;
    border-radius: 14px;
}
QLabel#PageTitle { font-size: 18px; font-weight: 800; color: @text_hi@; }
QLabel#H1 { font-size: 22px; font-weight: 800; color: @text_hi@; }
QLabel#Meta { color: @text_faint@; font-size: 12px; }
QLabel#Muted { color: @text_soft@; font-size: 12px; }
QLabel#Strong { font-weight: 700; color: @text_hi@; }

/* 文字动态色 */
QLabel[cText="red"]    { color: @red@; }
QLabel[cText="amber"]  { color: @amber@; }
QLabel[cText="green"]  { color: @green@; }
QLabel[cText="accent"] { color: @accent@; }
QLabel[cText="muted"]  { color: @text_faint@; }
QLabel[cText="text"]   { color: @text@; }
QLabel[cText="text_hi"]{ color: @text_hi@; }

/* ---------- 输入控件 ---------- */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
    background: @input@; border: 1px solid @border@; border-radius: 9px;
    padding: 8px 10px; color: @text@;
    selection-background-color: @accent@;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border: 1px solid @accent@;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: @card@; border: 1px solid @border@; border-radius: 9px;
    selection-background-color: @accent_soft@; selection-color: @accent@;
    color: @text@;
}

/* ---------- 按钮 ---------- */
QPushButton#Primary {
    background: _GRAD_PRIMARY_;
    color: @on_primary@; border: none; border-radius: 10px;
    padding: 9px 18px; font-weight: 600;
}
QPushButton#Primary:hover { background: _GRAD_PRIMARY_HI_; }
QPushButton#Primary:pressed { background: @accent@; }
QPushButton#Primary:disabled { background: @accent_soft@; color: @text_faint@; }
QPushButton#Primary[collapsed="true"] { padding: 9px 0px; }

QPushButton#Ghost {
    background: transparent; color: @text@;
    border: 1px solid @border_strong@; border-radius: 10px; padding: 8px 16px;
}
QPushButton#Ghost:hover { border-color: @accent@; color: @accent@; background: @accent_soft@; }

QPushButton#GhostSmall {
    background: @card@; color: @text@;
    border: 1px solid @border@; border-radius: 8px; padding: 5px 12px; font-size: 12px;
}
QPushButton#GhostSmall:hover { border-color: @accent@; color: @accent@; background: @accent_soft@; }

QPushButton#Danger {
    background: transparent; color: @red@;
    border: 1px solid @red@; border-radius: 10px; padding: 8px 16px;
}
QPushButton#Danger:hover { background: @red@; color: #ffffff; }

QPushButton#LinkBtn {
    background: transparent; border: none; border-radius: 6px;
    padding: 4px 6px; color: @accent@; font-size: 12px;
}
QPushButton#LinkBtn:hover { color: @accent_hi@; background: @accent_soft@; }

/* ---------- 列表 / 树 ---------- */
QListWidget { background: transparent; border: none; }
QListWidget::item {
    background: transparent; border: none;
    border-radius: 8px; padding: 1px;
}
QListWidget::item:hover { background: @card_hover@; }
QListWidget::item:selected { background: @accent_soft@; border: none; color: @accent@; }

QTreeWidget {
    background: transparent; border: none;
    color: @text@; font-size: 12.5px;
}
QTreeWidget::item { padding: 3px 2px; border-radius: 6px; }
QTreeWidget::item:hover { background: @card_hover@; }
QTreeWidget::item:selected { background: @accent_soft@; color: @accent@; }
QTreeWidget::branch { background: transparent; }

/* ---------- 滚动区域 / 滚动条 ---------- */
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: @border_strong@; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: @text_faint@; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: @border_strong@; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ---------- 对话消息 ---------- */
QFrame#UserBubble {
    background: _GRAD_PRIMARY_;
    border-radius: 16px;
}
/* 用户气泡内的文字统一用白（覆盖 MsgText 默认深色） */
QFrame#UserBubble QLabel { color: #ffffff; background: transparent; }
QLabel#MsgText { font-size: 13px; color: @text@; }
QLabel#SourceText { font-size: 11px; color: @text_faint@; }
QLabel#ThinkText { font-size: 12px; color: @text_faint@; }
QLabel#Badge {
    background: @accent_soft@; color: @accent@; border-radius: 12px;
    padding: 4px 14px; font-size: 12px; font-weight: 700;
}
QLabel#Badge[cText="green"] { background: @green_soft@; color: @green@; }
QLabel#Badge[cText="red"]   { background: @red_soft@; color: @red@; }
QLabel#Badge[cText="amber"] { background: @amber_soft@; color: @amber@; }

/* 笔记保存状态 */
QLabel#NoteStatus {
    font-size: 12px; padding: 3px 10px; border-radius: 10px;
    background: @card_hover@; color: @text_faint@; font-weight: 600;
}
QLabel#NoteStatus[cText="green"] { background: @green_soft@; color: @green@; }
QLabel#NoteStatus[cText="red"]   { background: @red_soft@; color: @red@; }
QLabel#NoteStatus[cText="muted"] { background: @card_hover@; color: @text_faint@; }

/* 输入卡片 */
QFrame#InputCard {
    background: @card@; border: 1px solid @border@; border-radius: 18px;
}
QFrame#InputCard:focus-within { border: 1px solid @accent@; }
QLineEdit#ChatInput, QPlainTextEdit#ChatInput {
    background: transparent; border: none; padding: 10px 12px; font-size: 13px;
}

/* 底部轻提示（替代模态弹窗） */
QLabel#Toast {
    background: @text_hi@; color: @bg@; border-radius: 17px;
    font-size: 12.5px; font-weight: 600; padding: 0 14px;
}

/* ---------- 部署页 ---------- */
QLabel#DeployStatus { font-size: 12px; color: @accent@; }

/* 复选框 */
QCheckBox { spacing: 8px; color: @text@; }
QCheckBox::indicator {
    width: 17px; height: 17px; border: 1px solid @border_strong@;
    border-radius: 5px; background: @input@;
}
QCheckBox::indicator:hover { border-color: @accent@; }
QCheckBox::indicator:checked { background: @accent@; border-color: @accent@; }

QToolTip {
    background: @card@; color: @text@;
    border: 1px solid @border@; padding: 6px;
}
"""


def build_qss(colors: dict) -> str:
    qss = _QSS_TEMPLATE
    # 先替换长占位符 _GRAD_PRIMARY_HI_，再替换短占位符 _GRAD_PRIMARY_，
    # 否则短占位符会先匹配掉长占位符的前缀、破坏 _GRAD_PRIMARY_HI_。
    qss = qss.replace("_GRAD_PRIMARY_HI_", _GRAD_PRIMARY_HI)
    qss = qss.replace("_GRAD_PRIMARY_", _GRAD_PRIMARY)
    for k, v in colors.items():
        qss = qss.replace(f"@{k}@", v)
    return qss


# ---------- 主题同步文件（内嵌工具跟随 LifeSystem 主题） ----------
_SYNC_NAME = ".life_system_theme"


def _sync_path() -> str:
    return os.path.join(os.path.expanduser("~"), _SYNC_NAME)


def read_sync_theme() -> str | None:
    """读取 LifeSystem 写入的主题同步文件（dark/light），不存在返回 None。"""
    try:
        with open(_sync_path(), encoding="utf-8") as f:
            v = f.read().strip()
        return v if v in ("dark", "light") else None
    except Exception:
        return None


class ThemeManager(QObject):
    """负责双主题切换（立即切换，无动画，避免内嵌时额外开销）。"""

    changed = Signal()

    def __init__(self, app):
        super().__init__()
        self._app = app
        self._dark = False
        self._colors = dict(LIGHT)

    @property
    def is_dark(self) -> bool:
        return self._dark

    @property
    def colors(self) -> dict:
        return self._colors

    def get(self, key: str) -> str:
        return self._colors.get(key, "#000000")

    def apply_immediate(self, dark: bool) -> None:
        self._dark = dark
        self._colors = dict(DARK if dark else LIGHT)
        self._app.setStyleSheet(build_qss(self._colors))
        self.changed.emit()

    def toggle(self) -> None:
        self.apply_immediate(not self._dark)


# 全局主题管理器（在 main 中初始化）
manager: ThemeManager | None = None


def get(key: str) -> str:
    if manager is None:
        return LIGHT.get(key, "#000000")
    return manager.get(key)


def md_palette() -> dict:
    """Markdown 渲染取色（跟随当前主题，切换主题后重新渲染即生效）。"""
    c = manager.colors if manager is not None else LIGHT
    return {
        "hi": c["text_hi"],
        "text": c["text"],
        "soft": c["text_soft"],
        "faint": c["text_faint"],
        "accent": c["accent"],
        "link": c["link"],
        "border": c["border"],
        "code_bg": c["card_hover"] if not manager or not manager.is_dark else c["input"],
        "code_fg": c["text_hi"],
    }
