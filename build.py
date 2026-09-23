"""一键打包脚本（轻量启动器方案）。

exe 只包含启动器（bootstrap.py，标准库 tkinter）+ 主程序源码（app/），
Python 环境与依赖不打包，由启动器在用户机器首次运行时自动初始化。

用法：
    python build.py               # 目录模式（推荐）
    python build.py --onefile     # 单文件模式
    python build.py --console     # 保留控制台窗口（调试用）
    python build.py --clean       # 强制全量重分析

改代码后重新运行本脚本即可更新 exe（打包很快，约 1 分钟）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "RAGAssistant"


def _add_data(src: str, dest: str) -> str:
    sep = ";" if os.name == "nt" else ":"
    return f"{src}{sep}{dest}"


def _remove_dir(path: Path) -> None:
    if not path.exists():
        return
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(path)], check=False)
    else:
        shutil.rmtree(path, ignore_errors=True)


def _runtime_binaries() -> list[str]:
    """conda 环境必须打包的运行时 DLL：
    - Tcl/Tk：否则 _tkinter 加载失败；
    - OpenSSL（libssl/libcrypto）：否则 exe 内 Python 的 urllib 无法发起
      https 请求（报 unknown url type: https），导致初始化无法下载任何东西。
    """
    lib_dir = Path(sys.executable).parent / "Library" / "bin"
    if not lib_dir.exists():
        return []
    items = []
    for name in ("tcl86t.dll", "tk86t.dll", "libssl-3-x64.dll", "libcrypto-3-x64.dll",
                 "libssl-1_1-x64.dll", "libcrypto-1_1-x64.dll"):
        p = lib_dir / name
        if p.exists():
            items += ["--add-binary", f"{p}{os.pathsep}."]
    return items


def build() -> None:
    onefile = "--onefile" in sys.argv
    console = "--console" in sys.argv
    clean = "--clean" in sys.argv

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--name", APP_NAME]
    if clean:
        cmd.append("--clean")
    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")
    if not console:
        cmd.append("--windowed")

    icon = ROOT / "assets" / "icon.ico"
    if icon.exists():
        cmd += ["--icon", str(icon)]

    # 主程序源码打包为 app/ 资源（启动器初始化时解压到环境目录）。
    # 注意：PyInstaller 的 --add-data 遇到「目录源」会复制目录内内容到 dest，
    # 因此 core 必须显式落到 app/core、assets 落到 app/assets，否则包结构被摊平，
    # 主程序 `from core.config import ...` 会报 ModuleNotFoundError。
    cmd += ["--add-data", _add_data("main.py", "app")]
    cmd += ["--add-data", _add_data("core", "app/core")]
    cmd += ["--add-data", _add_data("ui", "app/ui")]
    cmd += ["--add-data", _add_data("assets", "app/assets")]

    # conda 运行时 DLL（Tcl/Tk + OpenSSL）
    runtime_dlls = _runtime_binaries()
    if runtime_dlls:
        print("[build] 打包运行时 DLL（Tcl/Tk + OpenSSL）…")
        cmd += runtime_dlls

    cmd += [str(ROOT / "bootstrap.py")]

    # 清理旧输出
    out_dir = ROOT / "dist" / APP_NAME
    if out_dir.exists():
        print("[build] 删除旧输出目录…")
        _remove_dir(out_dir)
    for bak in (ROOT / "dist").glob("*.bak"):
        _remove_dir(bak)

    print("[build] 正在打包启动器（轻量，很快）…")
    subprocess.run(cmd, cwd=ROOT, check=True)

    _remove_dir(ROOT / "build")
    for spec in ROOT.glob("*.spec"):
        try:
            spec.unlink()
        except OSError:
            pass

    if onefile:
        out = ROOT / "dist" / f"{APP_NAME}.exe"
    else:
        out = ROOT / "dist" / APP_NAME / f"{APP_NAME}.exe"
        readme = ROOT / "使用说明.txt"
        if readme.exists():
            shutil.copy2(readme, ROOT / "dist" / APP_NAME / "使用说明.txt")
    print(f"\n[build] ✅ 打包完成：{out}")
    print("[build] 提示：")
    print("  - exe 为轻量启动器，首次运行会自动初始化 Python 环境（默认 D:\\RAGAssistant）。")
    print("  - 初始化需要联网下载 Python 与依赖（约 300MB）。")


if __name__ == "__main__":
    build()
