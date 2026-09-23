"""RAG 文件助手 —— 环境初始化启动器（轻量 exe）。

首次启动：自动下载便携版 Python 并安装依赖到指定目录（默认 D:\\RAGAssistant）；
环境就绪后：直接用该环境运行主程序（app/main.py），本启动器退出。

说明：本启动器只用标准库（tkinter），打包后体积很小；
Python 环境与依赖不打包进 exe，运行时才初始化。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "RAG 文件助手"
DEFAULT_ENV_DIR = r"D:\RAGAssistant"
PY_VERSION = "3.11.9"
PY_URL = f"https://mirrors.tuna.tsinghua.edu.cn/python/{PY_VERSION}/python-{PY_VERSION}-amd64.exe"
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
ORT_GPU_VERSION = "1.19.2"

# 依赖清单（CPU 基线）
REQUIREMENTS_CPU = [
    "numpy",
    "psutil",
    "python-dotenv",
    "requests",
    "openai",
    "fastembed",
    "huggingface_hub<1.0.0",
    "PySide6",
    # 个人空间一大，向量就只存在 index.faiss 里（numpy 回退方案每次检索都要
    # 重新 vstack 上百万条向量，慢到不可用）。缺了它就读不了已有索引。
    "faiss-cpu",
]


def _cuda_dll_dirs() -> list[str]:
    """返回系统 CUDA 库与 cuDNN 的 DLL 目录（用于加入主程序进程的 PATH）。"""
    dirs: list[str] = []
    cuda_root = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
    if cuda_root.exists():
        for v in sorted(cuda_root.glob("v*"), reverse=True):
            if (v / "bin" / "cublas64_12.dll").exists():
                dirs.append(str(v / "bin"))
                break
    cudnn_root = Path(r"C:\Program Files\NVIDIA\CUDNN")
    if cudnn_root.exists():
        for v in sorted(cudnn_root.glob("v*"), reverse=True):
            for sub in sorted((v / "bin").glob("*"), reverse=True):
                if (sub / "cudnn64_9.dll").exists():
                    dirs.append(str(sub))
                    break
            if any("CUDNN" in d for d in dirs):
                break
    return dirs


def _system_cuda_available() -> bool:
    """检测系统是否已安装 CUDA 库（cublas）与 cuDNN（onnxruntime GPU 加速的前置条件）。"""
    dirs = _cuda_dll_dirs()
    return len(dirs) >= 2  # 需要 cublas 目录 + cuDNN 目录


def _launch_env() -> dict:
    """构造启动主程序的子进程环境（PATH 注入 CUDA/cuDNN 目录）。

    注意：清除启动器打包环境设置的 TCL_LIBRARY / TK_LIBRARY，
    否则主程序 Python 的 tkinter 会与打包的 Tcl 版本冲突报错。
    """
    env = os.environ.copy()
    for k in ("TCL_LIBRARY", "TK_LIBRARY"):
        env.pop(k, None)
    extra = _cuda_dll_dirs()
    if extra:
        env["PATH"] = os.pathsep.join(extra) + os.pathsep + env.get("PATH", "")
    # 把启动器（exe）绝对路径传给主程序，用于「开机自启」正确定位入口
    if getattr(sys, "frozen", False):
        env["RAG_LAUNCHER"] = sys.executable
    return env


def app_dir() -> Path:
    """本启动器所在目录（打包后为 exe 目录，源码运行为本文件目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    """打包资源目录（_MEIPASS），未打包时为源码目录。"""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent


def launcher_cfg_path() -> Path:
    return app_dir() / "launcher.json"


def load_launcher_cfg() -> dict:
    p = launcher_cfg_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
    return {}


def save_launcher_cfg(cfg: dict) -> None:
    try:
        launcher_cfg_path().write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def env_python(env_dir: Path) -> Path:
    return env_dir / "python" / "python.exe"


