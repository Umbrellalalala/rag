"""RAG 文件助手 —— PySide6 主窗口（三栏布局 + 全部页面）。

三栏：左导航（对话列表）/ 中页面（对话·笔记·找文件·部署·设置）/ 右知识库文件树。
core/ 逻辑（embedding / 索引 / 聊天）保持原样复用，本文件只负责 UI 层。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from shiboken6 import isValid as _qt_alive   # noqa: E402  判断 C++ 侧是否已销毁
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QPlainTextEdit, QSlider, QSizePolicy,
    QStackedWidget, QSystemTrayIcon, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from core.config import (
    CONV_DIR, INDEX_DIR, NOTE_DIR, USER_DIR, atomic_write_json, atomic_write_text,
    dir_size_gb, free_gb, get_api_key, get_base_url, load_config, save_config,
    set_data_home,
)
from core.config import AUTO_PICKED as _AUTO_PICKED
from . import richtext, theme

FONT_FAMILY = "Microsoft YaHei UI"

# 侧边栏两种宽度：展开显示图标+文字，收起只显示图标（与 LifeSystem 同款）
SIDEBAR_WIDTH = 248
SIDEBAR_COLLAPSED_WIDTH = 60

CONV_DIR.mkdir(parents=True, exist_ok=True)
NOTE_DIR.mkdir(parents=True, exist_ok=True)

# ollama pull 的一行进度输出：`pulling 4aa3c8d:  45% of 2.5 GB`
_PULL_PCT = re.compile(r"([0-9]{1,3}(?:\.[0-9]+)?)%")
_PULL_SIZE = re.compile(r"of\s+([0-9.]+\s*[KMGT]?i?B)")


def resource_path(rel: str) -> Path:
    base = getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent.parent
    return Path(base) / rel


# ============================================================
# 对话管理
# ============================================================
def new_conversation() -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "title": "新对话",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "messages": [],
    }


def save_conv(conv: dict) -> None:
    p = CONV_DIR / f"{conv['id']}.json"
    try:
        atomic_write_json(p, conv)
    except OSError:
        pass


def load_all_convs() -> list[dict]:
    convs = []
    for p in CONV_DIR.glob("*.json"):
        try:
            convs.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    convs.sort(key=lambda c: c.get("created_at", ""), reverse=True)
    return convs


class UiCall(QObject):
    """把后台线程要做界面更新丢回主线程。

    Qt 的跨线程信号是排队的，所以后台线程 emit 一定会在主线程执行；
    而 QTimer.singleShot(0, fn) 从没有事件循环的后台线程调用**永远不会触发**
    ——原实现里所有流式 token、索引进度、下载状态都是这么静默丢掉的。
    """

    _fired = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._fired.connect(lambda fn: fn())

    def post(self, fn) -> None:
        self._fired.emit(fn)


class ElidedLabel(QLabel):
    """自动省略号标签：文本过长时按宽度截断，避免撑破布局。"""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self._full = text

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self._apply()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        fm = self.fontMetrics()
        elided = fm.elidedText(self._full, Qt.TextElideMode.ElideRight, max(self.width(), 20))
        if elided != self.text():
            super().setText(elided)


def rel_time(iso: str) -> str:
    """把 ISO 时间串转成「刚刚 / 3小时前 / 5天前 / 2026-03-05」这种人话。"""
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return ""
    delta = (datetime.now() - dt).total_seconds()
    if delta < 0:
        return "刚刚"
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    if delta < 86400 * 30:
        return f"{int(delta // 86400)} 天前"
    return dt.strftime("%Y-%m-%d")


def reveal_in_explorer(path: Path) -> None:
    """在资源管理器中定位该文件（选中它）。"""
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError:
        pass


def open_path_natively(path: Path) -> None:
    """用系统默认程序打开文件 / 文件夹。"""
    try:
        if os.name == "nt":
            os.startfile(str(path))  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def add_file_actions(menu: QMenu, path: Path, win: "MainWindow",
                     with_mention: bool = True) -> None:
    """给右键菜单补齐「引用 / 打开 / 定位 / 复制路径」这一组标准动作。"""
    if with_mention:
        act = QAction("引用到当前对话", menu)
        act.triggered.connect(lambda _=False, p=path: win.insert_mention(p))
        menu.addAction(act)
    menu.addAction(_file_action("打开文件", path, lambda p: open_path_natively(p)))
    menu.addAction(_file_action("在资源管理器中显示", path, lambda p: reveal_in_explorer(p)))
    copy_act = QAction("复制完整路径", menu)
    copy_act.triggered.connect(lambda _=False, p=path: win.toast(f"已复制路径：{p.name}"))
    copy_act.triggered.connect(lambda _=False, p=path: QApplication.clipboard().setText(str(p)))
    menu.addAction(copy_act)


def _file_action(text: str, path: Path, fn) -> QAction:
    act = QAction(text)
    act.triggered.connect(lambda _=False, p=path: fn(p))
    return act


def make_card(title: str = "", hint: str = "") -> tuple[QFrame, QVBoxLayout]:
    """返回 (卡片, 内容布局)。卡片自带标题与说明文案。"""
    card = QFrame()
    card.setObjectName("Card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    if title:
        head = QHBoxLayout()
        head.setSpacing(8)
        t = QLabel(title)
        t.setObjectName("CardTitle")
        head.addWidget(t)
        if hint:
            h = QLabel(hint)
            h.setObjectName("CardHint")
            head.addWidget(h)
        head.addStretch(1)
        lay.addLayout(head)
    return card, lay


class RowFrame(QFrame):
    """列表里的一整行：悬停显示右侧按钮、支持双击与右键菜单。

    对齐 Windows 资源管理器习惯 —— 单击只是选中，双击才执行主操作。
    """

    activated = Signal()          # 双击
    clicked_single = Signal()     # 单击

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover_widgets: list[QWidget] = []
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def add_hover_widget(self, w: QWidget) -> None:
        w.hide()
        self._hover_widgets.append(w)

    def enterEvent(self, event) -> None:  # noqa: N802
        for w in self._hover_widgets:
            w.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        for w in self._hover_widgets:
            w.hide()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.activated.emit()
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked_single.emit()
        super().mousePressEvent(event)


class _ImePlaceholder(QObject):
    """拼音没上屏时把占位符摘掉。

    Qt 把未提交的 preedit 存在控件的文本外面（`toPlainText()` / `text()` 仍是空），
    于是「文本为空才画占位符」的判断漏掉了组合中的那串字母，
    占位符和拼音叠在同一行上看不清在打什么。"""

    def __init__(self, widget, text: str):
        super().__init__(widget)
        self._w = widget
        self._text = text

    def _empty(self) -> bool:
        read = getattr(self._w, "toPlainText", None)
        return not (read() if read is not None else self._w.text())

    def eventFilter(self, obj, ev):  # noqa: ANN001
        if obj is self._w and ev.type() == QEvent.Type.InputMethod:
            if ev.preeditString():
                self._w.setPlaceholderText("")
            elif self._empty():
                self._w.setPlaceholderText(self._text)
        return False


def set_placeholder(widget, text: str) -> None:
    """带占位符的输入框一律走这里：组合期间自动让位给拼音。"""
    widget.setPlaceholderText(text)
    widget.installEventFilter(_ImePlaceholder(widget, text))


class ChatInputEdit(QPlainTextEdit):
    """聊天输入框：Enter 发送、Shift+Enter 换行，随内容 1~6 行自动增高。"""

    submitted = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        set_placeholder(self, "向 RAG 助手提问（Enter 发送，Shift+Enter 换行）；点左侧知识库文件可插入 @引用…")
        self.setObjectName("ChatInput")
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # 高度会随内容自己长，出现滚动条只会让输入框看起来像坏了
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        doc_h = int(self.document().size().height()) + 20
        self.setFixedHeight(min(max(doc_h, 44), 168))



class ConvRow(RowFrame):
    """侧栏「最近对话」的一行。

    对齐 Windows 习惯：单击切换、悬停才出现操作按钮、右键菜单、F2 重命名、
    Delete 删除；常驻的铅笔/垃圾桶图标既吵又容易误点。
    """

    def __init__(self, conv: dict, win: "MainWindow"):
        super().__init__()
        self.conv = conv
        self.win = win
        self.setObjectName("ConvRow")
        self.setProperty("sel", "true" if conv is win.current_conv else "false")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 5, 6, 5)
        lay.setSpacing(4)
        col = QVBoxLayout()
        col.setSpacing(0)
        title = ElidedLabel(conv.get("title") or "新对话")
        title.setObjectName("ConvTitle")
        n = len(conv.get("messages", []))
        parts = [p for p in [rel_time(conv.get("created_at", "")), f"{n} 条" if n else ""] if p]
        sub = QLabel(" · ".join(parts) or "空对话")
        sub.setObjectName("ConvSub")
        col.addWidget(title)
        col.addWidget(sub)
        lay.addLayout(col, 1)

        rename = QPushButton("✎")
        rename.setObjectName("ConvAction")
        rename.setToolTip("重命名（F2）")
        rename.clicked.connect(lambda _=False: win._rename_conv(self.conv))
        delete = QPushButton("✕")
        delete.setObjectName("ConvAction")
        delete.setProperty("danger", "true")
        delete.setToolTip("删除（Delete）")
        delete.clicked.connect(lambda _=False: win._delete_conv(self.conv))
        for w in (rename, delete):
            lay.addWidget(w)
            self.add_hover_widget(w)

        self.clicked_single.connect(lambda: win._switch_conv(self.conv))
        self.customContextMenuRequested.connect(self._menu)

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        a1 = QAction("重命名", menu)
        a1.triggered.connect(lambda _=False: self.win._rename_conv(self.conv))
        a2 = QAction("删除对话", menu)
        a2.triggered.connect(lambda _=False: self.win._delete_conv(self.conv))
        menu.addAction(a1)
        menu.addAction(a2)
        menu.addSeparator()
        a3 = QAction("新建对话", menu)
        a3.triggered.connect(lambda _=False: self.win.new_conv())
        menu.addAction(a3)
        menu.exec(self.mapToGlobal(pos))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_F2:
            self.win._rename_conv(self.conv)
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.win._delete_conv(self.conv)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.win._switch_conv(self.conv)
        else:
            super().keyPressEvent(event)


class MsgBlock(QWidget):
    """一条聊天消息。

    助手回答按 Markdown 渲染（代码块 / 列表 / 加粗不再是裸符号），
    引用来源收进可展开区，悬停出现「复制」，流式输出走缓冲节流重绘。
    """

    def __init__(self, win: "MainWindow", role: str, text: str = "", sources=None):
        super().__init__()
        self.win = win
        self.role = role
        self.raw = text
        self.sources = list(sources or [])
        self._dirty = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.setSpacing(0)

        body = QWidget()
        body.setObjectName("MsgBody")
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(3)

        if role == "user":
            bubble = QFrame()
            bubble.setObjectName("UserBubble")
            bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
            bl = QVBoxLayout(bubble)
            bl.setContentsMargins(16, 11, 16, 11)
            self.lbl = QLabel(text)
            self.lbl.setObjectName("MsgText")
            self.lbl.setWordWrap(True)
            self.lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            bl.addWidget(self.lbl)
            outer.addStretch(1)
            outer.addWidget(bubble)
            outer.addStretch(0)
            return

        # ---- 助手回答 ----
        self.think = QLabel("")
        self.think.setObjectName("ThinkText")
        self.think.hide()
        self._body_layout.addWidget(self.think)

        # 来源条先建好但藏着：流式回答是「先出字、检索结果后到」的，
        # 到齐后用 set_sources() 填进来，不用重建气泡。
        self.src_toggle = QPushButton("📎  引用来源 ▾")
        self.src_toggle.setObjectName("MiniAction")
        self.src_toggle.setCheckable(True)
        self.src_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.src_toggle.toggled.connect(self._toggle_sources)
        self.src_toggle.hide()
        self._body_layout.addWidget(self.src_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.src_panel = QWidget()
        self.src_layout = QVBoxLayout(self.src_panel)
        self.src_layout.setContentsMargins(10, 2, 0, 2)
        self.src_layout.setSpacing(3)
        self.src_panel.hide()
        self._body_layout.addWidget(self.src_panel)
        if self.sources:
            self.set_sources(self.sources)

        self.lbl = QLabel("")
        self.lbl.setObjectName("MsgText")
        self.lbl.setWordWrap(True)
        self.lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.lbl.setOpenExternalLinks(True)
        self.lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._body_layout.addWidget(self.lbl)

        self.copy_btn = QPushButton("📋 复制")
        self.copy_btn.setObjectName("MiniAction")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(lambda: self.win.copy_text(self.raw))
        self.copy_btn.hide()
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(self.copy_btn)
        actions.addStretch(1)
        self._body_layout.addLayout(actions)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        outer.addWidget(body, 1)
        outer.addStretch(0)
        self._render()

    # ---------------- 引用来源 ----------------
    def _source_row(self, s: dict) -> RowFrame:
        row = RowFrame()
        row.setObjectName("SourceRow")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(2)
        head = QLabel(f"📄  {s.get('name') or Path(s.get('file', '')).name}"
                      f"  ·  相关度 {float(s.get('score', 0)):.2f}")
        head.setObjectName("Strong")
        lay.addWidget(head)
        snippet = re.sub(r"\s+", " ", (s.get("text") or "").strip())
        if snippet:
            body = QLabel(snippet[:220] + ("…" if len(snippet) > 220 else ""))
            body.setObjectName("CardHint")
            body.setWordWrap(True)
            lay.addWidget(body)
        path = Path(s.get("file", ""))
        if path.exists():
            row.activated.connect(lambda p=path: open_path_natively(p))
            row.customContextMenuRequested.connect(
                lambda _p, p=path, r=row: self._source_menu(p, _p, r))
        return row

    def _source_menu(self, path: Path, pos, row: RowFrame) -> None:
        menu = QMenu(row)
        add_file_actions(menu, path, self.win)
        menu.exec(row.mapToGlobal(pos))

    def set_sources(self, hits) -> None:
        """检索结果到位后填进来源条（流式回答走这条路，历史消息走 __init__）。"""
        hits = list(hits or [])
        if not hits or self.sources and self.src_layout.count():
            return
        self.sources = hits
        for s in hits:
            self.src_layout.addWidget(self._source_row(s))
        self.src_toggle.setText(f"📎  引用来源 {len(hits)} 段 ▾")
        self.src_toggle.show()

    def _toggle_sources(self, on: bool) -> None:
        self.src_panel.setVisible(on)
        self.src_toggle.setText(
            f"📎  引用来源 {len(self.sources)} 段 {'▴' if on else '▾'}")
        self.win.scroll_chat(force=False)

    # ---------------- 文本渲染 ----------------
    def _alive(self) -> bool:
        """切会话/切主题会重建消息区，后台线程可能还在往已销毁的气泡里灌字。"""
        return _qt_alive(self) and _qt_alive(self.lbl)

    def _render(self) -> None:
        if not self._alive():
            return
        if richtext.plain_or_markdown(self.raw):
            self.lbl.setTextFormat(Qt.TextFormat.RichText)
            self.lbl.setText(richtext.to_html(self.raw, theme.md_palette()))
        else:
            self.lbl.setTextFormat(Qt.TextFormat.PlainText)
            self.lbl.setText(self.raw)

    def set_thinking(self, text: str) -> None:
        if not self._alive():
            return
        self.think.setText(text)
        self.think.setVisible(bool(text))

    def feed(self, piece: str) -> None:
        """流式追加。攒到 90ms 定时器统一重绘，避免每个 token 都重排一次布局。"""
        self.raw += piece
        self._dirty = True

    def flush(self) -> None:
        if self._dirty:
            self._dirty = False
            self._render()
            self.win.scroll_chat(force=False)

    def finish(self, text: str = "", thinking: str = "") -> None:
        if text:
            self.raw = text if not self.raw else (self.raw + text)
        self._dirty = True
        self.flush()
        if thinking:
            self.set_thinking(thinking)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.role == "assistant":
            self.copy_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.role == "assistant":
            self.copy_btn.hide()
        super().leaveEvent(event)


class RenameDialog(QDialog):
    """重命名对话的精致弹窗（替代 tkinter simpledialog 的灰扑扑对话框）。"""

    def __init__(self, current: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("重命名对话")
        self.setFixedWidth(360)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        title = QLabel("重命名对话")
        title.setObjectName("H1")
        root.addWidget(title)

        self.edit = QLineEdit(current)
        set_placeholder(self.edit, "输入新名称…")
        self.edit.setClearButtonEnabled(True)
        self.edit.selectAll()
        root.addWidget(self.edit)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("GhostSmall")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确定")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        root.addLayout(btns)

        self.edit.returnPressed.connect(self.accept)

    def value(self) -> str:
        return self.edit.text().strip()


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):
    status_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RAG 文件助手")
        self.resize(1320, 820)
        self.setMinimumSize(1040, 660)
        self.ui = UiCall(self)   # 后台线程 → 主线程的唯一通道，必须最早建好

        cfg = load_config()
        self.theme = "dark" if cfg.get("theme") == "dark" else "light"
        self._theme_pinned = bool(cfg.get("theme_pinned", False))

        # 状态
        self.embedder = None
        self.indexer = None
        self.assistant = None
        self.conversations: list[dict] = []
        self.current_conv: dict | None = None
        self._deploying = False
        self._quitting = False
        self._loading_settings = False
        self._tree_expand_all = False
        self.index_lock = threading.Lock()
        # 正在生成的回答：用于「停止」按钮与并发提问拦截
        self._answering = False
        self._stop_event = threading.Event()
        # 模型下载：取消标志 + 探测中标志，避免重复触发
        self._deploy_cancel = threading.Event()
        self._deploy_probing = False
        self._indexing = False
        # 流式输出：token 只写进缓冲区，由这个定时器统一重绘
        self._stream_block: MsgBlock | None = None
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(90)
        self._stream_timer.timeout.connect(self._flush_stream)

        # 图标
        icon = resource_path("assets/icon.ico")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        self._build_ui()
        # 恢复上次的侧边栏收起状态
        self._set_sidebar_collapsed(bool(cfg.get("sidebar_collapsed", False)))
        self._setup_shortcuts()

        # 加载对话
        self.conversations = load_all_convs()
        if not self.conversations:
            self.conversations = [new_conversation()]
            save_conv(self.conversations[0])
        self.current_conv = self.conversations[0]
        self._render_conv_list()
        self._render_chat()
        self.refresh_file_tree()

        # 主题同步（跟随 LifeSystem）+ 自动索引
        self._theme_timer = QTimer(self)
        self._theme_timer.timeout.connect(self._theme_sync_loop)
        self._theme_timer.start(1200)
        threading.Thread(target=self._auto_index_loop, daemon=True).start()

        self._setup_tray()
        self._setup_toast()

    def later(self, fn) -> None:
        """从任意线程安全地更新界面；主线程调用时立即执行。"""
        self.ui.post(fn)

    # ============================================================
    # UI 构建
    # ============================================================
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("Root")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左：侧栏
        root.addWidget(self._build_sidebar())

        # 中：页面容器
        self.stack = QStackedWidget()
        self.page_index: dict[str, int] = {}
        self._build_chat_page()
        self._build_notes_page()
        self._build_find_page()
        self._build_deploy_page()
        self._build_settings_page()
        root.addWidget(self.stack, 1)

        # 右：知识库（仅对话页显示）
        self.knowledge = self._build_knowledge()
        root.addWidget(self.knowledge)

        self.show_page("chat")

        # 侧边栏收起/展开手柄：浮在侧栏右缘、上下居中的窄长条
        self._build_sidebar_handle(central)
        self._position_sidebar_btn()

    def _build_sidebar(self) -> QWidget:
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(SIDEBAR_WIDTH)
        lay = QVBoxLayout(self.sidebar)
        lay.setContentsMargins(12, 16, 12, 10)
        lay.setSpacing(6)

        # Logo 区
        logo_row = QHBoxLayout()
        logo_row.setSpacing(10)
        mark = QLabel("R")
        mark.setObjectName("LogoMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_row.addWidget(mark)
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title = QLabel("RAG 文件助手")
        title.setObjectName("AppTitle")
        subtitle = QLabel("本地文件 · 检索增强")
        subtitle.setObjectName("AppSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        logo_row.addLayout(title_col, 1)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("ThemeToggle")
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.clicked.connect(self._toggle_theme)
        logo_row.addWidget(self.theme_btn)
        lay.addLayout(logo_row)
        # 收起侧边栏后需要隐藏的控件（只保留主题按钮，否则无法切换主题）
        self._sidebar_logo = mark
        self._sidebar_labels = [title, subtitle]
        self._update_theme_button()

        # 新建对话
        self.new_btn = QPushButton("＋  新建对话")
        self.new_btn.setObjectName("Primary")
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.clicked.connect(lambda: (self.new_conv(), self.show_page("chat")))
        lay.addWidget(self.new_btn)

        # 导航
        self.nav_buttons: dict[str, QPushButton] = {}
        self._nav_meta: dict[str, tuple[str, str]] = {}
        self._nav_hint = {
            "chat": "对话（Ctrl+N 新建对话）",
            "notes": "笔记（写完自动保存）",
            "find": "找文件（Ctrl+F）",
            "deploy": "模型部署（本地 Ollama 与向量模型）",
            "settings": "设置（Ctrl+,）",
        }
        nav_hints = self._nav_hint
        for page, icon, label in [
            ("chat", "💬", "对话"),
            ("notes", "📝", "笔记"),
            ("find", "🔍", "找文件"),
            ("deploy", "🧩", "模型部署"),
            ("settings", "⚙️", "设置"),
        ]:
            btn = QPushButton(f"{icon}  {label}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(nav_hints[page])
            btn.clicked.connect(lambda _=False, p=page: self.show_page(p))
            self.nav_buttons[page] = btn
            self._nav_meta[page] = (icon, label)
            lay.addWidget(btn)

        # 最近对话
        sec = QLabel("最近对话")
        sec.setObjectName("SideSection")
        self._conv_section = sec
        lay.addWidget(sec)

        self.conv_list = QWidget()
        self.conv_list_layout = QVBoxLayout(self.conv_list)
        self.conv_list_layout.setContentsMargins(0, 0, 0, 0)
        self.conv_list_layout.setSpacing(1)
        self.conv_list_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.conv_list)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._conv_scroll = scroll
        lay.addWidget(scroll, 1)

        return self.sidebar

    # ---------------- 侧边栏收起/展开 ----------------
    def _build_sidebar_handle(self, parent: QWidget) -> None:
        """贴在侧边栏右缘、上下居中的窄长条手柄（与 LifeSystem 同款）。"""
        self._sidebar_collapsed = False
        self.sidebar_btn = QPushButton("‹", parent)
        self.sidebar_btn.setObjectName("SidebarHandle")
        self.sidebar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sidebar_btn.setFixedSize(18, 46)
        self.sidebar_btn.clicked.connect(self.toggle_sidebar)

    def toggle_sidebar(self) -> None:
        """切换侧边栏显示/收起（Ctrl+B），状态写入 config.json。"""
        self._set_sidebar_collapsed(not self._sidebar_collapsed)
        try:
            cfg = load_config()
            cfg["sidebar_collapsed"] = self._sidebar_collapsed
            save_config(cfg)
        except Exception:
            pass

    def _set_sidebar_collapsed(self, collapsed: bool) -> None:
        """统一处理侧边栏收起/展开时的宽度与各控件显示状态。"""
        self._sidebar_collapsed = collapsed
        self.sidebar.setFixedWidth(
            SIDEBAR_COLLAPSED_WIDTH if collapsed else SIDEBAR_WIDTH)
        m = 8 if collapsed else 12
        self.sidebar.layout().setContentsMargins(m, 16, m, 10)

        # Logo / 标题：收起后隐藏（保留主题按钮）
        if getattr(self, "_sidebar_logo", None) is not None:
            self._sidebar_logo.setVisible(not collapsed)
        for w in getattr(self, "_sidebar_labels", []):
            w.setVisible(not collapsed)
        # 最近对话：60px 窄栏放不下，整体隐藏
        for w in (getattr(self, "_conv_section", None),
                  getattr(self, "_conv_scroll", None)):
            if w is not None:
                w.setVisible(not collapsed)

        # 新建对话：收起后只留加号
        if getattr(self, "new_btn", None) is not None:
            self.new_btn.setText("＋" if collapsed else "＋  新建对话")
            self.new_btn.setProperty("collapsed", "true" if collapsed else "false")
            self._repolish(self.new_btn)

        # 导航：收起后只显示图标（悬停提示显示完整名称）
        for page, btn in getattr(self, "nav_buttons", {}).items():
            icon, label = self._nav_meta[page]
            btn.setProperty("collapsed", "true" if collapsed else "false")
            if collapsed:
                btn.setText(icon)
                btn.setToolTip(label)
            else:
                btn.setText(f"{icon}  {label}")
                btn.setToolTip(getattr(self, "_nav_hint", {}).get(page, ""))
            self._repolish(btn)

        btn = getattr(self, "sidebar_btn", None)
        if btn is not None:
            btn.setText("›" if collapsed else "‹")
            btn.setToolTip("展开侧边栏" if collapsed else "收起侧边栏")
        self._position_sidebar_btn()

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        """QSS 动态属性变化后刷新样式，使 [collapsed="true"] 规则生效。"""
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _position_sidebar_btn(self) -> None:
        """手柄吸附在侧边栏右缘，上下居中。"""
        btn = getattr(self, "sidebar_btn", None)
        if btn is None or self.centralWidget() is None:
            return
        w, h = btn.width(), btn.height()
        btn.move(self.sidebar.width() - w // 2,
                 (self.centralWidget().height() - h) // 2)
        btn.raise_()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._position_sidebar_btn()

    def _make_page(self) -> QWidget:
        """创建并登记一个页面，返回其容器。"""
        page = QWidget()
        idx = self.stack.addWidget(page)
        return page

    # ---------------- 对话页 ----------------
    def _build_chat_page(self) -> None:
        page = self._make_page()
        self.page_index["chat"] = self.stack.count() - 1
        lay = QVBoxLayout(page)
        lay.setContentsMargins(28, 16, 28, 14)
        lay.setSpacing(8)

        header = QHBoxLayout()
        self.chat_title = QLabel("新对话")
        self.chat_title.setObjectName("PageTitle")
        header.addWidget(self.chat_title, 1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Meta")
        header.addWidget(self.status_label)
        lay.addLayout(header)

        # 消息区
        self.chat_content = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_content)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(4)
        self.chat_layout.addStretch(1)

        chat_scroll = QScrollArea()
        chat_scroll.setWidgetResizable(True)
        chat_scroll.setWidget(self.chat_content)
        chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(chat_scroll, 1)
        self.chat_scroll = chat_scroll

        # 输入区
        self.input_card = QFrame()
        self.input_card.setObjectName("InputCard")
        ic = QVBoxLayout(self.input_card)
        ic.setContentsMargins(14, 6, 14, 10)
        ic.setSpacing(4)

        self.chat_input = ChatInputEdit()
        self.chat_input.submitted.connect(self.send_message)
        ic.addWidget(self.chat_input)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(8)
        self.model_combo = QComboBox()
        self.model_combo.setFixedWidth(210)
        self.model_combo.currentIndexChanged.connect(self._on_model_change)
        bottom.addWidget(self.model_combo)
        # 检索范围：个人空间里代码片段常占九成，不过滤的话问笔记会被代码行挤掉
        self.scope_combo = QComboBox()
        self.scope_combo.setFixedWidth(170)
        self.scope_combo.addItem("📄 只搜笔记文档", "doc")
        self.scope_combo.addItem("🗂 全部文件（含代码）", "all")
        self.scope_combo.setToolTip(
            "索引里代码和文档都存着，这里只决定提问时去哪一类里检索。\n"
            "问学习/笔记 → 只搜笔记文档；问代码在哪、谁实现了某功能 → 切到全部文件。")
        self.scope_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        want_scope = load_config().get("search_scope", "doc")
        self.scope_combo.setCurrentIndex(0 if want_scope == "doc" else 1)
        self.scope_combo.currentIndexChanged.connect(self._on_scope_change)
        bottom.addWidget(self.scope_combo)
        self.input_hint = QLabel("")
        self.input_hint.setObjectName("Meta")
        bottom.addWidget(self.input_hint, 1)
        self.send_btn = QPushButton("发送 ↗")
        self.send_btn.setObjectName("Primary")
        self.send_btn.setMinimumHeight(40)
        self.send_btn.setMinimumWidth(90)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self.on_send_clicked)
        bottom.addWidget(self.send_btn)
        ic.addLayout(bottom)

        lay.addWidget(self.input_card)
        self._refresh_model_menu()

    # ---------------- 笔记页 ----------------
    def _build_notes_page(self) -> None:
        page = self._make_page()
        self.page_index["notes"] = self.stack.count() - 1
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        left = QFrame()
        left.setObjectName("Sidebar")
        left.setFixedWidth(220)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 16, 10, 10)
        ll.setSpacing(8)
        t = QLabel("笔记")
        t.setObjectName("AppTitle")
        ll.addWidget(t)
        nb = QPushButton("＋  新笔记")
        nb.setObjectName("Primary")
        nb.setCursor(Qt.CursorShape.PointingHandCursor)
        nb.clicked.connect(self._new_note)
        ll.addWidget(nb)

        self.notes_list = QListWidget()
        self.notes_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.notes_list.customContextMenuRequested.connect(self._notes_menu)
        self.notes_list.currentItemChanged.connect(self._on_note_selected)
        self._notes_hint = QLabel("还没有笔记，点上方「新笔记」开始写。\n写下的内容会自动保存在本地。")
        self._notes_hint.setObjectName("Muted")
        self._notes_hint.setWordWrap(True)
        self._notes_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._notes_hint.setContentsMargins(12, 40, 12, 0)
        self._notes_hint.hide()
        ll.addWidget(self.notes_list, 1)
        ll.addWidget(self._notes_hint, 1)
        lay.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(24, 16, 24, 16)
        rl.setSpacing(8)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.note_title_label = QLabel("选择左侧笔记，或新建一篇")
        self.note_title_label.setObjectName("PageTitle")
        header.addWidget(self.note_title_label)
        header.addStretch(1)
        self.note_status = QLabel("")
        self.note_status.setObjectName("NoteStatus")
        header.addWidget(self.note_status)
        rl.addLayout(header)
        self.note_editor = QPlainTextEdit()
        set_placeholder(self.note_editor, "在这里写笔记，支持 Markdown 纯文本…")
        self.note_editor.textChanged.connect(self._note_dirty)
        rl.addWidget(self.note_editor, 1)
        lay.addWidget(right, 1)
        self._current_note: Path | None = None
        # 每敲一个字就落盘一次既费 IO 也容易在断电时留下半截文件，
        # 统一走 600ms 防抖定时器。
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(600)
        self._note_timer.timeout.connect(self._save_current_note)

    # ---------------- 找文件页 ----------------
    def _build_find_page(self) -> None:
        page = self._make_page()
        self.page_index["find"] = self.stack.count() - 1
        lay = QVBoxLayout(page)
        lay.setContentsMargins(28, 16, 28, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        t = QLabel("找文件")
        t.setObjectName("PageTitle")
        head.addWidget(t)
        self.find_count = QLabel("")
        self.find_count.setObjectName("Meta")
        head.addWidget(self.find_count, 1)
        self.find_cancel_btn = QPushButton("✕ 取消搜索")
        self.find_cancel_btn.setObjectName("DangerSmall")
        self.find_cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.find_cancel_btn.clicked.connect(self._cancel_find)
        self.find_cancel_btn.hide()
        head.addWidget(self.find_cancel_btn)
        lay.addLayout(head)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.find_entry = QLineEdit()
        set_placeholder(self.find_entry, "输入文件名或内容关键字，边输入边搜（回车立即搜）…")
        self.find_entry.setClearButtonEnabled(True)
        self.find_entry.returnPressed.connect(lambda: self._do_find(force=True))
        self.find_entry.textChanged.connect(self._find_debounce)
        search_row.addWidget(self.find_entry, 1)
        self.find_content_check = QCheckBox("搜内容")
        self.find_content_check.setToolTip(
            "勾选后同时搜索文本文件正文的前 4000 字节（只覆盖可索引的文件类型，速度明显变慢）")
        self.find_content_check.toggled.connect(lambda _=False: self._do_find(force=True))
        search_row.addWidget(self.find_content_check)
        lay.addLayout(search_row)

        self.find_results = QWidget()
        self.find_results_layout = QVBoxLayout(self.find_results)
        self.find_results_layout.setContentsMargins(0, 0, 0, 0)
        self.find_results_layout.setSpacing(2)
        self.find_results_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.find_results)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(scroll, 1)
        hint = QLabel("输入关键字即可搜索个人空间；双击结果引用到对话，右键可打开文件或定位到文件夹")
        hint.setObjectName("Muted")
        self.find_results_layout.insertWidget(0, hint)

        # 输入停顿 350ms 再搜，避免每敲一个字就全量扫盘
        self._find_timer = QTimer(self)
        self._find_timer.setSingleShot(True)
        self._find_timer.setInterval(350)
        self._find_timer.timeout.connect(self._do_find)
        self._find_seq = 0

    # ---------------- 部署页 ----------------
    def _build_deploy_page(self) -> None:
        from core.models_catalog import OLLAMA_MODELS
        page = self._make_page()
        self.page_index["deploy"] = self.stack.count() - 1
        self._ollama_catalog = OLLAMA_MODELS
        lay = QVBoxLayout(page)
        lay.setContentsMargins(28, 16, 28, 12)
        lay.setSpacing(10)

        head = QHBoxLayout()
        t = QLabel("模型部署")
        t.setObjectName("PageTitle")
        head.addWidget(t)
        self.deploy_status_label = QLabel("")
        self.deploy_status_label.setObjectName("DeployStatus")
        self.deploy_status_label.setWordWrap(True)
        head.addWidget(self.deploy_status_label, 1)
        self.deploy_check_btn = QPushButton("↻  重新检测")
        self.deploy_check_btn.setObjectName("GhostSmall")
        self.deploy_check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.deploy_check_btn.clicked.connect(lambda: self.refresh_deploy_status(force=True))
        head.addWidget(self.deploy_check_btn)
        lay.addLayout(head)

        prog_row = QHBoxLayout()
        prog_row.setSpacing(8)
        self.deploy_progress = QProgressBar()
        self.deploy_progress.setRange(0, 100)
        self.deploy_progress.setValue(0)
        self.deploy_progress.setTextVisible(True)
        self.deploy_progress.setFixedHeight(16)
        self.deploy_progress.hide()
        prog_row.addWidget(self.deploy_progress, 1)
        self.deploy_cancel_btn = QPushButton("✕ 取消")
        self.deploy_cancel_btn.setObjectName("DangerSmall")
        self.deploy_cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.deploy_cancel_btn.clicked.connect(self._cancel_deploy)
        self.deploy_cancel_btn.hide()
        prog_row.addWidget(self.deploy_cancel_btn)
        lay.addLayout(prog_row)

        self.deploy_content = QWidget()
        self.deploy_layout = QVBoxLayout(self.deploy_content)
        self.deploy_layout.setContentsMargins(0, 0, 0, 0)
        self.deploy_layout.setSpacing(10)
        self.deploy_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.deploy_content)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(scroll, 1)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

    # ---------------- 设置页 ----------------
    def _build_settings_page(self) -> None:
        from core.models_catalog import EMBEDDING_MODELS, OLLAMA_MODELS, CLOUD_MODELS
        page = self._make_page()
        self.page_index["settings"] = self.stack.count() - 1
        cfg = load_config()

        lay = QVBoxLayout(page)
        lay.setContentsMargins(28, 16, 28, 12)
        lay.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel("设置")
        t.setObjectName("PageTitle")
        head.addWidget(t)
        self.settings_saved = QLabel("")
        self.settings_saved.setObjectName("NoteStatus")
        self.settings_saved.hide()
        head.addWidget(self.settings_saved)
        head.addStretch(1)
        lay.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(scroll, 1)
        content = QWidget()
        scroll.setWidget(content)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        # 表单限宽：拉满 1600px 的输入框既难看又难读
        col = QHBoxLayout()
        col.setSpacing(0)
        self.settings_layout = QVBoxLayout()
        self.settings_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_layout.setSpacing(12)
        self.settings_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        col.addLayout(self.settings_layout)
        col.addStretch(1)
        outer.addLayout(col)
        outer.addStretch(1)

        def field(form, text, widget, hint: str = "", max_w: int = 460) -> QWidget:
            row_w = QWidget()
            row = QHBoxLayout(row_w)
            row.setSpacing(12)
            row.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(text)
            lbl.setObjectName("FieldLabel")
            lbl.setFixedWidth(120)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            row.addWidget(lbl)
            sub = QVBoxLayout()
            sub.setSpacing(2)
            widget.setMaximumWidth(max_w)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            sub.addWidget(widget)
            if hint:
                h = QLabel(hint)
                h.setObjectName("CardHint")
                h.setWordWrap(True)
                h.setMaximumWidth(max_w)
                sub.addWidget(h)
            row.addLayout(sub, 1)
            row.addStretch(1)
            form.addWidget(row_w)
            return row_w

        self._loading_settings = True

        # ---------- 卡片：模型与索引 ----------
        card1, f1 = make_card("模型与索引", "决定文件怎么被向量化和检索")
        self.emb_combo = QComboBox()
        emb_names = [m["id"] for m in EMBEDDING_MODELS]
        self.emb_combo.addItems(emb_names)
        cur_emb = cfg["embedding_model"] if cfg["embedding_model"] in emb_names else emb_names[0]
        self.emb_combo.setCurrentText(cur_emb)
        self.emb_combo.currentIndexChanged.connect(self._save_settings)
        field(f1, "Embedding 模型", self.emb_combo,
              "更换模型后需要重建索引，否则新旧向量维度不一致、检索会失效")

        self.device_combo = QComboBox()
        for name in ["自动（优先 GPU）", "GPU（CUDA）", "仅 CPU"]:
            self.device_combo.addItem(name)
        device_map = {"自动（优先 GPU）": "auto", "GPU（CUDA）": "cuda", "仅 CPU": "cpu"}
        self._device_map = device_map
        cur_dev = cfg.get("embedding_device", "auto")
        self.device_combo.setCurrentText(
            next((k for k, v in device_map.items() if v == cur_dev), "自动（优先 GPU）"))
        self.device_combo.currentIndexChanged.connect(self._save_settings)
        field(f1, "计算设备", self.device_combo, "未装 CUDA 时会自动回落到 CPU，功能不受影响")

        path_row = QWidget()
        pr = QHBoxLayout(path_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(6)
        self.models_dir_edit = QLineEdit(cfg.get("models_dir", r"D:\RAGAssistant\models"))
        self.models_dir_edit.textChanged.connect(self._save_settings)
        browse = QPushButton("浏览…")
        browse.setObjectName("GhostSmall")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self._browse_models_dir)
        pr.addWidget(self.models_dir_edit, 1)
        pr.addWidget(browse)
        field(f1, "模型下载路径", path_row, "embedding 模型将下载/缓存到此目录", max_w=620)

        slider_row = QWidget()
        sr = QHBoxLayout(slider_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(10)
        self.topk_slider = QSlider(Qt.Orientation.Horizontal)
        self.topk_slider.setRange(1, 10)
        self.topk_slider.setValue(cfg["top_k"])
        self.topk_slider.valueChanged.connect(self._on_topk_change)
        self.topk_label = QLabel(f"{cfg['top_k']} 个片段")
        self.topk_label.setObjectName("FieldLabel")
        self.topk_label.setFixedWidth(70)
        sr.addWidget(self.topk_slider, 1)
        sr.addWidget(self.topk_label)
        field(f1, "检索片段数", slider_row, "每次提问带进上下文的文件片段数量，越大越全但越慢")

        self.auto_index_check = QCheckBox("每 60 秒自动检测文件夹变化并增量索引")
        self.auto_index_check.setChecked(cfg.get("auto_index", True))
        self.auto_index_check.toggled.connect(self._save_settings)
        f1.addWidget(self.auto_index_check)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("RowSep")
        f1.addWidget(sep)

        idx_row = QHBoxLayout()
        self.index_state_label = QLabel("")
        self.index_state_label.setObjectName("CardHint")
        self.index_state_label.setWordWrap(True)
        idx_row.addWidget(self.index_state_label, 1)
        self.rebuild_btn = QPushButton("⚡ 构建 / 更新索引")
        self.rebuild_btn.setObjectName("GhostSmall")
        self.rebuild_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rebuild_btn.clicked.connect(self._build_index)
        idx_row.addWidget(self.rebuild_btn)
        wipe = QPushButton("清空索引")
        wipe.setObjectName("DangerSmall")
        wipe.setCursor(Qt.CursorShape.PointingHandCursor)
        wipe.clicked.connect(self._clear_index)
        idx_row.addWidget(wipe)
        f1.addLayout(idx_row)
        self.settings_layout.addWidget(card1)

        # ---------- 卡片：对话后端 ----------
        card2, f2 = make_card("对话后端", "决定谁来生成回答")
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("本地 Ollama")
        self.provider_combo.addItem("云端 API")
        self.provider_combo.setCurrentText(
            "本地 Ollama" if cfg.get("chat_provider") == "ollama" else "云端 API")
        self.provider_combo.currentIndexChanged.connect(self._on_provider_change)
        field(f2, "后端类型", self.provider_combo)

        self.settings_model_combo = QComboBox()
        self._cloud_labels = [self._model_label(m) for m in CLOUD_MODELS]
        self._ollama_labels = [self._model_label(m) for m in OLLAMA_MODELS]
        self._cloud_map = {self._model_label(m): m["id"] for m in CLOUD_MODELS}
        self._ollama_map = {self._model_label(m): m["id"] for m in OLLAMA_MODELS}
        self.settings_model_combo.currentIndexChanged.connect(self._save_settings)
        field(f2, "Chat 模型", self.settings_model_combo,
              "列表里没有的模型：先在「模型部署」页下载，或直接在服务地址里填自定义端点")

        self.base_edit = QLineEdit(cfg.get("chat_base_url", ""))
        set_placeholder(self.base_edit, "留空即可：本地 Ollama 用 localhost:11434")
        self.base_edit.textChanged.connect(self._save_settings)
        field(f2, "服务地址", self.base_edit, "云端：自定义 API 地址；本地 Ollama：留空用默认端口")

        key_row = QWidget()
        kr = QHBoxLayout(key_row)
        kr.setContentsMargins(0, 0, 0, 0)
        kr.setSpacing(6)
        self.key_edit = QLineEdit(cfg.get("chat_api_key", ""))
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        set_placeholder(self.key_edit, "仅使用云端 API 时需要")
        self.key_edit.textChanged.connect(self._save_settings)
        self.key_reveal = QCheckBox("显示")
        self.key_reveal.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        kr.addWidget(self.key_edit, 1)
        kr.addWidget(self.key_reveal)
        self._key_field = field(f2, "API Key", key_row, "只保存在本机配置文件中，不会上传")
        self.settings_layout.addWidget(card2)

        # ---------- 卡片：数据存储位置 ----------
        card3, f3 = make_card("数据存储位置", "对话、笔记和向量索引都存在这里")
        loc = QHBoxLayout()
        loc.setSpacing(6)
        self.data_dir_label = ElidedLabel(str(USER_DIR))
        self.data_dir_label.setObjectName("Strong")
        loc.addWidget(self.data_dir_label, 1)
        openb = QPushButton("打开文件夹")
        openb.setObjectName("GhostSmall")
        openb.setCursor(Qt.CursorShape.PointingHandCursor)
        openb.clicked.connect(lambda: open_path_natively(USER_DIR))
        self.move_dir_btn = QPushButton("更改位置…")
        self.move_dir_btn.setObjectName("GhostSmall")
        self.move_dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.move_dir_btn.clicked.connect(self._move_data_home)
        loc.addWidget(openb)
        loc.addWidget(self.move_dir_btn)
        f3.addLayout(loc)
        self.data_dir_hint = QLabel("")
        self.data_dir_hint.setObjectName("CardHint")
        self.data_dir_hint.setWordWrap(True)
        f3.addWidget(self.data_dir_hint)
        self.settings_layout.addWidget(card3)

        self._on_provider_change()
        self._refresh_index_state()
        self._loading_settings = False

        # 逐字符写盘太浪费，统一 400ms 防抖后一次落盘
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(400)
        self._settings_timer.timeout.connect(self._save_settings_now)

    # ---------------- 知识库面板 ----------------
    def _build_knowledge(self) -> QWidget:
        kb = QFrame()
        kb.setObjectName("Sidebar")
        kb.setFixedWidth(296)
        lay = QVBoxLayout(kb)
        lay.setContentsMargins(14, 16, 14, 10)
        lay.setSpacing(6)

        head = QHBoxLayout()
        t = QLabel("知识库")
        t.setObjectName("AppTitle")
        head.addWidget(t, 1)
        refresh = QPushButton("⟳")
        refresh.setObjectName("GhostSmall")
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.clicked.connect(lambda: self.refresh_file_tree())
        head.addWidget(refresh)
        lay.addLayout(head)

        # 个人空间
        space_row = QHBoxLayout()
        cfg = load_config()
        sp_name = Path(cfg.get("personal_space", "")).name or "未设置"
        self.space_label = ElidedLabel(f"📂  {sp_name}")
        self.space_label.setObjectName("Muted")
        space_row.addWidget(self.space_label, 1)
        choose = QPushButton("选择个人空间")
        choose.setObjectName("GhostSmall")
        choose.setCursor(Qt.CursorShape.PointingHandCursor)
        choose.clicked.connect(self.choose_space)
        space_row.addWidget(choose)
        lay.addLayout(space_row)

        build_btn = QPushButton("⚡  构建 / 更新索引")
        build_btn.setObjectName("GhostSmall")
        build_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        build_btn.clicked.connect(self._build_index)
        self.kb_build_btn = build_btn
        lay.addWidget(build_btn)

        self.kb_progress = QProgressBar()
        self.kb_progress.setRange(0, 1000)
        self.kb_progress.setFixedHeight(12)
        self.kb_progress.setTextVisible(False)
        self.kb_progress.hide()
        lay.addWidget(self.kb_progress)

        # 计数 + 展开/收起
        count_row = QHBoxLayout()
        self.kb_count_label = QLabel("")
        self.kb_count_label.setObjectName("Meta")
        count_row.addWidget(self.kb_count_label, 1)
        expand = QPushButton("展开全部")
        expand.setObjectName("LinkBtn")
        expand.setCursor(Qt.CursorShape.PointingHandCursor)
        expand.clicked.connect(lambda: self.refresh_file_tree(expand_all=True))
        collapse = QPushButton("收起")
        collapse.setObjectName("LinkBtn")
        collapse.setCursor(Qt.CursorShape.PointingHandCursor)
        collapse.clicked.connect(lambda: self.refresh_file_tree(expand_all=False))
        count_row.addWidget(expand)
        count_row.addWidget(collapse)
        lay.addLayout(count_row)

        # 搜索
        self.file_search = QLineEdit()
        set_placeholder(self.file_search, "搜索文件名…")
        self.file_search.setClearButtonEnabled(True)
        lay.addWidget(self.file_search)

        # 文件树
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.itemExpanded.connect(self._on_tree_expand)
        self.tree.itemExpanded.connect(lambda it: self._set_dir_glyph(it, True))
        self.tree.itemCollapsed.connect(lambda it: self._set_dir_glyph(it, False))
        self.tree.itemDoubleClicked.connect(self._on_tree_item_activated)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        lay.addWidget(self.tree, 1)

        tip = QLabel("单击选中 · 双击引用到对话 · 右键打开文件")
        tip.setObjectName("CardHint")
        lay.addWidget(tip)

        # 文件名过滤防抖：每敲一个字就 rglob 整棵目录树会直接卡住界面
        self._tree_filter_timer = QTimer(self)
        self._tree_filter_timer.setSingleShot(True)
        self._tree_filter_timer.setInterval(300)
        self._tree_filter_timer.timeout.connect(self._filter_tree)
        self.file_search.textChanged.connect(lambda _=False: self._tree_filter_timer.start())

        return kb

    # ============================================================
    # 页面切换
    # ============================================================
    def show_page(self, name: str) -> None:
        if name not in self.page_index:
            return
        self.stack.setCurrentIndex(self.page_index[name])
        self.knowledge.setVisible(name == "chat")
        for p, btn in self.nav_buttons.items():
            btn.setChecked(p == name)
        if name == "chat":
            self._refresh_model_menu()
        elif name == "notes":
            self.refresh_notes_list()
        elif name == "find":
            self.find_entry.setFocus()
        elif name == "deploy":
            self.refresh_deploy_status()
        elif name == "settings":
            self._refresh_index_state()
            self._refresh_data_home()

    # ============================================================
    # 对话
    # ============================================================
    def new_conv(self):
        conv = new_conversation()
        self.conversations.insert(0, conv)
        save_conv(conv)
        self.current_conv = conv
        self._render_conv_list()
        self._render_chat()
        self.chat_input.setFocus()

    def _render_conv_list(self):
        self._clear_layout(self.conv_list_layout)
        # _clear_layout 会连带清掉 __init__ 里加的 addStretch(1)，这里必须补回，
        # 否则 QScrollArea widgetResizable 会把视口多余空间平均分给每一行，
        # 导致对话项之间出现大片空隙。
        self.conv_list_layout.addStretch(1)
        for conv in self.conversations[:80]:
            row = ConvRow(conv, self)
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, row)
        if len(self.conversations) > 80:
            more = QLabel(f"另有 {len(self.conversations) - 80} 个更早的对话")
            more.setObjectName("CardHint")
            more.setContentsMargins(9, 4, 9, 4)
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, more)

    def _switch_conv(self, conv: dict):
        if conv is self.current_conv:
            return
        self.current_conv = conv
        self._render_conv_list()
        self._render_chat()

    def _rename_conv(self, conv: dict):
        dlg = RenameDialog(conv.get("title", ""), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new = dlg.value()
            if new:
                conv["title"] = new
                save_conv(conv)
                self._render_conv_list()
                if self.current_conv is conv:
                    self.chat_title.setText(new or "新对话")

    def _delete_conv(self, conv: dict):
        title = conv.get("title", "新对话")
        n = len(conv.get("messages", []))
        box = QMessageBox(self)
        box.setWindowTitle("删除对话")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"确定删除「{title}」？")
        box.setInformativeText(
            f"该对话有 {n} 条消息，删除后无法恢复。" if n else "删除后无法恢复。")
        yes = box.addButton("删除", QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)   # 默认落在「取消」，回车不会误删
        box.exec()
        if box.clickedButton() is not yes:
            return
        p = CONV_DIR / f"{conv['id']}.json"
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
        if conv in self.conversations:
            self.conversations.remove(conv)
        if self.current_conv is conv:
            self.current_conv = self.conversations[0] if self.conversations else None
        if not self.conversations:
            self.conversations = [new_conversation()]
            save_conv(self.conversations[0])
            self.current_conv = self.conversations[0]
        self._render_conv_list()
        self._render_chat()
        self.toast("已删除对话")

    def _reset_chat_area(self) -> None:
        """清空消息区并补回尾部 stretch（否则 insertWidget 会算出负索引）。"""
        self._clear_layout(self.chat_layout)
        self.chat_layout.addStretch(1)

    def _insert_block(self, w: QWidget) -> None:
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, w)
        self.scroll_chat(force=True)

    def _render_chat(self):
        self._reset_chat_area()
        if not self.current_conv:
            return
        title = self.current_conv.get("title", "新对话")
        self.chat_title.setText(title or "新对话")
        msgs = self.current_conv.get("messages", [])
        if not msgs:
            self._render_welcome()
            return
        for msg in msgs:
            self._render_msg(msg)
        QTimer.singleShot(30, lambda: self.scroll_chat(force=True))

    def scroll_chat(self, force: bool = True) -> None:
        """滚到底部。force=False 时只在用户本来就贴着底部才滚，
        免得模型一边输出、用户一边往回翻却被不断拽走。"""
        sb = self.chat_scroll.verticalScrollBar()
        if not force and sb.value() < sb.maximum() - 90:
            return
        sb.setValue(sb.maximum())

    def _render_welcome(self):
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 40, 0, 0)
        wl.setSpacing(10)
        wl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        badge = QLabel("AI 优先的个人知识库")
        badge.setObjectName("Badge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.addWidget(badge)

        big = QLabel("让本地文件开口回答")
        big.setObjectName("H1")
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.addWidget(big)

        sub = QLabel("把你的文件夹变成可问答的知识库，回答自带引用来源")
        sub.setObjectName("Muted")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.addWidget(sub)

        cfg = load_config()
        checklist, self._welcome_actions = self._welcome_checklist(cfg)
        for line in checklist:
            lbl = QLabel(line)
            lbl.setObjectName("Strong")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            wl.addWidget(lbl)

        if self._welcome_actions:
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 0)
            row.setAlignment(Qt.AlignmentFlag.AlignCenter)
            for text, slot in self._welcome_actions:
                b = QPushButton(text)
                b.setObjectName("GhostSmall")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(slot)
                row.addWidget(b)
            wl.addLayout(row)

        if not self._welcome_actions:
            tip = QLabel("试试这样问：")
            tip.setObjectName("Muted")
            tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            wl.addWidget(tip)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setAlignment(Qt.AlignmentFlag.AlignCenter)
            for s in ["总结这个文件夹里的核心内容", "我的空间里有哪些文档？", "帮我找到最近修改的笔记"]:
                b = QPushButton(f"💬  {s}")
                b.setObjectName("GhostSmall")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, t=s: self._use_suggestion(t))
                row.addWidget(b)
            wl.addLayout(row)

        self.chat_layout.insertWidget(0, wrap)

    def _welcome_checklist(self, cfg: dict) -> tuple[list[str], list[tuple[str, object]]]:
        """首屏三步检查清单：缺哪步就给哪步的按钮，别让用户自己找。"""
        _has_space, _name, index_ready, index_count, model_ready, model_desc = \
            self._welcome_state(cfg)
        lines: list[str] = []
        actions: list[tuple[str, object]] = []
        if _has_space:
            lines.append(f"① ✓ 个人空间：{_name}")
        else:
            lines.append("① 还没选择个人空间（要检索哪个文件夹）")
            actions.append(("①  选择个人空间", self.choose_space))
        if index_ready:
            lines.append(f"② ✓ 索引已就绪（{index_count} 个片段）")
        elif _has_space:
            lines.append("② 还没有索引，构建后才能问答")
            actions.append(("②  构建索引", self._build_index))
        else:
            lines.append("② 索引：待构建")
        if model_ready:
            lines.append(f"③ ✓ 对话模型：{model_desc}")
        else:
            lines.append(f"③ 对话模型未就绪：{model_desc}")
            actions.append(("③  配置对话模型", lambda: self.show_page("settings")))
        return lines, actions

    def _welcome_state(self, cfg: dict):
        space = cfg.get("personal_space", "")
        has_space = bool(space)
        space_name = Path(space).name if space else ""
        index_ready = False
        index_count = 0
        try:
            meta = INDEX_DIR / "metadata.json"
            if meta.exists():
                data = json.loads(meta.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    index_count = len(data)
                    index_ready = index_count > 0
        except Exception:
            pass
        provider = cfg.get("chat_provider", "ollama")
        if provider == "openai":
            key = get_api_key(cfg.get("cloud_provider", "deepseek")) or cfg.get("chat_api_key")
            model_ready = bool(key)
            model_desc = "已接入云端 API" if model_ready else "云端 API 缺少 Key，点这里补上"
        else:
            model_ready = True
            model_desc = f"本地 Ollama · {cfg.get('chat_model', 'qwen2.5:3b')}"
        return has_space, space_name, index_ready, index_count, model_ready, model_desc

    def _use_suggestion(self, text: str):
        self.chat_input.setPlainText(text)
        self.chat_input.setFocus()

    def _model_label(self, m) -> str:
        if "size_gb" in m:
            return f"{m['id']} · 约{m['size_gb']}GB"
        return f"{m['id']} · 云端"

    def _refresh_model_menu(self):
        from core.models_catalog import OLLAMA_MODELS, CLOUD_MODELS
        cfg = load_config()
        provider = cfg.get("chat_provider", "ollama")
        models = OLLAMA_MODELS if provider == "ollama" else CLOUD_MODELS
        self._chat_model_map = {self._model_label(m): m["id"] for m in models}
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(list(self._chat_model_map.keys()))
        cur_id = cfg.get("chat_model", "")
        cur = next((d for d, mid in self._chat_model_map.items() if mid == cur_id), None)
        if cur:
            self.model_combo.setCurrentText(cur)
        self.model_combo.blockSignals(False)

    def _on_scope_change(self, _index):
        """切换检索范围只影响「去哪一类片段里查」，索引本身不动。"""
        scope = self.scope_combo.currentData() or "doc"
        cfg = load_config()
        if cfg.get("search_scope") == scope:
            return
        cfg["search_scope"] = scope
        save_config(cfg)
        self.assistant = None
        self.toast("提问时只在笔记/文档里检索" if scope == "doc"
                   else "提问时全量检索（含代码等所有文件）")

    def _on_model_change(self, _index):
        mid = getattr(self, "_chat_model_map", {}).get(self.model_combo.currentText(), "")
        if not mid:
            return
        cfg = load_config()
        cfg["chat_model"] = mid
        from core.models_catalog import get_ollama_model, get_cloud_model
        if get_ollama_model(mid):
            cfg["chat_provider"] = "ollama"
        elif get_cloud_model(mid):
            cfg["chat_provider"] = "openai"
            cfg["cloud_provider"] = get_cloud_model(mid)["provider"]
        save_config(cfg)
        self.assistant = None

    def _render_msg(self, msg: dict):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            self._insert_block(MsgBlock(self, "user", content))
        elif role == "assistant":
            thinking = msg.get("thinking") or {}
            block = MsgBlock(self, "assistant", content, sources=msg.get("sources"))
            block.set_thinking("⊙  " + (thinking.get("thought") or "已检索个人空间"))
            self._insert_block(block)

    _SEND_HELP = ("⚠️ 助手未就绪。请依次确认：\n"
                  "1. 已在侧栏选择个人空间，并点「构建 / 更新索引」；\n"
                  "2. 已在「设置」中配置对话后端：本地 Ollama（可到「模型部署」一键部署）"
                  "或云端 API（填写 Key）。")

    def on_send_clicked(self):
        if self._answering:
            self.stop_answer()
        else:
            self.send_message()

    def stop_answer(self):
        self._stop_event.set()
        self.send_btn.setEnabled(False)
        self.input_hint.setText("正在停止…")

    def _set_answering_ui(self, answering: bool):
        self._answering = answering
        self.send_btn.setText("■  停止" if answering else "发送 ↗")
        self.send_btn.setObjectName("DangerSmall" if answering else "Primary")
        self.send_btn.setEnabled(True)
        self._repolish(self.send_btn)
        self.input_hint.setText("回答生成中，可点「停止」中断" if answering else "")
        if answering:
            self._stream_timer.start()
        else:
            self._stream_timer.stop()
            self._stream_block = None

    def _flush_stream(self):
        if self._stream_block is not None:
            self._stream_block.flush()

    def send_message(self):
        if self._answering:
            self.toast("正在回答中，点右下角「停止」可以先中断")
            return
        q = self.chat_input.toPlainText().strip()
        if not q:
            return
        self.chat_input.clear()
        if not self.current_conv:
            self.new_conv()
        conv = self.current_conv
        if not conv.get("messages"):
            self._reset_chat_area()
        self._insert_block(MsgBlock(self, "user", q))
        conv["messages"].append({"role": "user", "content": q})
        if conv.get("title") in ("新对话", "") and len(conv["messages"]) == 1:
            conv["title"] = q[:30]
            self._render_conv_list()
            self.chat_title.setText(conv["title"])
        save_conv(conv)

        block = MsgBlock(self, "assistant", "")
        block.set_thinking("⊙  正在检索知识库…")
        self._insert_block(block)
        self._stream_block = block
        self._set_answering_ui(True)
        self._stop_event.clear()

        def worker():
            assistant = self._get_assistant()
            if assistant is None:
                self.later(lambda: self._end_answer(block, conv, self._SEND_HELP, None))
                return
            time.sleep(0.3)
            try:
                history = [{"role": m["role"], "content": m["content"]}
                           for m in conv["messages"][:-1]]
                hits, stream = assistant.answer_stream(q, history=history)
                n = len(hits or [])
                self.later(lambda: block.set_sources(hits or []))
                self.later(lambda: block.set_thinking(
                    f"⊙  检索到 {n} 段相关内容，正在生成回答…" if n else "⊙  未检索到相关内容，正在生成回答…"))
                for piece in stream:
                    if self._stop_event.is_set():
                        break
                    self.later(lambda p=piece, b=block: b.feed(p))
                try:
                    stream.close()
                except Exception:
                    pass
                self.later(lambda: self._end_answer(block, conv, "", hits,
                                                    stopped=self._stop_event.is_set()))
            except Exception as exc:
                # `except ... as e` 在块尾会删掉 e，而 self.later 是延后执行的 ——
                # 直接闭包引用它会在真正跑起来时报 NameError，等于把错误提示换成崩溃。
                self.later(lambda exc=exc: self._end_answer(
                    block, conv, self._friendly_chat_error(exc), None))

        threading.Thread(target=worker, daemon=True).start()

    def _end_answer(self, block: MsgBlock, conv: dict, extra: str,
                    hits, stopped: bool = False) -> None:
        """收尾：填来源 + 落盘 + 恢复发送按钮（主线程调用）。"""
        block.set_sources(hits or [])
        block.finish(text=extra, thinking=("⊙  已停止生成" if stopped else
                                           f"⊙  已检索个人空间 · 参考 {len(hits or [])} 段"))
        if block.raw and (conv.get("messages", []) and conv["messages"][-1].get("role") == "user"):
            conv["messages"].append({
                "role": "assistant", "content": block.raw,
                "sources": hits or [],
                "thinking": {"thought": "已停止生成" if stopped else "已检索个人空间",
                             "tools": [{"name": "search"}, {"name": "read"}] if hits else [{"name": "search"}]},
            })
            save_conv(conv)
        if stopped:
            self._render_conv_list()
        self._set_answering_ui(False)
        self.scroll_chat(force=False)
        self.chat_input.setFocus()

    def _friendly_chat_error(self, e) -> str:
        s = str(e)
        low = s.lower()
        model = load_config().get("chat_model", "")
        # 状态码要先判：错误文本里通常带着 http://localhost:11434/... 这个 URL，
        # 先匹配「11434」会把 404 误报成「连不上服务」。
        if any(k in low for k in ("api key", "401", "unauthorized", "authentication", "invalid key")):
            return "⚠️ API 密钥无效或未填写，请在「设置 → 对话后端」里检查 API Key。"
        if "404" in low or ("model" in low and any(k in low for k in ("not found", "not exist", "no such"))):
            return (f"⚠️ 模型 `{model}` 在服务端不存在（404）。"
                    f"到「模型部署」页下载它，或在「设置」里换成已经装好的模型。")
        if "429" in low or "rate limit" in low:
            return "⚠️ 请求太频繁被限流了（429），稍等几秒再问一次。"
        if any(k in low for k in ("connection", "connect", "refused", "11434", "timed out")):
            return ("⚠️ 连不上模型服务。本地 Ollama 请到「模型部署」页点「启动服务」；"
                    "云端 API 请检查服务地址是否写对。")
        if "timeout" in low:
            return "⚠️ 模型响应超时。本地小模型首次加载会慢，稍后再问一次试试；反复超时可换更小的模型。"
        return f"⚠️ 出错了：{s[:200]}"

    # ============================================================
    # 轻提示 / 复制
    # ============================================================
    def _setup_toast(self) -> None:
        self._toast = QLabel("", self)
        self._toast.setObjectName("Toast")
        self._toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast.hide)

    def toast(self, text: str, ms: int = 2400) -> None:
        """底部浮层提示。取代一连串模态弹窗——弹窗会打断输入，用户还得点确定。"""
        self._toast.setText(text)
        self._toast.adjustSize()
        w = min(self._toast.sizeHint().width() + 28, max(self.width() - 40, 120))
        self._toast.setFixedSize(w, 34)
        self._toast.move((self.width() - w) // 2, self.height() - 62)
        self._toast.show()
        self._toast.raise_()
        self._toast_timer.start(ms)

    def copy_text(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.toast("已复制到剪贴板")

    # ============================================================
    # 知识库
    # ============================================================
    def choose_space(self):
        d = QFileDialog.getExistingDirectory(self, "选择个人空间文件夹")
        if not d:
            return
        cfg = load_config()
        cfg["personal_space"] = d
        save_config(cfg)
        self.file_search.clear()
        self.refresh_file_tree()
        self.space_label.setText(f"📂  {Path(d).name}")
        self.toast("个人空间已设置，记得构建索引")
        self._refresh_index_state()

    def refresh_file_tree(self, expand_all: bool | None = None):
        self.tree.clear()
        cfg = load_config()
        root = cfg.get("personal_space", "")
        if not root or not Path(root).exists():
            self.kb_count_label.setText("")
            top = QTreeWidgetItem(self.tree)
            top.setText(0, "还没有个人空间\n点击上方「选择个人空间」添加一个文件夹")
            top.setFlags(Qt.ItemFlag.NoItemFlags)
            return
        if expand_all is None:
            expand_all = self._tree_expand_all
        self._tree_expand_all = expand_all
        self._node_count = 0
        root_item = QTreeWidgetItem(self.tree)
        root_item.setText(0, f"▾  📂  {Path(root).name}（个人空间）")
        root_item.setData(0, Qt.ItemDataRole.UserRole, str(root))
        root_item.setData(0, Qt.ItemDataRole.UserRole + 1, "dir")
        root_item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        self._fill_tree_children(root_item, Path(root), expand_all)
        root_item.setExpanded(True)
        if expand_all:
            self.tree.expandAll()
        # 后台统计文件数
        def _count():
            from core.indexer import list_text_files
            n = -1
            try:
                n = len(list_text_files(root))
            except Exception:
                pass

            def _update():
                self._file_count = n
                if n < 0:
                    self.kb_count_label.setText("文件统计失败")
                elif n:
                    self.kb_count_label.setText(f"共 {n} 个可索引文件")
                else:
                    self.kb_count_label.setText("未发现可索引文件")
            self.later(_update)
        threading.Thread(target=_count, daemon=True).start()

    def _fill_tree_children(self, parent_item: QTreeWidgetItem, dir_path: Path, expand_all: bool):
        if getattr(self, "_node_count", 0) > 2000:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return
        shown = 0
        for p in entries:
            if getattr(self, "_node_count", 0) > 2000:
                break
            if p.name.startswith(".") or p.name in {"__pycache__", "node_modules"}:
                continue
            self._node_count += 1
            item = QTreeWidgetItem(parent_item)
            if p.is_dir():
                item.setText(0, f"▸  📁  {p.name}")
                item.setData(0, Qt.ItemDataRole.UserRole, str(p))
                item.setData(0, Qt.ItemDataRole.UserRole + 1, "dir")
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                if expand_all:
                    self._fill_tree_children(item, p, expand_all)
            else:
                icon = "📄" if p.suffix.lower() in {".md", ".txt", ".markdown", ".rst"} else "📃"
                item.setText(0, f"{icon}  {p.name}")
                item.setData(0, Qt.ItemDataRole.UserRole, str(p))
                item.setData(0, Qt.ItemDataRole.UserRole + 1, "file")
            shown += 1
            if shown >= 300:
                break

    def _set_dir_glyph(self, item: QTreeWidgetItem, expanded: bool) -> None:
        """QSS 里给 branch 设了背景色，Qt 就不画默认三角了，
        于是用文字箭头 + 开合文件夹图标来指示展开状态。"""
        if item.data(0, Qt.ItemDataRole.UserRole + 1) != "dir":
            return
        name = Path(item.data(0, Qt.ItemDataRole.UserRole)).name
        if item.parent() is None:
            name = f"{name}（个人空间）"
        item.setText(0, f"▾  📂  {name}" if expanded else f"▸  📁  {name}")

    def _on_tree_expand(self, item: QTreeWidgetItem):
        if item.childCount() == 0 and item.data(0, Qt.ItemDataRole.UserRole + 1) == "dir":
            p = Path(item.data(0, Qt.ItemDataRole.UserRole))
            self._fill_tree_children(item, p, False)
            if item.childCount() == 0:
                empty = QTreeWidgetItem(item)
                empty.setText(0, "（空文件夹）")
                empty.setFlags(Qt.ItemFlag.NoItemFlags)

    def _on_tree_item_activated(self, item: QTreeWidgetItem, _col):
        kind = item.data(0, Qt.ItemDataRole.UserRole + 1)
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if kind == "file":
            self.insert_mention(Path(path))
        elif kind == "dir":
            item.setExpanded(not item.isExpanded())

    def _tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            menu = QMenu(self)
            act = QAction("选择个人空间…", menu)
            act.triggered.connect(self.choose_space)
            menu.addAction(act)
            menu.addAction(QAction("刷新", menu, triggered=lambda _=False: self.refresh_file_tree()))
            menu.exec(self.tree.mapToGlobal(pos))
            return
        kind = item.data(0, Qt.ItemDataRole.UserRole + 1)
        path = Path(item.data(0, Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        if kind == "file":
            add_file_actions(menu, path, self)
        else:
            menu.addAction(_file_action("在资源管理器中打开", path, lambda p: open_path_natively(p)))
            menu.addAction(_file_action("复制完整路径", path,
                                        lambda p: QApplication.clipboard().setText(str(p))))
            menu.addSeparator()
            refresh = QAction("重新扫描此文件夹", menu)
            refresh.triggered.connect(lambda _=False: self.refresh_file_tree())
            menu.addAction(refresh)
        menu.exec(self.tree.mapToGlobal(pos))

    def insert_mention(self, path: Path) -> None:
        """把文件路径以 @相对路径 的形式插进输入框（模型会看到这条线索）。"""
        try:
            rel = path.relative_to(Path(load_config().get("personal_space", ".")))
        except Exception:
            rel = Path(path.name)
        edit = self.chat_input
        cursor = edit.textCursor()
        text = edit.toPlainText()
        prefix = "" if (not text or text.endswith((" ", "\n"))) else " "
        edit.show()
        cursor.insertText(f"{prefix}@{rel.as_posix()} ")
        self.toast(f"已引用文件：{rel.name}")
        edit.setFocus()

    def _filter_tree(self):
        """文件名过滤。整棵目录树的 rglob 放后台线程跑，
        否则在几万个文件的个人空间里每敲一个字就卡一次界面。"""
        q = self.file_search.text().strip().lower()
        if not q:
            self.refresh_file_tree(expand_all=False)
            return
        cfg = load_config()
        root = cfg.get("personal_space", "")
        if not root or not Path(root).exists():
            return
        self._tree_seq = getattr(self, "_tree_seq", 0) + 1
        seq = self._tree_seq
        self.kb_count_label.setText("搜索文件名…")

        def scan():
            hits = []
            try:
                base = Path(root)
                for i, p in enumerate(base.rglob("*")):
                    if i > 60000 or len(hits) >= 500:
                        break
                    if i and i % 5000 == 0:
                        n = i
                        self.later(lambda n=n: self.kb_count_label.setText(
                            f"正在扫描 {n:,} 个条目… 命中 {len(hits)}"))
                    if p.is_file() and not p.name.startswith(".") and q in p.name.lower():
                        hits.append((str(p), p.name, str(p.relative_to(base))))
            except Exception:
                pass
            self.later(lambda: self._show_tree_hits(seq, hits, q))

        threading.Thread(target=scan, daemon=True).start()

    def _show_tree_hits(self, seq: int, hits: list, q: str) -> None:
        if seq != getattr(self, "_tree_seq", 0):
            return          # 已经有更新的一次搜索了
        if q != self.file_search.text().strip().lower():
            return
        self.tree.clear()
        for full, name, rel in hits:
            item = QTreeWidgetItem(self.tree)
            item.setText(0, f"📄  {name}")
            item.setData(0, Qt.ItemDataRole.UserRole, full)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, "file")
            item.setToolTip(0, rel)
        self.kb_count_label.setText(
            f"匹配 {len(hits)} 个文件" + ("（已截断）" if len(hits) >= 500 else ""))

    # ============================================================
    # 笔记
    # ============================================================
    def refresh_notes_list(self):
        keep = self._current_note
        self.notes_list.blockSignals(True)
        self.notes_list.clear()
        try:
            notes = sorted(NOTE_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            notes = []
        for p in notes:
            item = QListWidgetItem(p.stem)
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(f"{p}\n修改于 {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}")
            self.notes_list.addItem(item)
            if keep and p == keep:
                self.notes_list.setCurrentItem(item)
        self.notes_list.blockSignals(False)
        self._notes_hint.setVisible(not notes)
        self.notes_list.setVisible(bool(notes))

    def _on_note_selected(self, cur, _prev):
        if cur is not None:
            self._load_note(cur.data(Qt.ItemDataRole.UserRole))

    def _new_note(self):
        name = f"笔记 {datetime.now():%Y-%m-%d %H%M%S}"
        p = NOTE_DIR / f"{name}.md"
        atomic_write_text(p, "")
        self._load_note(p)
        self.refresh_notes_list()
        self.note_editor.setFocus()
        self.toast("已新建笔记，右侧直接写，自动保存")

    def _load_note(self, path: Path):
        self._note_timer.stop()
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            self.toast(f"读取失败：{e}")
            return
        self.note_editor.blockSignals(True)
        self.note_editor.setPlainText(text)
        self.note_editor.blockSignals(False)
        self._current_note = path
        self.note_title_label.setText(path.stem)
        self._note_status_mark("已保存", "green")

    def _note_dirty(self):
        """有改动：先把状态改成「未保存」，再重启防抖定时器。"""
        if self._current_note is None:
            return
        self._note_status_mark("未保存…", "muted")
        self._note_timer.start()

    def _note_status_mark(self, text: str, tone: str) -> None:
        status = getattr(self, "note_status", None)
        if status is None:
            return
        status.setText(text)
        status.setProperty("cText", tone)
        self._repolish(status)

    def _save_current_note(self):
        if not self._current_note:
            self._note_status_mark("未选择笔记", "muted")
            return
        try:
            atomic_write_text(self._current_note, self.note_editor.toPlainText())
            self._note_status_mark(f"已保存 {datetime.now():%H:%M:%S}", "green")
        except OSError as e:
            self._note_status_mark("保存失败", "red")
            self.toast(f"笔记保存失败：{e}")

    def _flush_note(self):
        if self._note_timer.isActive():
            self._note_timer.stop()
            self._save_current_note()

    def _notes_menu(self, pos) -> None:
        item = self.notes_list.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            act = QAction("新建笔记", menu)
            act.triggered.connect(self._new_note)
            menu.addAction(act)
        else:
            path = item.data(Qt.ItemDataRole.UserRole)
            a1 = QAction("重命名", menu)
            a1.triggered.connect(lambda _=False, p=path: self._rename_note(p))
            a2 = QAction("在资源管理器中显示", menu)
            a2.triggered.connect(lambda _=False, p=path: reveal_in_explorer(p))
            a3 = QAction("删除笔记", menu)
            a3.triggered.connect(lambda _=False, p=path: self._delete_note(p))
            for a in (a1, a2, a3):
                menu.addAction(a)
        menu.exec(self.notes_list.mapToGlobal(pos))

    def _rename_note(self, path: Path):
        text, ok = QInputDialog.getText(self, "重命名笔记", "笔记名称：", text=path.stem)
        if not ok:
            return
        clean = re.sub(r'[\\/:*?"<>|]', "", text).strip()
        if not clean:
            self.toast("名称不能为空")
            return
        target = path.with_name(f"{clean}.md")
        if target.exists():
            self.toast("已经有同名笔记了")
            return
        try:
            path.rename(target)
        except OSError as e:
            self.toast(f"重命名失败：{e}")
            return
        if self._current_note == path:
            self._current_note = target
            self.note_title_label.setText(target.stem)
        self.refresh_notes_list()

    def _delete_note(self, path: Path):
        box = QMessageBox(self)
        box.setWindowTitle("删除笔记")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"确定删除笔记「{path.stem}」？")
        box.setInformativeText("文件会被直接删掉，不放进回收站。")
        yes = box.addButton("删除", QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        if box.clickedButton() is not yes:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            self.toast(f"删除失败：{e}")
            return
        if self._current_note == path:
            self._current_note = None
            self._note_timer.stop()
            self.note_editor.clear()
            self.note_title_label.setText("选择左侧笔记，或新建一篇")
            self._note_status_mark("", "muted")
        self.refresh_notes_list()
        self.toast("已删除笔记")

    # ============================================================
    # 找文件
    # ============================================================
    def _find_debounce(self):
        self._find_timer.start()

    def _cancel_find(self):
        self._find_cancel.set()
        self.find_count.setText("正在取消…")

    def _do_find(self, force: bool = False):
        if force:
            self._find_timer.stop()
        q = self.find_entry.text().strip().lower()
        self._find_seq += 1
        seq = self._find_seq
        self._find_cancel = getattr(self, "_find_cancel", threading.Event())
        self._find_cancel.clear()
        self._clear_layout(self.find_results_layout)
        self.find_results_layout.addStretch(1)
        if not q:
            self.find_count.setText("")
            hint = QLabel("输入关键字即可搜索个人空间")
            hint.setObjectName("Muted")
            self.find_results_layout.insertWidget(0, hint)
            return
        cfg = load_config()
        root = cfg.get("personal_space", "")
        if not root or not Path(root).exists():
            self.find_count.setText("")
            hint = QLabel("还没有个人空间，先在对话页右侧选择要搜索的文件夹")
            hint.setObjectName("Muted")
            self.find_results_layout.insertWidget(0, hint)
            return
        self.find_count.setText("搜索中…")
        self.find_cancel_btn.show()
        by_content = self.find_content_check.isChecked()
        cancel = self._find_cancel

        def progress(scanned: int, found: int):
            self.later(lambda: self.find_count.setText(
                f"已扫描 {scanned:,} 个文件 · 命中 {found} 个…"))

        def scan():
            hits: list[tuple] = []
            scanned = 0
            truncated = False
            cancelled = False
            try:
                from core.indexer import SUPPORTED_EXTS   # 顺带把 numpy 的导入放到后台线程
                base = Path(root)
                for p in base.rglob("*"):
                    if cancel.is_set():
                        cancelled = True
                        break
                    if len(hits) >= 100 or scanned >= 60000:
                        truncated = len(hits) >= 100
                        break
                    try:
                        if not p.is_file() or p.name.startswith("."):
                            continue
                    except OSError:
                        continue
                    scanned += 1
                    if scanned % 2000 == 0:
                        progress(scanned, len(hits))
                    if q in p.name.lower():
                        hits.append((str(p), p.name, str(p.relative_to(base)), False))
                    # 只对可索引的文本类型读正文：省下打开上万个二进制文件的时间
                    elif (by_content and p.suffix.lower() in SUPPORTED_EXTS
                          and self._file_contains(p, q)):
                        hits.append((str(p), p.name, str(p.relative_to(base)), True))
            except Exception:
                pass
            self.later(lambda: self._show_find_hits(seq, hits, scanned, truncated, cancelled))

        threading.Thread(target=scan, daemon=True).start()

    @staticmethod
    def _file_contains(path: Path, q: str) -> bool:
        """只读文件头 4000 字节再解码：内容匹配够用，又不会把大文件整个吞进内存。"""
        try:
            with path.open("rb") as f:
                head = f.read(4000)
        except Exception:
            return False
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return q in head.decode(enc, errors="ignore").lower()
            except Exception:
                continue
        return False

    def _show_find_hits(self, seq: int, hits: list, scanned: int,
                        truncated: bool, cancelled: bool = False) -> None:
        self.find_cancel_btn.hide()
        if seq != self._find_seq:
            return                      # 更新的一次搜索已经发起了，丢掉这次结果
        tail = "（结果过多已截断）" if truncated else ""
        if cancelled:
            self.find_count.setText(f"已取消 · 扫描 {scanned:,} 个文件，命中 {len(hits)} 个")
        else:
            self.find_count.setText(
                f"匹配 {len(hits)} 个文件 · 已扫描 {scanned:,} 个" + tail)
        if not hits:
            hint = QLabel("没有找到匹配的文件；试试更短的关键字，或勾选「搜内容」"
                          if not cancelled else "搜索已取消")
            hint.setObjectName("Muted")
            self.find_results_layout.insertWidget(0, hint)
            return
        for full, name, rel, content_hit in hits:
            row = RowFrame()
            row.setObjectName("FindRow")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(12, 8, 10, 8)
            lay.setSpacing(10)
            col = QVBoxLayout()
            col.setSpacing(1)
            title = QLabel(f"📄  {name}" + ("　·  内容匹配" if content_hit else ""))
            title.setObjectName("Strong")
            sub = ElidedLabel(rel)
            sub.setObjectName("Meta")
            col.addWidget(title)
            col.addWidget(sub)
            lay.addLayout(col, 1)
            path = Path(full)
            quote = QPushButton("引用到对话")
            quote.setObjectName("GhostSmall")
            quote.clicked.connect(lambda _=False, p=path: self._use_find_result(p))
            openb = QPushButton("打开")
            openb.setObjectName("GhostSmall")
            openb.clicked.connect(lambda _=False, p=path: open_path_natively(p))
            for w in (quote, openb):
                lay.addWidget(w)
                row.add_hover_widget(w)
            row.activated.connect(lambda p=path: self._use_find_result(p))
            row.customContextMenuRequested.connect(
                lambda _p, p=path, r=row: self._find_row_menu(p, _p, r))
            self.find_results_layout.insertWidget(self.find_results_layout.count() - 1, row)

    def _find_row_menu(self, path: Path, pos, row: RowFrame) -> None:
        menu = QMenu(row)
        add_file_actions(menu, path, self)
        menu.exec(row.mapToGlobal(pos))

    def _use_find_result(self, path: Path):
        self.show_page("chat")
        self.insert_mention(path)

    # ============================================================
    # 模型部署
    # ============================================================
    def check_ollama(self) -> dict:
        """同步查询 Ollama 与已装模型。只允许在后台线程调用：
        `ollama list` 在服务没起来时要等好几秒，放在主线程会直接卡住界面。"""
        result: dict = {"installed": False, "models": [], "alive": False}
        exe = self._find_ollama_exe()
        if exe is None:
            return result
        result["installed"] = True
        result["alive"] = self._ollama_alive()
        try:
            out = subprocess.run([exe, "list"], capture_output=True, text=True,
                                 timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if out.returncode == 0:
                for line in (out.stdout or "").splitlines()[1:]:
                    parts = line.split()
                    if parts:
                        result["models"].append(parts[0])
        except Exception:
            pass
        return result

    def refresh_deploy_status(self, force: bool = False):
        """进页面先画「检测中」骨架，真正的探测丢到后台线程。"""
        if getattr(self, "_deploy_probing", False) and not force:
            return
        self._deploy_probing = True
        self._deploy_vars = {}
        self._clear_layout(self.deploy_layout)
        self.deploy_layout.addStretch(1)
        self._set_deploy_status("")
        loading = QLabel("⏳  正在检测 Ollama 与已部署模型…")
        loading.setObjectName("Muted")
        self.deploy_layout.insertWidget(0, loading)

        def worker():
            info = self.check_ollama()
            self._deploy_probing = False
            self.later(lambda: self._render_deploy(info))

        threading.Thread(target=worker, daemon=True).start()

    def _render_deploy(self, info: dict) -> None:
        from core.models_catalog import EMBEDDING_MODELS
        self._clear_layout(self.deploy_layout)
        self.deploy_layout.addStretch(1)
        installed_models = set(info.get("models", []))
        cfg = load_config()

        def add(w: QWidget):
            self.deploy_layout.insertWidget(self.deploy_layout.count() - 1, w)

        # ---------- 卡片 1：运行环境 ----------
        card1, f1 = make_card("① 本地运行环境", "Ollama：在你这台电脑上跑大模型的工具")
        if info.get("installed"):
            if info.get("alive"):
                status, tone = f"● 运行中 · 已装 {len(installed_models)} 个模型" + (
                    ("：" + "、".join(sorted(installed_models))) if installed_models else "（还没有模型，去第 ② 步下载）"), "green"
            else:
                status, tone = "● 已安装但未运行，点右侧按钮启动", "amber"
        else:
            status, tone = "● 未安装，装完才能本地问答（约 1GB，全程在你这台电脑上完成）", "red"
        line = QHBoxLayout()
        st = QLabel(status)
        st.setObjectName("Strong")
        st.setProperty("cText", tone)
        st.setWordWrap(True)
        self._repolish(st)
        line.addWidget(st, 1)
        if not info.get("installed"):
            btn = QPushButton("⬇  一键安装 Ollama")
            btn.setObjectName("Primary")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(self._install_ollama)
            line.addWidget(btn)
        elif not info.get("alive"):
            btn = QPushButton("▶  启动服务")
            btn.setObjectName("Primary")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(self._start_ollama)
            line.addWidget(btn)
        f1.addLayout(line)
        add(card1)

        # ---------- 卡片 2：对话模型 ----------
        card2, f2 = make_card("② 对话模型", "生成回答的大模型，勾选后可批量下载")
        bar = QHBoxLayout()
        all_btn = QPushButton("全选")
        all_btn.setObjectName("GhostSmall")
        all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        all_btn.clicked.connect(lambda: self._set_deploy_select(True))
        none_btn = QPushButton("全不选")
        none_btn.setObjectName("GhostSmall")
        none_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        none_btn.clicked.connect(lambda: self._set_deploy_select(False))
        self.deploy_batch_btn = QPushButton("⬇  部署选中模型")
        self.deploy_batch_btn.setObjectName("Primary")
        self.deploy_batch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.deploy_batch_btn.clicked.connect(self._deploy_selected)
        bar.addWidget(all_btn)
        bar.addWidget(none_btn)
        bar.addStretch(1)
        bar.addWidget(self.deploy_batch_btn)
        f2.addLayout(bar)
        tip = QLabel("整行点一下即可勾选，双击直接部署；勾选框只是状态指示")
        tip.setObjectName("CardHint")
        f2.addWidget(tip)
        if cfg.get("chat_provider") == "ollama" and cfg.get("chat_model") not in installed_models:
            cur = cfg.get("chat_model", "")
            warn = QHBoxLayout()
            lbl = QLabel(f"⚠ 当前正在使用的 {cur} 还没下载，现在提问一定会失败")
            lbl.setObjectName("Strong")
            lbl.setProperty("cText", "amber")
            lbl.setWordWrap(True)
            self._repolish(lbl)
            warn.addWidget(lbl, 1)
            m = next((x for x in self._ollama_catalog if x["id"] == cur), None)
            if m:
                fix = QPushButton("⬇  立即下载它")
                fix.setObjectName("Primary")
                fix.setCursor(Qt.CursorShape.PointingHandCursor)
                fix.clicked.connect(lambda _=False, mm=m: self._deploy_model(mm))
                warn.addWidget(fix)
            f2.addLayout(warn)

        for m in self._ollama_catalog:
            f2.addWidget(self._chat_model_row(m, m["id"] in installed_models,
                                              cfg.get("chat_model") == m["id"]))
        add(card2)

        # ---------- 卡片 3：Embedding 模型 ----------
        card3, f3 = make_card("③ Embedding 模型", "把文件变成向量，决定检索准不准")
        for m in EMBEDDING_MODELS:
            f3.addWidget(self._embed_model_row(m, cfg.get("embedding_model") == m["id"]))
        add(card3)

    def _chat_model_row(self, m: dict, deployed: bool, current: bool) -> RowFrame:
        row = RowFrame()
        row.setObjectName("ModelRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(10)
        cb = QCheckBox()
        cb.setFixedWidth(22)
        cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._deploy_vars[m["id"]] = cb
        lay.addWidget(cb)
        col = QVBoxLayout()
        col.setSpacing(0)
        name = QLabel(f"{m['name']} · 约{m['size_gb']}GB" + ("　·  当前使用" if current else ""))
        name.setObjectName("Strong")
        desc = ElidedLabel(m["desc"])
        desc.setObjectName("Meta")
        col.addWidget(name)
        col.addWidget(desc)
        lay.addLayout(col, 1)
        if deployed:
            badge = QLabel("✓ 已部署")
            badge.setObjectName("Badge")
            badge.setProperty("cText", "green")
            lay.addWidget(badge)
            if not current:
                use = QPushButton("设为当前")
                use.setObjectName("GhostSmall")
                use.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                use.clicked.connect(lambda _=False, mid=m["id"]: self._use_chat_model(mid))
                lay.addWidget(use)
        else:
            dep = QPushButton("⬇  部署")
            dep.setObjectName("GhostSmall")
            dep.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            dep.clicked.connect(lambda _=False, mm=m: self._deploy_model(mm))
            lay.addWidget(dep)
        copy = QPushButton("📋")
        copy.setObjectName("MiniAction")
        copy.setToolTip(f"复制命令：{m['ollama_cmd']}")
        copy.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        copy.clicked.connect(lambda _=False, cmd=m["ollama_cmd"]: self._copy(cmd))
        lay.addWidget(copy)

        row.clicked_single.connect(lambda _c=cb: _c.setChecked(not _c.isChecked()))
        row.activated.connect(lambda mm=m, d=deployed: None if d else self._deploy_model(mm))
        return row

    def _embed_model_row(self, m: dict, current: bool) -> RowFrame:
        row = RowFrame()
        row.setObjectName("ModelRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(0)
        name = QLabel(("●  " if current else "○  ") + m["id"])
        name.setObjectName("Strong")
        desc = QLabel(f"{m['lang']} · 约{m['size_gb']}GB")
        desc.setObjectName("Meta")
        col.addWidget(name)
        col.addWidget(desc)
        lay.addLayout(col, 1)
        if self._embedding_downloaded(m["id"]):
            badge = QLabel("✓ 已下载")
            badge.setObjectName("Badge")
            badge.setProperty("cText", "green")
            lay.addWidget(badge)
            if not current:
                use = QPushButton("设为当前")
                use.setObjectName("GhostSmall")
                use.clicked.connect(lambda _=False, mid=m["id"]: self._use_embedding_model(mid))
                lay.addWidget(use)
        else:
            dl = QPushButton("⬇  下载")
            dl.setObjectName("GhostSmall")
            dl.clicked.connect(lambda _=False, mid=m["id"]: self._deploy_embedding(mid))
            lay.addWidget(dl)
        return row

    def _use_chat_model(self, model_id: str) -> None:
        cfg = load_config()
        cfg["chat_model"] = model_id
        cfg["chat_provider"] = "ollama"
        save_config(cfg)
        self.assistant = None
        self._refresh_model_menu()
        self.refresh_deploy_status(force=True)
        self.toast(f"已切换到 {model_id}")

    def _use_embedding_model(self, model_id: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("切换 Embedding 模型")
        box.setText(f"改用 {model_id}？")
        box.setInformativeText("换模型后旧索引会失效，需要重新构建一次索引。")
        ok = box.addButton("切换并重建索引", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("只切换", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None:
            return
        cfg = load_config()
        cfg["embedding_model"] = model_id
        save_config(cfg)
        self.embedder = None
        self.indexer = None
        self.assistant = None
        self._refresh_index_state()
        if clicked is ok:
            self._build_index(full=True)

    def _start_ollama(self) -> None:
        exe = self._find_ollama_exe()
        if not exe:
            self.toast("没找到 ollama.exe，请先执行第 ① 步安装")
            return
        subprocess.Popen([exe, "serve"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._set_deploy_status("正在启动 Ollama 服务…")
        QTimer.singleShot(2500, lambda: self.refresh_deploy_status(force=True))

    def _embedding_downloaded(self, model_id: str) -> bool:
        """检查 embedding 模型是否已下载到本地缓存（兼容 fastembed 两种落地格式）。"""
        cache = Path(load_config().get("models_dir", ""))
        if not cache.exists():
            return False
        short = model_id.split("/")[-1].lower()
        try:
            entries = list(cache.iterdir())
        except OSError:
            return False
        for d in entries:
            if not d.is_dir():
                continue
            name = d.name.lower()
            if short not in name:
                continue
            if name.startswith("models--"):
                # HF snapshot 格式：有 blobs/snapshots 才算下载完成（避免残留 refs 误判）
                if (d / "blobs").exists() or (d / "snapshots").exists():
                    return True
            else:
                # GCS 解压格式（fastembed 回退路线）：fast-<name> / <name>，含 onnx 即算完成
                if any(d.glob("*.onnx")):
                    return True
        return False

    def _find_ollama_exe(self) -> str | None:
        p = shutil.which("ollama")
        if p:
            return p
        for cand in (
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
            os.path.expandvars(r"%ProgramFiles%\Ollama\ollama.exe"),
        ):
            if os.path.exists(cand):
                return cand
        return None

    def _ollama_alive(self) -> bool:
        import urllib.request
        try:
            with urllib.request.urlopen("http://localhost:11434", timeout=2):
                return True
        except Exception:
            return False

    def _install_ollama(self):
        if self._deploying:
            self.toast("已有下载任务在进行，请先取消或等它完成")
            return
        self._deploying = True
        threading.Thread(target=self._install_ollama_worker, daemon=True).start()

    def _install_ollama_worker(self):
        try:
            exe = self._ensure_ollama(self._set_deploy_status)
            if exe:
                self._set_deploy_status("✅ Ollama 已就绪，可以在第 ② 步下载对话模型了")
                self.later(lambda: self.refresh_deploy_status(force=True))
            else:
                self._set_deploy_status("⚠️ 自动安装没成功，可到 ollama.com/download 手动下载安装后再「重新检测」")
        except Exception as e:
            self._set_deploy_status(f"⚠️ 安装出错：{e}")
        finally:
            self._deploying = False
            self.later(self._deploy_finished)

    def _download_ollama_setup(self, dest: Path, set_status) -> bool:
        import requests as _rq
        urls = [
            "https://gh-proxy.com/https://github.com/ollama/ollama/releases/latest/download/OllamaSetup.exe",
            "https://ollama.com/download/OllamaSetup.exe",
        ]
        for u in urls:
            try:
                set_status("正在下载 Ollama 安装包（约 1GB，取决于网速）…")
                with _rq.get(u, stream=True, timeout=60) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_content(65536):
                            if self._deploy_cancel.is_set():
                                return False
                            f.write(chunk)
                            done += len(chunk)
                            if total:
                                pct = done * 100 / total
                                self._set_progress(pct, f"下载安装包 {done / 1048576:.0f} / {total / 1048576:.0f} MB")
                if dest.stat().st_size > 10_000_000:
                    return True
            except Exception:
                continue
        return False

    def _ensure_ollama(self, set_status) -> str | None:
        exe = self._find_ollama_exe()
        if exe:
            if not self._ollama_alive():
                set_status("正在启动 Ollama 服务…")
                subprocess.Popen([exe, "serve"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                time.sleep(3)
            return exe
        setup = Path(os.environ.get("TEMP", ".")) / "OllamaSetup.exe"
        if not self._download_ollama_setup(setup, set_status):
            return None
        set_status("正在静默安装 Ollama（约 1 分钟）…")
        self._set_progress(None, "正在安装 Ollama…")
        subprocess.run([str(setup), "/SILENT"],
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                       timeout=900)
        try:
            setup.unlink(missing_ok=True)
        except Exception:
            pass
        exe = self._find_ollama_exe()
        if not exe:
            return None
        set_status("✅ Ollama 安装完成，正在启动服务…")
        if not self._ollama_alive():
            subprocess.Popen([exe, "serve"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            time.sleep(3)
        return exe

    def _deploy_embedding(self, model_id: str):
        if self._deploying:
            self.toast("已有下载任务在进行，请先取消或等它完成")
            return
        self._deploying = True
        threading.Thread(target=self._deploy_embedding_worker, args=(model_id,), daemon=True).start()

    def _deploy_embedding_worker(self, model_id: str):
        try:
            self._set_progress(None, f"正在下载 embedding 模型 {model_id}…")
            from core.indexer import Embedder
            Embedder(model_id, device="cpu")
            self._set_progress(100, f"✅ embedding 模型 {model_id} 已就绪")
            self.later(lambda: self.refresh_deploy_status(force=True))
        except Exception as e:
            self._set_progress(None, f"⚠️ embedding 模型下载失败：{e}")
        finally:
            self._deploying = False
            self.later(self._deploy_finished)

    def _deploy_model(self, model: dict):
        if self._deploying:
            self.toast("已有下载任务在进行，请先取消或等它完成")
            return
        self._deploying = True
        self._deploy_cancel.clear()

        def worker():
            try:
                self._deploy_one(model)
            finally:
                self._deploying = False
                self.later(self._deploy_finished)
                self.later(lambda: self.refresh_deploy_status(force=True))

        threading.Thread(target=worker, daemon=True).start()

    def _deploy_selected(self):
        models = [m for m in self._ollama_catalog
                  if (cb := self._deploy_vars.get(m["id"])) is not None and cb.isChecked()]
        if not models:
            self.toast("先点几行模型把它勾上（或按「全选」）")
            return
        if self._deploying:
            self.toast("已有下载任务在进行，请先取消或等它完成")
            return
        self._deploying = True
        self._deploy_cancel.clear()

        def worker():
            failed = []
            total = len(models)
            try:
                for i, model in enumerate(models, 1):
                    if self._deploy_cancel.is_set():
                        break
                    self._set_progress(None, f"[{i}/{total}] 开始下载 {model['id']}")
                    if not self._deploy_one(model, prefix=f"[{i}/{total}] "):
                        failed.append(model["name"])
            finally:
                self._deploying = False
                self.later(self._deploy_finished)
                self.later(lambda: self.refresh_deploy_status(force=True))
                done = total - len(failed)
                msg = f"✅ 批量部署完成（{done}/{total}）" if not failed \
                    else f"⚠️ 完成 {done}/{total}，失败：{'、'.join(failed)}"
                self.later(lambda m=msg: self._set_deploy_status(m))

        threading.Thread(target=worker, daemon=True).start()

    def _set_deploy_select(self, checked: bool):
        for var in self._deploy_vars.values():
            var.setChecked(checked)

    def _deploy_one(self, model: dict, prefix: str = "") -> bool:
        """拉取一个模型，解析 ollama 输出里的百分比画到进度条上。返回是否成功。"""
        try:
            exe = self._ensure_ollama(lambda t: self._set_deploy_status(prefix + t))
            if not exe:
                self._set_deploy_status("⚠️ 安装未成功，可点「重新检测」或到 ollama.com 手动安装")
                return False
            self._set_progress(None, f"{prefix}正在下载模型 {model['id']}（约 {model['size_gb']} GB）…")
            proc = subprocess.Popen(
                [exe, "pull", model["id"]],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in (proc.stdout or []):
                if self._deploy_cancel.is_set():
                    proc.terminate()
                    self._set_deploy_status("⏹ 已取消下载")
                    return False
                line = line.strip()
                if not line:
                    continue
                pct = _PULL_PCT.search(line)
                if pct:
                    bar = float(pct.group(1))
                    sizes = _PULL_SIZE.search(line)
                    tail = f"  ·  已下载 {bar:.0f}% / 共 {sizes.group(1)}" if sizes else ""
                    self._set_progress(bar, f"{prefix}{model['id']}{tail}")
                else:
                    self._set_deploy_status(prefix + line[:120])
            rc = proc.wait()
            return rc == 0
        except Exception as e:
            self._set_deploy_status(f"⚠️ 部署失败：{e}")
            return False

    def _cancel_deploy(self) -> None:
        self._deploy_cancel.set()
        self._set_deploy_status("正在取消下载…")

    def _deploy_finished(self) -> None:
        """下载结束后恢复界面：进度条收起，按钮回到「重新检测」。"""
        self.deploy_progress.hide()
        self.deploy_cancel_btn.hide()
        self.deploy_check_btn.setEnabled(True)

    def _set_progress(self, pct, text: str) -> None:
        """pct=None 表示进度未知，跑不确定进度条；否则按百分比填充。"""
        def apply():
            self.deploy_progress.show()
            self.deploy_cancel_btn.show()
            self.deploy_check_btn.setEnabled(False)
            if pct is None:
                self.deploy_progress.setRange(0, 0)
                self.deploy_progress.setFormat(text[:120])
            else:
                self.deploy_progress.setRange(0, 100)
                self.deploy_progress.setValue(int(pct))
                self.deploy_progress.setFormat(f"{pct:.0f}%　{text}"[:120])
            self._set_deploy_status(text)
        self.later(apply)

    def _set_deploy_status(self, text: str):
        self.later(lambda t=text: self.deploy_status_label.setText(t))

    def _copy(self, text: str):
        QApplication.clipboard().setText(text)
        self.toast(f"已复制：{text[:60]}")

    # ============================================================
    # 设置（自动保存）
    # ============================================================
    def _browse_models_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择模型下载目录")
        if d:
            self.models_dir_edit.setText(d)

    def _on_provider_change(self):
        local = self.provider_combo.currentText() == "本地 Ollama"
        if local:
            labels, mapping = self._ollama_labels, self._ollama_map
        else:
            labels, mapping = self._cloud_labels, self._cloud_map
        self._settings_model_map = mapping
        self.settings_model_combo.blockSignals(True)
        self.settings_model_combo.clear()
        self.settings_model_combo.addItems(labels)
        self.settings_model_combo.blockSignals(False)
        # 本地模式没有 Key 这回事，留着一栏只会让人以为必须填
        self._key_field.setVisible(not local)
        self._save_settings()

    def _on_topk_change(self, value: int):
        self.topk_label.setText(f"{value} 个片段")
        self._save_settings()

    def _save_settings(self, *_args):
        """控件变化只重启定时器，400ms 内没有下一次变化才真正写盘。"""
        if self._loading_settings or not hasattr(self, "models_dir_edit"):
            return
        self._set_saved_mark("等待保存…", "muted")
        self._settings_timer.start()

    def _set_saved_mark(self, text: str, tone: str) -> None:
        lbl = getattr(self, "settings_saved", None)
        if lbl is None:
            return
        lbl.setText(text)
        lbl.setVisible(bool(text))
        lbl.setProperty("cText", tone)
        self._repolish(lbl)

    def _save_settings_now(self):
        """设置页任一控件变化即自动保存（无需「保存」按钮）。"""
        if self._loading_settings:
            return
        cfg = load_config()
        prev_emb = cfg.get("embedding_model")
        cfg["models_dir"] = self.models_dir_edit.text().strip() or r"D:\RAGAssistant\models"
        cfg["embedding_device"] = self._device_map.get(self.device_combo.currentText(), "auto")
        cfg["auto_index"] = self.auto_index_check.isChecked()
        cfg["embedding_model"] = self.emb_combo.currentText()
        provider = self.provider_combo.currentText()
        cfg["chat_provider"] = "ollama" if provider == "本地 Ollama" else "openai"
        cfg["chat_model"] = getattr(self, "_settings_model_map", {}).get(
            self.settings_model_combo.currentText(), self.settings_model_combo.currentText())
        if cfg["chat_provider"] == "openai":
            from core.models_catalog import get_cloud_model
            m = get_cloud_model(cfg["chat_model"])
            if m:
                cfg["cloud_provider"] = m["provider"]
        cfg["chat_base_url"] = self.base_edit.text().strip()
        if self.key_edit.text().strip():
            cfg["chat_api_key"] = self.key_edit.text().strip()
        cfg["top_k"] = int(self.topk_slider.value())
        save_config(cfg)
        # 模型/设备变化后，让依赖在下次使用时重新加载
        self.embedder = None
        self.indexer = None
        self.assistant = None
        self._set_saved_mark(f"✓ 已自动保存 {datetime.now():%H:%M:%S}", "green")
        self._refresh_model_menu()
        if prev_emb != cfg["embedding_model"]:
            self.toast("Embedding 模型已更换，旧索引失效，请重建索引")
        self._refresh_index_state()

    # ============================================================
    # 索引状态
    # ============================================================
    def _index_info(self) -> tuple[int, float]:
        """返回 (片段数, 索引文件的修改时间)。"""
        try:
            meta = INDEX_DIR / "metadata.json"
            if not meta.exists():
                return 0, 0.0
            data = json.loads(meta.read_text(encoding="utf-8"))
            return (len(data) if isinstance(data, list) else 0), meta.stat().st_mtime
        except Exception:
            return 0, 0.0

    def _refresh_index_state(self) -> None:
        """把索引状态讲成人话：有没有、多大、什么时候建的、用的哪个模型。"""
        lbl = getattr(self, "index_state_label", None)
        if lbl is None:
            return
        cfg = load_config()
        count, mtime = self._index_info()
        built_with = cfg.get("index_embedding_model", "")
        current = cfg.get("embedding_model", "")
        if not cfg.get("personal_space"):
            text, tone = "还没有选择个人空间，先去对话页右侧选一个文件夹。", "amber"
        elif not count:
            text, tone = "还没有索引 —— 点「构建 / 更新索引」后才能问答。", "amber"
        elif built_with and built_with != current:
            text, tone = (f"⚠ 索引是用 {built_with} 建的，当前选了 {current}，"
                          f"两者向量对不上，必须重建索引（现有 {count} 个片段）。"), "red"
        else:
            when = datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M") if mtime else "未知时间"
            text, tone = f"✓ 索引就绪：{count} 个片段 · 构建于 {when}", "green"
        lbl.setText(text)
        lbl.setProperty("cText", tone)
        self._repolish(lbl)

    def _clear_index(self):
        count, _ = self._index_info()
        if not count:
            self.toast("当前没有索引可清空")
            return
        box = QMessageBox(self)
        box.setWindowTitle("清空索引")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"清空现有的 {count} 个索引片段？")
        box.setInformativeText("只会删除本软件生成的向量缓存，你的文件不会被删掉。之后需要重新构建索引。")
        yes = box.addButton("清空", QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        if box.clickedButton() is not yes:
            return
        for name in ("index.faiss", "vectors.npy", "metadata.json",
                     "index.faiss.bak", "vectors.npy.bak", "metadata.json.bak"):
            try:
                (INDEX_DIR / name).unlink(missing_ok=True)
            except OSError as e:
                self.toast(f"删除 {name} 失败：{e}")
        self.indexer = None
        self.assistant = None
        cfg = load_config()
        cfg.pop("index_embedding_model", None)
        save_config(cfg)
        self._refresh_index_state()
        self.refresh_file_tree()
        self.toast("索引已清空")

    # ============================================================
    # 数据存储位置
    # ============================================================
    def _refresh_data_home(self) -> None:
        """显示当前数据目录、占用、所在盘剩余空间。目录里文件不多，直接算。"""
        size = dir_size_gb(USER_DIR)
        free = free_gb(USER_DIR)
        self.data_dir_label.setText(str(USER_DIR))
        self.data_dir_label.setToolTip(
            "改位置后需要重启软件生效。\n"
            "记录在 ~/.rag_assistant_location 指针文件与环境变量 RAG_DATA_HOME 里。")
        extra = ""
        if getattr(self, "_moved_hint", ""):
            extra = "\n" + self._moved_hint
        self.data_dir_hint.setText(
            f"当前占用 {size:.2f} GB · 该盘剩余 {free:.1f} GB"
            + ("（首次运行自动选在了剩余空间最大的非系统盘）" if _AUTO_PICKED else "")
            + extra)

    def _move_data_home(self):
        if getattr(self, "_moving_data", False):
            self.toast("正在复制中，请等它完成")
            return
        picked = QFileDialog.getExistingDirectory(self, "选择新的数据目录（会在里面建 RAGAssistant数据）",
                                                  str(USER_DIR.parent if USER_DIR.parent.exists() else USER_DIR))
        if not picked:
            return
        chosen = Path(picked)
        try:
            empty = not any(chosen.iterdir())
        except OSError:
            empty = False
        target = chosen if empty else chosen / "RAGAssistant数据"
        if target.resolve() == USER_DIR.resolve():
            self.toast("这已经是当前目录了")
            return
        size = dir_size_gb(USER_DIR)
        free = free_gb(target.parent if not target.parent.exists() else target)
        if free >= 0 and free < size + 1.0:
            self.toast(f"目标盘剩余 {free:.1f} GB，放不下 {size:.2f} GB 的数据")
            return
        box = QMessageBox(self)
        box.setWindowTitle("更改数据存储位置")
        box.setText(f"把数据复制到\n{target}")
        box.setInformativeText(
            f"包括配置、对话、笔记和向量索引，约 {size:.2f} GB。\n"
            "原目录不会自动删除，复制完成后你可以选择删掉它。改完需要重启软件生效。")
        go = box.addButton("开始复制", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(go)
        box.exec()
        if box.clickedButton() is not go:
            return
        self._start_data_move(target, size)

    def _start_data_move(self, target: Path, size: float) -> None:
        self._moving_data = True
        self.move_dir_btn.setEnabled(False)
        src = USER_DIR

        def progress(text: str):
            self.later(lambda: self.data_dir_hint.setText(text))

        def worker():
            try:
                files = [p for p in src.rglob("*") if p.is_file()]
                total = len(files)
                for i, f in enumerate(files, 1):
                    rel = f.relative_to(src)
                    dst = target / rel
                    try:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dst)
                    except OSError as e:
                        progress(f"复制失败：{e}")
                        self.later(self._data_move_failed)
                        return
                    if i % 10 == 0 or i == total:
                        progress(f"正在复制 {i}/{total} 个文件…")
                copied = dir_size_gb(target)
                if copied + 0.01 < size:
                    progress(f"复制不完整（{copied:.2f} / {size:.2f} GB），未切换")
                    self.later(self._data_move_failed)
                    return
                set_data_home(target, persist_env=True)
                self._moving_data = False
                self.later(lambda: self._data_moved(src, target, size))
            except Exception as e:
                progress(f"复制出错：{e}")
                self.later(self._data_move_failed)

        threading.Thread(target=worker, daemon=True).start()

    def _data_move_failed(self) -> None:
        self._moving_data = False
        self.move_dir_btn.setEnabled(True)
        self.toast("复制没成功，数据目录没动")
        self._refresh_data_home()

    def _data_moved(self, src: Path, target: Path, size: float) -> None:
        self._moving_data = False
        self.move_dir_btn.setEnabled(True)
        self._moved_hint = f"已改到 {target}，重启软件后生效（当前进程仍在使用 {src}）"
        self.data_dir_label.setText(str(target))
        self._refresh_data_home()
        self.toast("已切换数据目录，重启软件后生效", ms=5000)
        box = QMessageBox(self)
        box.setWindowTitle("是否删除旧目录")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"新目录已复制完成（{size:.2f} GB）")
        box.setInformativeText(f"要现在删掉旧目录 {src} 吗？删了才能腾出原来的空间。")
        dele = box.addButton("删除旧目录", QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton("先留着", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep)   # 删目录是不可逆的，默认停在「先留着」
        box.exec()
        if box.clickedButton() is not dele:
            return
        try:
            shutil.rmtree(src)
            self._moved_hint += "；旧目录已删除"
            self._refresh_data_home()
            self.toast("旧目录已删除")
        except OSError as e:
            self.toast(f"旧目录删除失败（可能正被占用）：{e}", ms=5000)

    # ============================================================
    # 依赖加载
    # ============================================================
    def _get_embedder(self):
        from core.indexer import Embedder, resolve_device
        cfg = load_config()
        model = cfg["embedding_model"]
        device = cfg.get("embedding_device", "auto")
        expected = resolve_device(device)
        # 比对「申请的设备」而不是「实际生效的设备」：CUDA 申请失败回退 CPU 时，
        # 用实际值比会永远不相等，导致每问一次都重新加载一遍模型。
        if self.embedder is None or self.embedder.model_name != model or self.embedder.requested != expected:
            try:
                self.embedder = Embedder(model, device=device)
                self.embedder_error = ""
            except Exception as e:
                self.embedder = None
                self.embedder_error = f"模型 {model} 加载失败：{e}"
                return None
        return self.embedder

    def _get_indexer(self):
        from core.indexer import Indexer
        cfg = load_config()
        emb = self._get_embedder()
        if emb is None:
            return None
        if self.indexer is None or self.indexer.embedder is not emb:
            self.indexer = Indexer(emb, cfg["chunk_size"], cfg["chunk_overlap"])
        if self.indexer.store.size == 0:
            try:
                self.indexer.load()
            except Exception:
                pass
        return self.indexer

    def _get_assistant(self):
        from core.chat import ChatEngine, RAGAssistant
        cfg = load_config()
        idx = self._get_indexer()
        if idx is None or idx.store.size == 0:
            return None
        if cfg.get("chat_provider") == "ollama":
            engine = ChatEngine("ollama", cfg["chat_model"], base_url=cfg.get("chat_base_url", ""))
        else:
            provider = cfg.get("cloud_provider", "deepseek")
            api_key = cfg.get("chat_api_key") or get_api_key(provider)
            base_url = cfg.get("chat_base_url") or get_base_url(provider)
            engine = ChatEngine("openai", cfg["chat_model"], base_url=base_url, api_key=api_key)
        return RAGAssistant(idx, engine, cfg["system_prompt"], cfg["top_k"],
                            cfg.get("search_scope", "doc"))

    # ============================================================
    # 索引
    # ============================================================
    def _build_index(self, full: bool = False):
        cfg = load_config()
        root = cfg.get("personal_space", "")
        if not root:
            self.toast("先在右侧选择个人空间文件夹")
            self.choose_space()
            return
        if self._indexing:
            self.toast("索引正在构建中，请等它跑完")
            return
        n = getattr(self, "_file_count", 0)
        if n > 800 and not getattr(self, "_warned_big_space", False):
            self._warned_big_space = True
            box = QMessageBox(self)
            box.setWindowTitle("个人空间很大")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(f"这个文件夹里有 {n} 个可索引文件")
            box.setInformativeText(
                "首次构建要逐个文件算向量，可能要几十分钟甚至更久，期间可以照常使用其它功能。\n"
                "建议只把真正要问答的笔记/文档目录设为个人空间，代码仓库、下载目录这类不必放进来。")
            go = box.addButton("继续构建", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("换个小一点的文件夹", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is go:
                self._start_index_build(root, full)
            elif box.clickedButton() is not None:
                self.choose_space()
            return
        self._start_index_build(root, full)

    def _start_index_build(self, root: str, full: bool) -> None:
        self._indexing = True
        self._set_index_ui(True)
        self._set_index_progress(None, "正在准备索引…")

        def worker():
            try:
                with self.index_lock:
                    if full:
                        for name in ("index.faiss", "vectors.npy", "metadata.json"):
                            try:
                                (INDEX_DIR / name).unlink(missing_ok=True)
                            except OSError:
                                pass
                        self.indexer = None
                        self.assistant = None
                    idx = self._get_indexer()
                    if idx is None:
                        detail = getattr(self, "embedder_error", "") or "请到设置中检查模型与设备选择。"
                        self.later(lambda d=detail: self._index_failed(
                            "Embedding 模型加载失败：" + d))
                        return
                    if full or idx.store.size == 0:
                        stats = idx.build(root, progress_cb=self._index_progress)
                    else:
                        stats = idx.incremental_update(root, progress_cb=self._index_progress)
                cfg2 = load_config()
                cfg2["index_embedding_model"] = cfg2.get("embedding_model", "")
                save_config(cfg2)
                self.later(lambda s=stats: self._index_done(s))
            except Exception as e:
                self.later(lambda m=str(e): self._index_failed(f"索引失败：{m}"))

        threading.Thread(target=worker, daemon=True).start()

    def _set_index_ui(self, running: bool) -> None:
        for b in (getattr(self, "kb_build_btn", None), getattr(self, "rebuild_btn", None)):
            if b is not None:
                b.setEnabled(not running)
                b.setText("⏳  索引构建中…" if running else "⚡  构建 / 更新索引")
        if not running:
            self.kb_progress.hide()

    def _set_index_progress(self, pct, text: str) -> None:
        def apply():
            self.status_label.setText(text)
            self.kb_progress.show()
            if pct is None:
                self.kb_progress.setRange(0, 0)
            else:
                self.kb_progress.setRange(0, 100)
                self.kb_progress.setValue(int(pct))
        self.later(apply)

    def _index_done(self, stats: dict) -> None:
        self._indexing = False
        self._set_index_ui(False)
        msg = (f"索引完成：{stats.get('chunks', 0)} 个片段 · {stats.get('files', 0)} 个文件"
               if "chunks" in stats and "files" in stats else "索引已更新")
        self.status_label.setText(msg)
        self.toast(msg)
        self._refresh_index_state()
        self.refresh_file_tree()

    def _index_failed(self, msg: str) -> None:
        self._indexing = False
        self._set_index_ui(False)
        self.status_label.setText(msg[:80])
        self.toast(msg, ms=4000)
        self._refresh_index_state()

    def _index_progress(self, msg, done, total):
        pct = (done / total * 100) if total else None
        self._set_index_progress(pct, msg)

    def _auto_index_loop(self):
        while True:
            time.sleep(60)
            try:
                cfg = load_config()
                if not cfg.get("auto_index", True) or not cfg.get("personal_space"):
                    continue
                with self.index_lock:
                    idx = self._get_indexer()
                    if idx is None or idx.store.size == 0:
                        continue
                    stats = idx.incremental_update(cfg["personal_space"])
                if stats["added"] or stats["removed_files"]:
                    self.later(lambda s=stats: self.status_label.setText(
                        f"已自动更新索引：+{s['added']} 片段，移除 {s['removed_files']} 个文件"))
            except Exception:
                pass

    # ============================================================
    # 主题
    # ============================================================
    def _apply_theme(self, name: str, from_toggle: bool = False) -> None:
        if name not in ("dark", "light") or (name == self.theme and not from_toggle):
            return
        self.theme = name
        if theme.manager is not None:
            theme.manager.apply_immediate(name == "dark")
        cfg = load_config()
        cfg["theme"] = name
        save_config(cfg)
        self._update_theme_button()
        # Markdown 回答的配色是渲染时写进 HTML 的，换主题必须重画一遍
        self._render_chat()

    def _toggle_theme(self):
        # 手动切过一次之后就不再被 LifeSystem 的同步文件拽回去，
        # 否则用户点🌙会在 1.2 秒内被轮询覆盖，看起来像按钮坏了。
        self._theme_pinned = True
        cfg = load_config()
        cfg["theme_pinned"] = True
        save_config(cfg)
        self._apply_theme("dark" if self.theme == "light" else "light", from_toggle=True)

    def _update_theme_button(self):
        if self.theme == "light":
            self.theme_btn.setText("🌙")
            self.theme_btn.setToolTip("切换到夜间主题")
        else:
            self.theme_btn.setText("☀️")
            self.theme_btn.setToolTip("切换到日间主题")

    def _theme_sync_loop(self):
        if getattr(self, "_theme_pinned", False):
            return
        v = theme.read_sync_theme()
        if v in ("dark", "light") and v != self.theme:
            self._apply_theme(v)

    # ============================================================
    # 托盘 / 关闭 / 内嵌
    # ============================================================
    def _setup_tray(self):
        self._embed_mode = "--tray" in sys.argv
        self.tray = None
        # 内嵌监听：被 LifeSystem 用 Win32 SetParent 内嵌后，Qt 内部仍认为窗口
        # 隐藏（backing store 不渲染 → 内容空白）。轮询检测父窗口变化，在被
        # 内嵌时于 Qt 层调用 show() 恢复渲染（等价于原 Tk 版的 _embed_watch_loop）。
        if self._embed_mode and os.name == "nt":
            self._embed_watch_timer = QTimer(self)
            self._embed_watch_timer.timeout.connect(self._embed_watch_tick)
            self._embed_watch_timer.start(400)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = self.windowIcon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("RAG 文件助手")
        menu = QMenu()
        open_act = QAction("打开", self)
        open_act.triggered.connect(self._open_from_tray)
        menu.addAction(open_act)
        menu.addSeparator()
        quit_act = QAction("退出", self)
        quit_act.triggered.connect(self._really_quit)
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._open_from_tray()

    def _embed_watch_tick(self):
        """内嵌模式轮询：被 LifeSystem SetParent 后在 Qt 层恢复显示（触发渲染）；
        若 LifeSystem 宿主被强杀销毁，则把本窗口还原为独立窗口继续运行。"""
        if self._quitting:
            return
        try:
            import ctypes
            u32 = ctypes.windll.user32
            hwnd = int(self.winId())
            parent = u32.GetParent(hwnd)
            if parent:
                if not u32.IsWindow(parent):
                    # 父窗口（LifeSystem）已销毁 → 脱离内嵌，作为独立窗口继续运行
                    self._detach_from_embed()
                    return
                if not self.isVisible():
                    self.show()
        except Exception:
            pass

    def _detach_from_embed(self) -> bool:
        """把被 LifeSystem 内嵌（SetParent 成子窗口）的本窗口还原为独立顶层窗口。

        点托盘「打开」、宿主被强杀自愈两条路径都走这里。
        返回是否真的执行了脱离（本就独立时返回 False）。
        """
        if os.name != "nt":
            return False
        try:
            import ctypes
            u32 = ctypes.WinDLL("user32", use_last_error=True)
            hwnd = int(self.winId())
            if not hwnd or not u32.IsWindow(hwnd):
                return False
            parent = u32.GetParent(hwnd)
            if not parent or parent == u32.GetDesktopWindow():
                return False  # 未被内嵌（parent 为 0 / 桌面）
            GWL_STYLE = -16
            u32.GetWindowLongW.restype = ctypes.c_uint32
            u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            u32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
            style = u32.GetWindowLongW(hwnd, GWL_STYLE)
            WS_CAPTION = 0x00C00000
            WS_THICKFRAME = 0x00040000
            WS_CHILD = 0x40000000
            WS_SYSMENU = 0x00080000
            # 注意：不要加 WS_POPUP(0x80000000)。窗口原本是 WS_OVERLAPPED 类型，
            # 加了之后 style 高位会被 ctypes 解释成有符号负数，导致 LifeSystem
            # 之后再次内嵌时 SetWindowLongW 参数溢出、内嵌失败。
            new_style = (style | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU) & ~WS_CHILD
            u32.SetWindowLongW(hwnd, GWL_STYLE, new_style)
            u32.SetParent(hwnd, 0)
            # 必须带 SWP_FRAMECHANGED 重算非客户区，否则上面改的样式不生效
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                             SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER)
            u32.ShowWindow(hwnd, 9)  # SW_RESTORE
            # Qt 层同步：窗口被外部 SetParent 后 Qt 仍按顶层窗口管理，
            # 这里显式 show/raise 才能让 backing store 重新渲染
            self.show()
            self.raise_()
            return True
        except Exception:
            return False

    def _open_from_tray(self):
        # 关键：若正被 LifeSystem 内嵌（SetParent 成子窗口），必须先还原成独立
        # 顶层窗口，否则下面的 show/raise 只会把窗口继续显示在内嵌区里，
        # 看起来就是「点了托盘图标没反应」。与 Arxiver / ApiCluster 保持一致：
        # 内嵌 XOR 独立，同一时刻只有一个界面。
        detached = self._detach_from_embed()
        self._move_onscreen(clamp=detached)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._force_foreground()

    def _force_foreground(self):
        """Windows 下 raise_/activateWindow 有时抢不到前台，兜底用 Win32 API。"""
        if os.name != "nt":
            return
        try:
            import ctypes
            u32 = ctypes.WinDLL("user32", use_last_error=True)
            hwnd = int(self.winId())
            if hwnd and u32.IsWindow(hwnd):
                u32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _move_onscreen(self, clamp: bool = False):
        """确保窗口落在屏幕可见区域。

        被内嵌时窗口坐标由 LifeSystem 控制，不做任何干预；脱离内嵌（clamp=True）
        后坐标可能仍停留在内嵌区，需要夹回屏幕可用区域。
        """
        try:
            app = QApplication.instance()
            if app is None:
                return
            g = self.frameGeometry()
            # 极端离屏坐标（隐藏时被移出去的兜底）
            if g.x() <= -30000 or g.y() <= -30000 or g.x() >= 9000 or g.y() >= 9000:
                self.move(100, 100)
                if not clamp:
                    return
                g = self.frameGeometry()
            if not clamp:
                return
            scr = app.screenAt(g.center()) or app.primaryScreen()
            if scr is None:
                return
            avail = scr.availableGeometry()
            w = max(min(g.width(), avail.width() - 20), 400)
            h = max(min(g.height(), avail.height() - 20), 300)
            x = min(max(g.x(), avail.x()), max(avail.x(), avail.right() - w))
            y = min(max(g.y(), avail.y()), max(avail.y(), avail.bottom() - h))
            self.setGeometry(x, y, w, h)
        except Exception:
            pass

    def _really_quit(self):
        self._quitting = True
        self._flush_note()
        if self.tray is not None:
            self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event: QCloseEvent):
        self._flush_note()
        if self._quitting:
            event.accept()
            return
        if self.tray is not None:
            # 统一「关闭到托盘」：独立模式从托盘恢复；被 LifeSystem 内嵌/脱离后，
            # LifeSystem 检测到窗口隐藏（IsWindowVisible=False）会自动重新内嵌。
            event.ignore()
            self.hide()
            return
        event.accept()

    # ============================================================
    # 快捷键
    # ============================================================
    def _setup_shortcuts(self):
        from PySide6.QtGui import QShortcut
        QShortcut(QKeySequence("Ctrl+N"), self, activated=lambda: (self.new_conv(), self.show_page("chat")))
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: self.show_page("find"))
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.show_page("find"))
        QShortcut(QKeySequence("Ctrl+,"), self, activated=lambda: self.show_page("settings"))
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self.toggle_sidebar)
        QShortcut(QKeySequence("Ctrl+I"), self, activated=lambda: self._build_index())
        QShortcut(QKeySequence("Esc"), self, activated=self._on_escape)

    def _on_escape(self):
        """Esc 分层退出：正在回答→停止；焦点在输入框→清空它；否则→什么都不做。"""
        if self._answering:
            self.stop_answer()
            return
        focus = self.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit)):
            text = focus.text() if isinstance(focus, QLineEdit) else focus.toPlainText()
            if text.strip():
                focus.clear()
                return
        if self.chat_input.toPlainText().strip():
            self.chat_input.clear()
            self.toast("已清空提问输入框")
