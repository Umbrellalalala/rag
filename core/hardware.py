"""硬件信息检测模块。

读取本机 CPU、内存、GPU（NVIDIA / Apple Silicon）配置，
用于后续推荐合适的 embedding / chat 模型。
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Any

# Windows 下隐藏 subprocess 弹出的控制台窗口
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def get_cpu_info() -> dict[str, Any]:
    """获取 CPU 信息（型号、物理核、逻辑核、主频）。"""
    info: dict[str, Any] = {
        "name": platform.processor() or "Unknown CPU",
        "physical_cores": None,
        "logical_cores": os_cpu_count(),
        "frequency_mhz": None,
        "arch": platform.machine(),
    }
    try:
        import psutil

        info["physical_cores"] = psutil.cpu_count(logical=False)
        info["logical_cores"] = psutil.cpu_count(logical=True)
        freq = psutil.cpu_freq()
        if freq:
            info["frequency_mhz"] = round(freq.max or freq.current)
    except Exception:
        pass
    return info


def os_cpu_count() -> int | None:
    import os

    return os.cpu_count()


def get_memory_info() -> dict[str, Any]:
    """获取内存总量（GB）。"""
    info: dict[str, Any] = {"total_gb": None}
    try:
        import psutil

        vm = psutil.virtual_memory()
        info["total_gb"] = round(vm.total / (1024 ** 3), 1)
    except Exception:
        pass
    return info


def _query_nvidia_smi() -> list[dict[str, Any]]:
    """通过 nvidia-smi 读取 NVIDIA GPU 列表（无需额外依赖）。"""
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            encoding="utf-8",
            errors="ignore",
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        return []
    gpus: list[dict[str, Any]] = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[1].replace(".", "").isdigit():
            gpus.append(
                {
                    "name": parts[0],
                    "memory_mb": int(float(parts[1])),
                    "driver": parts[2] if len(parts) > 2 else "N/A",
                    "vendor": "NVIDIA",
                }
            )
    return gpus


def _query_pynvml() -> list[dict[str, Any]]:
    """通过 pynvml 读取 NVIDIA GPU（可选依赖）。"""
    try:
        import pynvml

        pynvml.nvmlInit()
        gpus: list[dict[str, Any]] = []
        for i in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            gpus.append(
                {
                    "name": name.decode() if isinstance(name, bytes) else name,
                    "memory_mb": int(mem.total / (1024 ** 2)),
                    "driver": "N/A",
                    "vendor": "NVIDIA",
                }
            )
        pynvml.nvmlShutdown()
        return gpus
    except Exception:
        return []


def _query_apple_silicon() -> list[dict[str, Any]]:
    """检测 Apple Silicon（M 系列芯片，可用 MPS 加速）。"""
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return [
            {
                "name": "Apple Silicon (M-series, MPS)",
                "memory_mb": None,
                "driver": "Metal",
                "vendor": "Apple",
            }
        ]
    return []


def get_gpu_info() -> list[dict[str, Any]]:
    """获取 GPU 列表，按优先级尝试多种检测方式。"""
    gpus = _query_nvidia_smi()
    if not gpus:
        gpus = _query_pynvml()
    if not gpus:
        gpus = _query_apple_silicon()
    return gpus


def get_hardware_summary() -> dict[str, Any]:
    """汇总硬件信息，并给出一个粗粒度的算力等级。"""
    cpu = get_cpu_info()
    mem = get_memory_info()
    gpus = get_gpu_info()

    total_vram_mb = 0
    for g in gpus:
        if g.get("memory_mb"):
            total_vram_mb += int(g["memory_mb"])

    # 算力等级：high / medium / low / cpu
    if total_vram_mb >= 16 * 1024:
        tier = "high"
    elif total_vram_mb >= 8 * 1024:
        tier = "medium"
    elif total_vram_mb >= 4 * 1024:
        tier = "low"
    elif gpus:  # 有 GPU 但显存很小，或 Apple Silicon
        tier = "low"
    else:
        tier = "cpu"

    return {
        "cpu": cpu,
        "memory": mem,
        "gpus": gpus,
        "total_vram_gb": round(total_vram_mb / 1024, 1) if total_vram_mb else None,
        "tier": tier,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(get_hardware_summary(), ensure_ascii=False, indent=2))
