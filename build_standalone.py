"""单层架构打包脚本：直接把主程序 main.py 及其全部依赖打包成独立 exe（无启动器层）。

与旧「轻量启动器」方案（build.py）的区别：
- 旧：exe 只含启动器，首次运行需联网下载 Python+依赖（约300MB），两层架构启动慢
- 新：exe 直接包含主程序与全部依赖（CPU onnxruntime），开箱即用、单层启动快

用法（用便携环境运行，因为它已装好 CPU 依赖）：
    D:\\RAGAssistant\\python\\python.exe build_standalone.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "RAGAssistant"
SEP = ";" if os.name == "nt" else ":"


def build() -> None:
    onefile = "--onefile" in sys.argv
    console = "--console" in sys.argv

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--name", APP_NAME]
    cmd.append("--onefile" if onefile else "--onedir")
    if not console:
        cmd.append("--windowed")

    icon = ROOT / "assets" / "icon.ico"
    if icon.exists():
        cmd += ["--icon", str(icon)]

    # 数据文件（图标等）
    cmd += ["--add-data", f"assets{SEP}assets"]

    # 收集动态依赖（lazy import / 数据文件 / 原生 DLL）
    # PySide6 由 PyInstaller 内置 hook 处理（Qt DLL + plugins），无需 collect-all
    for mod in ("fastembed", "onnxruntime", "tokenizers", "PIL"):
        cmd += ["--collect-all", mod]

    cmd += [str(ROOT / "main.py")]

    # 清理旧输出
    out_dir = ROOT / "dist" / APP_NAME
    if out_dir.exists():
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(out_dir)], check=False)
    shutil.rmtree(ROOT / "build", ignore_errors=True)

    print("[build] 正在打包单层 exe（含全部依赖，约 1-3 分钟）…")
    subprocess.run(cmd, cwd=ROOT, check=True)

    shutil.rmtree(ROOT / "build", ignore_errors=True)
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
    print(f"\n[build] 打包完成：{out}")


if __name__ == "__main__":
    build()
