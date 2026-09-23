"""自动检测 GPU 并安装依赖（轻量 fastembed / onnxruntime 方案）。

- 有 NVIDIA GPU：安装 onnxruntime-gpu（CUDA 12 版，需要驱动 >= 550、系统 CUDA 12.x 库、cuDNN 9）
- 无 GPU：安装 onnxruntime（CPU 版）

用法：
    python install.py           # 自动检测
    python install.py --gpu     # 强制安装 GPU 版 onnxruntime
    python install.py --cpu     # 强制安装 CPU 版 onnxruntime
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIP_MIRROR = os.environ.get("PIP_MIRROR", "https://pypi.tuna.tsinghua.edu.cn/simple")
# onnxruntime-gpu 的 CUDA 12 版本源（匹配系统 CUDA 12.5 + cuDNN 9.4）
ORT_CUDA12_INDEX = "https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-12/pypi/simple/"
ORT_GPU_VERSION = "1.19.2"


def run(cmd: list[str]) -> bool:
    print("[install]", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT).returncode == 0


def detect_nvidia_gpu() -> bool:
    """检测是否存在 NVIDIA GPU 且驱动支持 CUDA。"""
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        out = subprocess.check_output(
            ["nvidia-smi"], encoding="utf-8", errors="ignore", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return False
    m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", out)
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2))
    # 需要驱动支持 CUDA 12+（onnxruntime CUDA 12 版）
    return major >= 12


def _gpu_available() -> bool:
    code = (
        "import onnxruntime as ort; "
        "print('CUDAExecutionProvider' in ort.get_available_providers())"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    return "True" in r.stdout


def install_ort_gpu() -> bool:
    print(f"[install] 安装 onnxruntime-gpu {ORT_GPU_VERSION}（CUDA 12 版）…")
    run([sys.executable, "-m", "pip", "install", "--no-cache-dir",
         f"onnxruntime-gpu=={ORT_GPU_VERSION}",
         "--extra-index-url", ORT_CUDA12_INDEX])
    return _gpu_available()


def install_ort_cpu() -> None:
    print("[install] 安装 onnxruntime（CPU 版）…")
    run([sys.executable, "-m", "pip", "install", "-i", PIP_MIRROR, "onnxruntime"])


def main() -> None:
    force = None
    if "--gpu" in sys.argv or "--cuda" in sys.argv:
        force = "gpu"
    elif "--cpu" in sys.argv:
        force = "cpu"

    use_gpu = False
    if force == "gpu":
        use_gpu = True
    elif force == "cpu":
        use_gpu = False
    else:
        use_gpu = detect_nvidia_gpu()

    if use_gpu:
        print("[install] 检测到 NVIDIA GPU，安装 GPU 版 onnxruntime…")
        if not install_ort_gpu():
            print("[install] ⚠️ GPU 版 onnxruntime 不可用（可能缺 CUDA 库或 cuDNN），回退 CPU 版。")
            install_ort_cpu()
    else:
        print("[install] 未检测到 NVIDIA GPU，安装 CPU 版 onnxruntime。")
        install_ort_cpu()

    print("[install] 安装其余依赖 …")
    run([sys.executable, "-m", "pip", "install", "-i", PIP_MIRROR, "-r", str(ROOT / "requirements.txt")])

    print("[install] ✅ 全部依赖安装完成。")
    if use_gpu and _gpu_available():
        print("[install] GPU 加速已就绪（CUDAExecutionProvider 可用）。")
        print("[install] 注意：onnxruntime-gpu 需要系统有 CUDA 12.x 库与 cuDNN 9 在 PATH 中。")


if __name__ == "__main__":
    main()