def env_pythonw(env_dir: Path) -> Path:
    """无控制台版 Python（GUI 启动用，避免弹出黑框）。"""
    return env_dir / "python" / "pythonw.exe"


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def env_ready(env_dir: Path) -> bool:
    """检查环境是否就绪（python 存在且关键包已安装）。

    只做文件系统级检查，绝不 import 重包（fastembed 会加载 onnxruntime，
    每次启动都 import 会白白拖慢几秒），实现秒级判断。
    """
    py = env_python(env_dir)
    if not py.exists():
        return False
    sp = env_dir / "python" / "Lib" / "site-packages"
    if not sp.exists():
        return False
    entries = {p.name.lower() for p in sp.iterdir()}
    for pkg in ("fastembed", "pyside6"):
        if pkg not in entries and not any(
            e.startswith(pkg + "-") or e.startswith(pkg + "_") for e in entries
        ):
            return False
    return True


def launch_app(env_dir: Path) -> None:
    """启动主程序 GUI：用 pythonw.exe（无控制台），杜绝 python 黑框。

    当启动器带 --tray（注册表开机自启）时，把 --tray 透传给主程序，使其
    启动即缩到系统托盘（不弹独立窗口）；手动双击启动器则不传、正常显示窗口。
    """
    pyw = env_pythonw(env_dir)
    main_py = env_dir / "app" / "main.py"
    tray_flag = ["--tray"] if "--tray" in sys.argv else []
    if pyw.exists():
        subprocess.Popen([str(pyw), str(main_py), *tray_flag],
                         cwd=str(main_py.parent), env=_launch_env())
    else:
        subprocess.Popen([str(env_python(env_dir)), str(main_py), *tray_flag],
                         cwd=str(main_py.parent), env=_launch_env(),
                         creationflags=_NO_WINDOW)


