"""RAG 文件助手 —— PySide6 桌面版入口。

运行：python main.py
特性：日间/夜间模式（默认日间）、扁平卡片式 UI、对话 + 知识库文件树 + 笔记。
内嵌：--tray 参数启动即隐藏，供 LifeSystem 用 Win32 SetParent 内嵌。
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from core.config import MIGRATED_FROM, USER_DIR, load_config
from ui import theme
from ui.main_window import MainWindow, resource_path

APP_TITLE = "RAG 文件助手"
APP_USER_MODEL_ID = "RAGAssistant.App.1.0"


def _acquire_single_instance() -> bool:
    """Windows 命名互斥量实现单实例：已在运行则返回 False。"""
    if os.name != "nt":
        return True
    try:
        import ctypes
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, "RAGAssistant_SingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return False
        globals()["_single_instance_mutex"] = handle  # 保持引用，防止句柄被释放
        return True
    except Exception:
        return True


def main() -> int:
    if not _acquire_single_instance():
        return 0  # 已在运行（可能正被 life_system 内嵌），静默退出避免多开
    # 数据目录迁移（core.config 导入时已完成）；此处仅记录来源，便于排查
    if MIGRATED_FROM:
        print(f"[RAG] 已将数据迁移到持久化目录 {USER_DIR}（来源：{MIGRATED_FROM}）")
    cfg = load_config()
    models_dir = cfg.get("models_dir") or str(USER_DIR / "models")
    os.environ["HF_HOME"] = models_dir
    os.environ["TRANSFORMERS_CACHE"] = models_dir
    # 国内网络默认走 HF 镜像，否则别人电脑首次下载 embedding 模型会连不上
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setFont(QFont("Microsoft YaHei UI", 10))

    # Windows 任务栏图标分组（否则显示 Python 图标）
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass

    # 主题管理器（日间为默认）
    theme.manager = theme.ThemeManager(app)
    saved_dark = cfg.get("theme") == "dark"
    theme.manager.apply_immediate(saved_dark)

    # 应用图标
    icon_path = resource_path("assets/icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.setWindowIcon(app.windowIcon())

    if "--tray" in sys.argv:
        # 内嵌预热：窗口先显示（注册 HWND 与标题）后立即隐藏。
        # 与 Tk 不同，Qt 的 hide 是持久的（不会自动重新显示），LifeSystem 通过
        # EnumWindows 能找到隐藏窗口并 SetParent + SW_SHOW 内嵌，无需移屏幕外
        # （Win32 会把窗口坐标 clamp 到约 -21845，移屏幕外方案在 Qt 下不可靠）。
        window.show()
        window.hide()
    else:
        window.showMaximized()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
