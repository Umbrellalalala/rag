"""文件扫描、分块、embedding 与索引构建模块。

当前支持 .txt / .md 等文本文件，后续可扩展 pdf、png（OCR）等。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .config import INDEX_DIR
from .store import VectorStore

# 当前支持的文本扩展名（后续可扩展 pdf、png（OCR）等）
# 不含 .log / .csv：调试日志和表格转储会切出成千上万个片段，
# 检索时把真正的笔记挤出 top_k，实测是净负收益。
SUPPORTED_EXTS = {".txt", ".md", ".markdown", ".rst", ".json", ".py", ".yaml", ".yml"}

# 跳过目录
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".idea", ".vscode", "data", ".cache"}

# 检索范围分类：个人空间里代码往往占九成以上片段，问笔记时会被代码行挤掉，
# 所以检索时按「文档 / 全部」分档过滤（索引照旧全建，只是查的时候挑一挑）。
DOC_EXTS = {".md", ".markdown", ".txt", ".rst"}


def chunk_is_doc(meta: dict) -> bool:
    suffix = (meta.get("suffix") or Path(meta.get("file", "")).suffix).lower()
    return suffix in DOC_EXTS


def list_text_files(root: str | Path) -> list[Path]:
    """递归列出个人空间下所有支持的文本文件。"""
    root = Path(root)
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in SUPPORTED_EXTS:
            files.append(p)
    return files


def read_text(path: Path, max_bytes: int = 5 * 1024 * 1024) -> str:
    """读取文本文件，自动尝试多种编码；过大文件截断。"""
    size = path.stat().st_size
    if size > max_bytes:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
        for enc in ("utf-8", "gbk", "utf-8-sig", "latin-1"):
            try:
                return data.decode(enc, errors="ignore")
            except Exception:
                continue
        return data.decode("utf-8", errors="ignore")

    for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def split_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """将长文本切分为重叠的分块，优先按段落/句子边界切分。"""
    text = text.strip()
    if not text:
        return []
    # 先按段落切
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # 超长段落继续按句子/固定长度切
        while len(para) > chunk_size:
            cut = para[:chunk_size]
            # 尝试在句子边界切断
            for sep in ("。", "！", "？", ". ", "! ", "? ", "\n"):
                idx = cut.rfind(sep)
                if idx > chunk_size * 0.6:
                    cut = para[: idx + 1]
                    break
            chunks.append(cut.strip())
            para = para[max(1, len(cut) - overlap):].strip()
        if buf:
            merged = buf + "\n" + para
            if len(merged) <= chunk_size:
                buf = merged
                continue
            else:
                chunks.append(buf)
                buf = para
        else:
            buf = para
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c]


def _setup_gpu_dll_paths() -> None:
    """将 CUDA 相关动态库目录加入 DLL 搜索路径。

    外部环境运行时，用户机器可能没有系统级 CUDA Toolkit，
    CUDA 库来自 pip 安装的 nvidia-*-cu12 包（site-packages/nvidia/*/bin）；
    若系统装有 CUDA Toolkit / cuDNN 则优先使用系统库。
    """
    try:
        import sysconfig

        site = Path(sysconfig.get_paths()["purelib"])
        add_dir = getattr(os, "add_dll_directory", None)

        # 1) pip 安装的 nvidia 包（cublas/cudnn/cudart，含 DLL）
        nvidia_root = site / "nvidia"
        if nvidia_root.exists():
            for d in sorted(nvidia_root.glob("*/bin")):
                if add_dir:
                    add_dir(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")

        # 2) 系统 CUDA Toolkit（含 cublas/cudart）
        for cuda_bin in sorted(Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA").glob("v*/bin"), reverse=True):
            if (cuda_bin / "cublas64_12.dll").exists():
                if add_dir:
                    add_dir(str(cuda_bin))
                os.environ["PATH"] = str(cuda_bin) + os.pathsep + os.environ.get("PATH", "")
                break

        # 3) 系统 cuDNN（DLL 可能在 bin 子目录）
        for vdir in sorted(Path(r"C:\Program Files\NVIDIA\CUDNN").glob("v*"), reverse=True):
            for sub in sorted((vdir / "bin").glob("*"), reverse=True):
                if (sub / "cudnn64_9.dll").exists():
                    if add_dir:
                        add_dir(str(sub))
                    os.environ["PATH"] = str(sub) + os.pathsep + os.environ.get("PATH", "")
                    break
    except Exception:
        pass


def resolve_device(prefer: str | None = None) -> str:
    """自动选择最优计算设备：cuda > cpu。

    prefer 可显式指定 'cuda' / 'cpu'，留空或 'auto' 则自动检测。
    基于 onnxruntime 的 provider 检测（fastembed 推理引擎）。
    """
    if prefer and prefer.lower() not in ("auto", ""):
        return prefer.lower()
    # 必须先把 CUDA / cuDNN 的 DLL 目录注册进搜索路径，再 import onnxruntime：
    # 顺序反了 provider 桥就加载不到 onnxruntime_providers_cuda.dll
    # （LoadLibrary error 126），而 get_available_providers() 只要编译进去了
    # 就会列出 CUDA，看不出实际失败。
    try:
        _setup_gpu_dll_paths()
        import onnxruntime as ort

        if "CUDAExecutionProvider" in ort.get_available_providers():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def describe_device(device: str) -> str:
    """返回人类可读的设备描述（含 GPU 型号/显存）。"""
    if device == "cuda":
        try:
            from .hardware import get_gpu_info

            gpus = get_gpu_info()
            if gpus:
                g = gpus[0]
                mem = g.get("memory_mb")
                return f"CUDA · {g.get('name')} · {mem} MB 显存" if mem else f"CUDA · {g.get('name')}"
            return "CUDA (NVIDIA GPU)"
        except Exception:
            return "CUDA (NVIDIA GPU)"
    return "CPU"


class Embedder:
    """封装 fastembed（onnxruntime）的 embedding 模型加载与推理。

    默认自动选择最优设备（GPU 优先），也可通过 device 参数显式指定。
    模型为 ONNX 格式，下载缓存在设置的 models_dir 目录。
    """

    def __init__(self, model_name: str, device: str | None = None):
        _setup_gpu_dll_paths()

        from fastembed import TextEmbedding

        from .config import load_config

        self.model_name = model_name
        self.requested = resolve_device(device)
        self.device = self.requested
        cfg = load_config()
        cache_dir = cfg.get("models_dir") or None
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if self.requested == "cuda"
            else ["CPUExecutionProvider"]
        )
        kwargs: dict = {"providers": providers}
        if cache_dir:
            kwargs["cache_dir"] = cache_dir
        self._model = TextEmbedding(model_name, **kwargs)
        # 以会话真正挂上的 provider 为准：get_available_providers() 只反映编译期
        # 支持的列表，CUDA 建会话失败回退 CPU 时它照样返回 CUDA。
        self.device = self._actual_device()

    def _actual_device(self) -> str:
        for attr in ("model", "_model"):
            sess = getattr(self._model, attr, None)
            get_providers = getattr(sess, "get_providers", None)
            if get_providers is None:
                continue
            try:
                return "cuda" if any(
                    p.startswith("CUDA") for p in get_providers()) else "cpu"
            except Exception:
                break
        return "cpu"

    def encode(self, texts: list[str], batch_size: int = 32, show_progress: bool = False) -> np.ndarray:
        return np.asarray(
            list(self._model.embed(texts, batch_size=batch_size)),
            dtype="float32",
        )


class Indexer:
    """负责扫描、分块、embedding，并维护 VectorStore。"""

    def __init__(self, embedder: Embedder, chunk_size: int = 500, overlap: int = 50):
        self.embedder = embedder
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.store = VectorStore()
        self.stats: dict[str, Any] = {"files": 0, "chunks": 0}

    def _index_file(self, path: Path, progress_cb, done: int, total: int) -> int:
        """索引单个文件，返回新增片段数。"""
        if progress_cb:
            progress_cb(f"正在索引：{path.name}", done, total)
        content = read_text(path)
        chunks = split_text(content, self.chunk_size, self.overlap)
        if not chunks:
            return 0
        mtime = path.stat().st_mtime
        vectors = self.embedder.encode(chunks)
        metas = [
            {
                "file": str(path),
                "name": path.name,
                "suffix": path.suffix.lower(),
                "mtime": mtime,
                "text": c,
            }
            for c in chunks
        ]
        self.store.add(np.vstack(vectors), metas)
        return len(chunks)

    def build(
        self,
        root: str | Path,
        progress_cb: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        """构建个人空间的索引。progress_cb(message, done, total)。"""
        files = list_text_files(root)
        total_files = len(files)
        chunk_count = 0

        for idx, path in enumerate(files):
            chunk_count += self._index_file(path, progress_cb, idx + 1, total_files)

        if chunk_count:
            self.store.save(INDEX_DIR)

        indexed_files = len({m["file"] for m in self.store.metas})
        self.stats = {"files": total_files, "chunks": chunk_count, "indexed_files": indexed_files}
        return self.stats

    def incremental_update(
        self,
        root: str | Path,
        progress_cb: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        """增量更新：只处理新增/修改的文件，并移除已删除文件对应的条目。"""
        root = Path(root)
        current_files = list_text_files(root)
        current_map = {str(p): p for p in current_files}

        # 已索引的文件 -> mtime
        indexed: dict[str, float] = {}
        for m in self.store.metas:
            if m.get("file") not in indexed:
                indexed[m["file"]] = m.get("mtime", 0)

        # 删除：已索引但文件已不存在
        removed_files = set(indexed) - set(current_map)
        removed = self.store.remove_files(removed_files)

        # 新增 / 修改：文件存在但未索引或 mtime 变化
        to_index = []
        for fpath, p in current_map.items():
            mtime = p.stat().st_mtime
            if fpath not in indexed or abs(indexed[fpath] - mtime) > 1e-6:
                to_index.append(p)

        added = 0
        total = len(to_index)
        for i, p in enumerate(to_index):
            if progress_cb:
                progress_cb(f"增量索引：{p.name}", i + 1, total)
            # 先移除旧条目（避免重复）
            self.store.remove_files({str(p)})
            added += self._index_file(p, progress_cb, i + 1, total)

        if removed or added:
            self.store.save(INDEX_DIR)

        self.stats = {
            "files": len(current_files),
            "added": added,
            "removed_files": len(removed_files),
            "removed_chunks": removed,
            "chunks": self.store.size,
        }
        return self.stats

    def load(self) -> bool:
        """从磁盘加载已构建的索引。"""
        return self.store.load(INDEX_DIR)

    def search(self, query: str, top_k: int = 4, scope: str = "all") -> list[dict[str, Any]]:
        """检索。scope='doc' 时只在笔记/文档类片段里挑。

        个人空间里代码常占九成以上片段，不过滤的话问笔记时 top_k 会被代码行挤满。
        """
        qv = self.embedder.encode([query])[0]
        if scope != "doc":
            return self.store.search(qv, top_k)
        # 先多取候选再过滤，否则取回来的可能全是代码
        cand = self.store.search(qv, min(max(top_k * 12, 60), max(self.store.size, 1)))
        docs = [c for c in cand if chunk_is_doc(c)]
        return docs[:top_k] if docs else cand[:top_k]