def detect_nvidia_gpu() -> bool:
    """检测 NVIDIA GPU 且驱动支持 CUDA 12+（兼容新旧 nvidia-smi 输出格式）。"""
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        out = subprocess.run(
            ["nvidia-smi"], capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode != 0:
            return False
        import re

        text = out.stdout or out.stderr
        # 新版：CUDA UMD Version: 13.4；旧版：CUDA Version: 12.1
        m = re.search(r"CUDA (?:UMD )?Version:\s*(\d+)\.(\d+)", text)
        if not m:
            return False
        return int(m.group(1)) >= 12
    except Exception:
        return False


def _http_download_impl(url: str, dest: Path) -> bool:
    """用 urllib 下载（带浏览器 UA）。返回是否成功。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        return dest.exists() and dest.stat().st_size > 0
    except Exception:
        return False


def download(url: str, dest: Path, progress_cb=None) -> None:
    """下载文件，多级兜底，失败抛异常。

    顺序：系统 curl → PowerShell(WebClient/.NET) → urllib。
    原因：exe 内 Python 可能缺少 OpenSSL（https 不可用），
    因此优先用系统自带工具（curl / .NET 均有独立 TLS 实现）；
    前两者都失败才尝试 urllib。
    """
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    last_err: Exception | None = None

    # 1) 系统 curl（Windows 10 1803+ 自带）
    curl = shutil.which("curl")
    if curl:
        try:
            r = subprocess.run(
                [curl, "-sL", "--connect-timeout", "15", "-o", str(dest), url],
                capture_output=True, timeout=600, creationflags=creationflags)
            if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                if progress_cb:
                    progress_cb(dest.stat().st_size, dest.stat().st_size)
                return
            dest.unlink(missing_ok=True)
        except Exception as e:
            last_err = e

    # 2) PowerShell WebClient（.NET，自带 TLS，几乎任何 Win 都有）
    try:
        ps = ("$ErrorActionPreference='Stop';"
              "(New-Object System.Net.WebClient).DownloadFile('{0}','{1}')").format(url, dest)
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, timeout=600, creationflags=creationflags)
        if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            if progress_cb:
                progress_cb(dest.stat().st_size, dest.stat().st_size)
            return
        dest.unlink(missing_ok=True)
    except Exception as e:
        last_err = e

    # 3) urllib（exe 内含 ssl 时可用；已由打包脚本补入 OpenSSL 库）
    if _http_download_impl(url, dest):
        if progress_cb:
            progress_cb(dest.stat().st_size, dest.stat().st_size)
        return

    raise RuntimeError(f"下载失败：{url}（curl/WebClient/urllib 均不可用，{last_err or ''}）")


# ---------------------------------------------------------------
# 便携版 Python（python-build-standalone）
# 新用户机器上通常没有 Python，不能依赖检测；本方案自带 Python：
# 下载 tar.gz → 解压即用（无官方安装器自解压 / MSI，快 5–10 倍）。
# 包含完整标准库（tkinter/tcl）、pip。
# ---------------------------------------------------------------
PBS_TAG = "20260901"
PBS_FILE = f"cpython-3.11.16+{PBS_TAG}-x86_64-pc-windows-msvc-install_only.tar.gz"
# 依次尝试多个 GitHub 加速镜像（短超时快速切换）；全部失败时由调用方回退官方安装器。
# 注：国内网络可能连不上 GitHub 及其镜像，此时会快速失败并自动回退清华官方安装器。
_PBS_BASE = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/{PBS_FILE}"
PBS_URLS = [
    f"https://gh-proxy.com/{_PBS_BASE}",
    f"https://ghfast.top/{_PBS_BASE}",
    f"https://gh.llkk.cc/{_PBS_BASE}",
    f"https://github.moeyy.xyz/{_PBS_BASE}",
    _PBS_BASE,
]


def install_python_standalone(env_dir: Path, log_fn=None, status_cb=None, monitor_fn=None,
                              detail_log_fn=None) -> Path:
    """下载并解压便携 Python 运行环境，返回 python.exe 路径；失败抛 RuntimeError。

    对普通用户只输出友好文案（log_fn）；技术细节（下载地址/节点/文件名等）
    走 detail_log_fn（UI 默认不打印，排障时可打开）。
    """
    import tarfile

    log_fn = log_fn or (lambda m: None)
    status_cb = status_cb or (lambda m, p: None)
    detail_log_fn = detail_log_fn or (lambda m: None)
    env_dir.mkdir(parents=True, exist_ok=True)
    py_dir = env_dir / "python"
    py_exe = py_dir / "python.exe"

    # 下载（依次尝试多个通道，任一个成功即继续）
    archive = env_dir / "_python_standalone.tar.gz"
    last_err = None
    first = True
    for idx, url in enumerate(PBS_URLS, 1):
        if first:
            log_fn("正在下载运行环境（约 45MB）…")
            first = False
        else:
            status_cb("正在下载运行环境…（切换通道）", 15)
        detail_log_fn(f"[{idx}/{len(PBS_URLS)}] 下载通道：{url}")
        try:
            download(url, archive, lambda d, t: status_cb(
                f"正在下载运行环境… {d / 1048576:.0f} / {t / 1048576:.0f} MB",
                5 + (d / max(t, 1)) * 20))
            if archive.stat().st_size > 0:
                break
        except Exception as e:
            detail_log_fn(f"  通道不可用：{e}")
            archive.unlink(missing_ok=True)
            last_err = e
    else:
        detail_log_fn(f"全部下载通道均失败，最后一个错误：{last_err}")
        raise RuntimeError("运行环境下载失败，请检查网络连接后重试。")

    # 解压到临时目录（解压即完成，无需安装）
    extract_tmp = env_dir / "_pbs_extract"
    if extract_tmp.exists():
        shutil.rmtree(extract_tmp, ignore_errors=True)
    extract_tmp.mkdir(parents=True, exist_ok=True)
    try:
        log_fn("正在部署运行环境…（解压文件，通常十几秒）")
        stop_evt = threading.Event()
        mon = None
        if monitor_fn is not None:
            mon = threading.Thread(target=monitor_fn, daemon=True,
                                   args=(extract_tmp, stop_evt, "正在部署运行环境", 25, 9, 90))
            mon.start()
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(extract_tmp, filter="data")
        stop_evt.set()
        if mon:
            mon.join(timeout=3)
    finally:
        archive.unlink(missing_ok=True)

    # 定位 python.exe（压缩包内顶层为 python/）
    hits = list(extract_tmp.rglob("python.exe"))
    if not hits:
        raise RuntimeError("运行环境包结构异常（未找到可执行文件）")
    src_python = hits[0].parent

    # 移动到正式位置
    if py_dir.exists():
        shutil.rmtree(py_dir, ignore_errors=True)
    shutil.move(str(src_python), str(py_dir))
    shutil.rmtree(extract_tmp, ignore_errors=True)

    # 验证组件可用（standalone 自带，正常都会通过）
    check = subprocess.run(
        [str(py_exe), "-c", "import tkinter, pip; print('OK')"],
        capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check.returncode != 0:
        # 极少见：尝试用 ensurepip 补齐 pip
        subprocess.run([str(py_exe), "-m", "ensurepip", "--upgrade"],
                       capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        check = subprocess.run(
            [str(py_exe), "-c", "import tkinter, pip; print('OK')"],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if check.returncode != 0:
            detail_log_fn("运行环境自检输出（最后 300 字符）：\n" + (check.stderr or "")[-300:])
            raise RuntimeError("运行环境自检未通过，请重新初始化。")
    return py_exe


def unpack_app(env_dir: Path) -> None:
    """把内置的主程序源码（app/）解压到环境目录。"""
    dst = env_dir / "app"
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)

    src = resource_dir() / "app"
    if src.exists():
        # 打包模式：资源里有完整 app 目录
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return

    # 源码模式：从本文件所在目录组装
    root = Path(__file__).resolve().parent
    shutil.copy2(root / "main.py", dst / "main.py")
    if (root / "core").exists():
        shutil.copytree(root / "core", dst / "core")
    if (root / "ui").exists():
        shutil.copytree(root / "ui", dst / "ui")
    if (root / "assets").exists():
        shutil.copytree(root / "assets", dst / "assets")


class InitWindow:
    """初始化窗口：显示进度并安装环境。"""

    def __init__(self, env_dir: Path):
        self.env_dir = env_dir
        self.win = tk.Tk()
        self.win.title(f"{APP_TITLE} —— 环境初始化")
        self.win.geometry("640x480")
        self.win.minsize(560, 420)

        tk.Label(self.win, text=APP_TITLE, font=("Microsoft YaHei", 16, "bold")).pack(pady=(18, 2))
        tk.Label(self.win, text="首次使用需要初始化运行环境（首次约需几分钟）",
                 fg="#666").pack()

        # 环境路径
        row = tk.Frame(self.win)
        row.pack(fill="x", padx=24, pady=(12, 4))
        tk.Label(row, text="环境目录：").pack(side="left")
        self.path_var = tk.StringVar(value=str(env_dir))
        tk.Entry(row, textvariable=self.path_var).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="浏览…", command=self._browse).pack(side="left", padx=(6, 0))
        tk.Label(self.win, text="Python 环境与模型将安装在此目录（默认 D 盘，可切换）",
                 fg="#888", font=("Microsoft YaHei", 9)).pack(anchor="w", padx=24)

        # 进度条
        self.bar = ttk.Progressbar(self.win, maximum=100)
        self.bar.pack(fill="x", padx=24, pady=(10, 4))
        self.status = tk.Label(self.win, text="点击「开始初始化」", fg="#333", anchor="w")
        self.status.pack(fill="x", padx=24)

        # 日志
        log_frame = tk.Frame(self.win)
        log_frame.pack(fill="both", expand=True, padx=24, pady=6)
        self.log = tk.Text(log_frame, height=8, wrap="word", state="disabled", bg="#f7f7f7")
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(log_frame, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)

        # 按钮
        btn_row = tk.Frame(self.win)
        btn_row.pack(fill="x", padx=24, pady=(4, 16))
        self.btn = tk.Button(btn_row, text="开始初始化", height=2, command=self._start)
        self.btn.pack(side="right")

        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self.running = False
        self._tech: list[str] = []  # 技术细节日志（只写本地文件，不外显）
        self._pip_installing = False  # 安装阶段是否进入落盘（用于进度显示衔接）

    def _browse(self):
        d = filedialog.askdirectory(title="选择环境目录")
        if d:
            self.path_var.set(d)

    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.win.update_idletasks()

    def _log_detailed(self, msg: str):
        """技术细节只累积到本地日志文件，绝不在界面上展示给普通用户。"""
        self._tech.append(msg)

    def _save_techlog(self) -> Path | None:
        """把技术细节日志写入环境目录 install_log.txt，返回路径（失败返回 None）。"""
        try:
            self.env_dir.mkdir(parents=True, exist_ok=True)
            p = self.env_dir / "install_log.txt"
            p.write_text("\n".join(self._tech), encoding="utf-8")
            return p
        except Exception:
            return None

    def _set_status(self, msg: str, pct: float | None = None):
        self.status.configure(text=msg)
        if pct is not None:
            self.bar["value"] = pct
        self.win.update_idletasks()

    def _monitor_dir(self, path: Path, stop_event: threading.Event,
                     label: str, base_pct: float, span_pct: float, total_mb: float):
        """实时监控目录写入进度（安装器/ pip 本身不输出，靠统计已写入文件来反馈）。

        每 0.8 秒统计一次 path 下的文件数与总大小，换算成估算进度并更新
        状态栏与进度条；stop_event.set() 后线程退出。
        """
        start = time.time()
        while not stop_event.is_set():
            files = 0
            total = 0
            try:
                for p in path.rglob("*"):
                    try:
                        if p.is_file():
                            files += 1
                            total += p.stat().st_size
                    except OSError:
                        pass
            except Exception:
                pass
            mb = total / 1048576
            pct = base_pct + min(total / max(total_mb * 1048576, 1), 1.0) * span_pct
            elapsed = int(time.time() - start)
            msg = (f"{label}… 已写入 {files} 个文件 / {mb:.0f} MB"
                   f"（估算 {pct:.0f}%，用时 {elapsed}s）")

            def _do(m=msg, p=pct):
                # 下载阶段（还没落盘）由实时下载状态接管，避免互相覆盖
                if files == 0 and not self._pip_installing:
                    return
                self.status.configure(text=m)
                self.bar.configure(mode="determinate", maximum=100)
                self.bar["value"] = p
            try:
                self.win.after(0, _do)
            except Exception:
                return
            stop_event.wait(0.8)

    def _run_stream(self, args, cwd, on_line=None) -> tuple[int, str]:
        """运行子命令，完整收集输出（技术细节仅写入日志文件，不外显）。

        返回 (退出码, 完整输出)。on_line(line) 用于把每行输出回调给
        界面做友好化的实时状态展示（不泄露包名等细节）。
        """
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        lines: list[str] = []
        try:
            proc = subprocess.Popen(
                args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", creationflags=creationflags)
        except Exception as e:
            return -1, str(e)
        for line in (proc.stdout or []):
            line = line.rstrip()
            if line:
                lines.append(line)
                self._log_detailed("  " + line[:200])
                if on_line:
                    on_line(line)
        rc = proc.wait()
        return rc, "\n".join(lines)

    def _pip_status(self, label: str):
        """解析安装输出，生成不暴露包名的友好实时状态（下载个数/累计大小）。"""
        import re
        state = {"n": 0, "bytes": 0}

        def _on_line(line: str) -> None:
            s = line.strip()
            if s.startswith("Collecting"):
                self._set_status(f"正在分析{label}的组成…")
            elif s.startswith("Downloading"):
                state["n"] += 1
                m = re.search(r"\(([\d.]+)\s*([kMG])B\)", s)
                if m:
                    mul = {"k": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[m.group(2)]
                    state["bytes"] += float(m.group(1)) * mul
                mb = state["bytes"] / 1048576
                self._set_status(
                    f"正在下载{label}（第 {state['n']} 个 · 累计约 {mb:.0f} MB）…")
            elif s.startswith("Installing collected packages"):
                self._pip_installing = True
                self._set_status(f"下载完成，正在把{label}安装到本机…")
            elif s.startswith("Successfully installed"):
                self._set_status(f"{label}安装完成，正在收尾…")

        return _on_line

    def _on_close(self):
        if self.running:
            if messagebox.askyesno("确认", "初始化正在进行，确定退出吗？"):
                self.win.destroy()
        else:
            self.win.destroy()

    def _start(self):
        if self.running:
            return
        self.env_dir = Path(self.path_var.get().strip() or DEFAULT_ENV_DIR)
        try:
            self.env_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("错误", f"无法创建目录：{e}")
            return
        self.running = True
        self.btn.configure(state="disabled")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            self._do_init()
            # 保存配置
            save_launcher_cfg({"env_dir": str(self.env_dir)})
            self._save_techlog()  # 成功也完整记录，便于日后排查
            self.win.after(0, self._launch_main)
        except Exception as e:
            log_path = self._save_techlog()
            self.win.after(0, lambda: self._fail(str(e), log_path))

    def _do_init(self):
        env = self.env_dir
        py_dir = env / "python"
        py_exe = py_dir / "python.exe"

        # pip 下载缓存统一放到环境目录（D 盘），避免用户 C 盘被缓存撑大
        os.environ["PIP_CACHE_DIR"] = str(env / "pip-cache")

        # 1. 准备 Python 运行环境（便携版优先，自动回退）
        if not py_exe.exists():
            self._set_status("正在准备运行环境…", 5)
            try:
                py_exe = install_python_standalone(
                    env, log_fn=self._log, status_cb=self._set_status,
                    monitor_fn=self._monitor_dir, detail_log_fn=self._log_detailed)
                self._log("✅ 运行环境部署完成。")
            except Exception as e:
                self._log_detailed(f"便携运行环境不可用：{e}")
                # 回退：官方安装器（慢但可靠）
                self._log("正在通过备用通道安装运行环境…（可能较慢，请耐心等待）")
                installer = env / "python-installer.exe"
                download(PY_URL, installer, lambda d, t: self._set_status(
                    f"正在下载运行环境… {d / 1048576:.1f} MB", 5 + (d / max(t, 1)) * 15))
                self._log("正在安装运行环境…（通常需要 1–2 分钟）")
                r = subprocess.run(
                    [str(installer), "/quiet", "InstallAllUsers=0",
                     f"TargetDir={py_dir}", "PrependPath=0", "Include_test=0",
                     "Include_launcher=0", "Include_doc=0", "Include_pip=1",
                     "Include_tcltk=1", "Shortcuts=0", "AssociateFiles=0"],
                    capture_output=True, timeout=900,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                installer.unlink(missing_ok=True)
                if r.returncode != 0:
                    self._log_detailed(f"安装器返回码：{r.returncode}")
                if not py_exe.exists():
                    raise RuntimeError("运行环境安装失败，请检查磁盘空间与网络后重试。")
                self._log("✅ 运行环境安装完成。")
        else:
            self._log("运行环境已存在，跳过准备。")

        # 3. 安装 AI 推理组件（pip，进度由目录监控实时显示）
        # 先自检关键组件：若损坏（如中断残留的残缺包）则清理重装，避免“已满足跳过”
        check = subprocess.run(
            [str(py_exe), "-c", "import onnxruntime as o; assert o.__file__ and hasattr(o, 'SessionOptions')"],
            capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if check.returncode != 0:
            self._log("正在修复关键组件…")
            self._run_stream([str(py_exe), "-m", "pip", "uninstall", "-y", "onnxruntime", "fastembed"], env)
            sp_dir = env / "python" / "Lib" / "site-packages"
            if sp_dir.exists():
                for pat in ("onnxruntime*", "fastembed*"):
                    for item in sp_dir.glob(pat):
                        shutil.rmtree(item, ignore_errors=True)
            self._log_detailed("已清理残缺组件，将重新安装。")
        self._log("正在安装 AI 推理组件（首次需联网下载，通常几分钟）…")
        stop_evt = threading.Event()
        mon = threading.Thread(
            target=self._monitor_dir,
            args=(env / "Lib" / "site-packages", stop_evt, "正在安装 AI 推理组件", 35, 24, 260),
            daemon=True)
        mon.start()
        pip = [str(py_exe), "-m", "pip", "install", "-i", PIP_MIRROR,
               "--no-warn-script-location", "--progress-bar", "off"] + REQUIREMENTS_CPU
        self._pip_installing = False
        rc, pip_out = self._run_stream(pip, env, on_line=self._pip_status("AI 推理组件"))
        stop_evt.set()
        mon.join(timeout=3)
        if rc != 0:
            self._log_detailed("组件安装输出（最后 800 字符）：\n" + pip_out[-800:])
            raise RuntimeError("AI 推理组件安装失败，请检查网络连接后重试。")
        self._log("✅ AI 推理组件安装完成。")

        # 4. 解压主程序
        self._set_status("正在部署软件本体…", 60)
        unpack_app(env)
        self._log("✅ 软件部署完成。")

        # 5. 配置硬件加速（自动，无需用户干预）
        self._set_status("正在配置硬件加速…", 80)
        if detect_nvidia_gpu():
            self._log("检测到独立显卡，正在配置加速…")
            ok = self._try_gpu(py_exe, env)
            if ok:
                self._log("✅ 显卡加速已启用。")
            else:
                self._log("✅ 已为你选择 CPU 模式（同样可以正常使用）。")
        else:
            self._log("未检测到独立显卡，使用 CPU 模式（同样可以正常使用）。")

        self._log("🎉 初始化全部完成，正在启动软件…")

    def _try_gpu(self, py_exe: Path, env: Path) -> bool:
        """有系统 CUDA 库时安装 GPU 版 onnxruntime，并验证 CUDA provider 可实际加载。

        从国内镜像下载并设置超时，避免卡住；失败自动恢复 CPU 版，不影响使用。
        """
        pip = [str(py_exe), "-m", "pip"]
        if not _system_cuda_available():
            # 未装系统 CUDA：确保 CPU 版可用（幂等，防止上次卸载中断留下坏环境）
            subprocess.run(pip + ["install", "-i", PIP_MIRROR, "--timeout", "15",
                                  "--retries", "1", "onnxruntime"],
                           cwd=str(env), capture_output=True, timeout=600, check=False)
            return False
        try:
            # 卸载 CPU 版 onnxruntime
            subprocess.run(pip + ["uninstall", "-y", "onnxruntime"],
                           cwd=str(env), capture_output=True, timeout=120, check=False)
            self._log("正在下载显卡加速组件（约 150MB，视网速可能需要几分钟）…")
            stop_evt = threading.Event()
            mon = threading.Thread(
                target=self._monitor_dir,
                args=(env / "Lib" / "site-packages", stop_evt, "正在安装显卡加速组件", 80, 15, 300),
                daemon=True)
            mon.start()
            self._pip_installing = False
            rc, gpu_out = self._run_stream(
                pip + ["install", "--no-cache-dir", "-i", PIP_MIRROR,
                       "--timeout", "15", "--retries", "1", "--progress-bar", "off",
                       f"onnxruntime-gpu=={ORT_GPU_VERSION}"], env,
                on_line=self._pip_status("显卡加速组件"))
            stop_evt.set()
            mon.join(timeout=3)
            if rc != 0:
                self._log_detailed("显卡组件安装输出（最后 600 字符）：\n" + gpu_out[-600:])
                # 恢复 CPU 版，保证软件可用
                subprocess.run(pip + ["install", "-i", PIP_MIRROR, "--timeout", "15",
                                      "--retries", "1", "onnxruntime"],
                               cwd=str(env), capture_output=True, timeout=600, check=False)
                return False
            # 验证 CUDA provider 可用（PATH 已注入系统 CUDA/cuDNN 目录）
            check_code = (
                "import onnxruntime as ort; "
                "print('CUDA_OK' if 'CUDAExecutionProvider' in ort.get_available_providers() else 'NO_CUDA')"
            )
            check = subprocess.run(
                [str(py_exe), "-c", check_code],
                cwd=str(env), capture_output=True, text=True, timeout=120,
                env=_launch_env())
            out = (check.stdout or "").strip()
            self._log_detailed("显卡加速验证：" + (out or "无输出"))
            return check.returncode == 0 and "CUDA_OK" in out
        except Exception:
            return False

    def _fail(self, msg: str, log_path: Path | None = None):
        self.running = False
        self.btn.configure(state="normal")
        self._log("❌ 初始化失败：" + msg)
        extra = ""
        if log_path:
            extra = f"\n\n如需帮助，可将以下文件发给开发者：\n{log_path}"
        messagebox.showerror("初始化失败", msg + extra + "\n\n可点击「开始初始化」重试。")

    def _launch_main(self):
        self._set_status("正在为你打开软件…", 100)
        self._log("初始化完成，正在启动…")
        try:
            launch_app(self.env_dir)
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
        self.win.destroy()


def main() -> None:
    # 确保工作目录指向 exe 所在目录，否则从注册表 Run 键
    # 开机自启时工作目录为 C:\Windows\System32，会导致找不到依赖。
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))

    cfg = load_launcher_cfg()
    env_dir = Path(cfg.get("env_dir", DEFAULT_ENV_DIR))
    if env_ready(env_dir):
        # 环境就绪：先同步一次主程序文件（保证包结构/app 内容与 exe 内置一致，
        # 可自愈上次异常留下的损坏目录），再启动主程序
        try:
            unpack_app(env_dir)
        except Exception:
            pass
        try:
            launch_app(env_dir)
            return
        except Exception:
            pass
    InitWindow(env_dir).win.mainloop()


if __name__ == "__main__":
    main()
