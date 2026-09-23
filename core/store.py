"""向量存储：优先使用 FAISS（可选依赖），未安装时回退到 numpy 余弦检索。"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np

try:
    import faiss  # type: ignore

    _HAS_FAISS = True
except Exception:
    _HAS_FAISS = False


class VectorStore:
    """基于内积（归一化向量即等价余弦相似度）的向量库。"""

    def __init__(self) -> None:
        self._index = None            # faiss.IndexFlatIP 或 None
        self._vectors: list[np.ndarray] = []   # numpy 回退用
        self._metas: list[dict[str, Any]] = []  # 元数据列表
        self._dim: int | None = None

    @property
    def size(self) -> int:
        return len(self._metas)

    @property
    def metas(self) -> list[dict[str, Any]]:
        return self._metas

    def _ensure_index(self) -> None:
        """确保 faiss 索引存在。

        从 vectors.npy 恢复的旧索引只填了 _vectors、_dim 却留空 _index，
        此时再 add() 就会踩到 None.add()；加载过索引之后的任何一次增量更新
        都会崩，这里统一兜底并按已存向量重建。
        """
        if not _HAS_FAISS or self._dim is None:
            return
        if self._index is not None and self._index.ntotal == len(self._metas):
            return
        index = faiss.IndexFlatIP(self._dim)
        have = self._all_vectors()
        if len(have):
            index.add(have)
        self._index = index

    def _all_vectors(self) -> np.ndarray:
        """取出全部向量，形状 (n, dim)。

        有 faiss 时以 faiss 为准（IndexFlat 内部就是裸数组，取视图不复制），
        否则用 Python 列表。以前 add() 无论有没有 faiss 都往 _vectors 里再存一份，
        而 load() 从 index.faiss 恢复时又不填 _vectors —— 于是「加载旧索引 + 新增若干条」
        之后，_vectors 只剩新增的那部分，和 _metas 完全错位，
        下一次 remove_files 就会把向量配错文件。
        """
        if self._index is not None and self._index.ntotal:
            try:
                n, d = self._index.ntotal, self._index.d
                return np.reshape(faiss.rev_swig_ptr(self._index.get_xb(), n * d), (n, d))
            except Exception:
                pass        # 非 IndexFlat 拿不到裸数组，退回 _vectors
        if not self._vectors:
            return np.zeros((0, self._dim or 0), dtype="float32")
        return np.asarray(self._vectors, dtype="float32")

    def add(self, vectors: np.ndarray, metas: list[dict[str, Any]]) -> None:
        """批量添加向量与对应元数据。"""
        if len(vectors) == 0:
            return
        vectors = np.asarray(vectors, dtype="float32")
        # L2 归一化，使内积等价余弦相似度
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vectors = vectors / norms

        if self._dim is None:
            self._dim = vectors.shape[1]
        self._ensure_index()

        if _HAS_FAISS:
            self._index.add(vectors)
        else:
            # 没有 faiss 时才需要自己留一份向量（用于删除重建与暴力检索）
            self._vectors.extend(vectors)
        self._metas.extend(metas)

    def remove_files(self, files: set[str]) -> int:
        """按文件路径删除对应条目（过滤后重建索引），返回删除数量。"""
        if not files:
            return 0
        all_vecs = self._all_vectors()
        if len(all_vecs) != len(self._metas):
            # 向量和元数据对不上，重建会把内容配到错误的文件上，宁可不动。
            return 0
        keep_idx = [i for i, m in enumerate(self._metas) if m.get("file") not in files]
        removed = len(self._metas) - len(keep_idx)
        if removed:
            keep_metas = [self._metas[i] for i in keep_idx]
            keep_vecs = all_vecs[keep_idx] if keep_idx else np.zeros((0, self._dim or 0), "float32")
            self._index = None
            self._vectors = []
            self._metas = []
            self._dim = int(keep_vecs.shape[1]) if keep_vecs.size else None
            if len(keep_metas):
                self.add(keep_vecs, keep_metas)
        return removed

    def search(self, query_vector: np.ndarray, top_k: int = 4) -> list[dict[str, Any]]:
        """检索最相似的 top_k 条，返回带分数的元数据列表。"""
        if self.size == 0:
            return []
        q = np.asarray(query_vector, dtype="float32").reshape(1, -1)
        norm = np.linalg.norm(q)
        q = q / (norm if norm else 1.0)

        if _HAS_FAISS and self._index is not None:
            scores, indices = self._index.search(q, min(top_k, self.size))
            results = []
            for s, i in zip(scores[0], indices[0]):
                if i < 0:
                    continue
                item = dict(self._metas[int(i)])
                item["score"] = float(s)
                results.append(item)
            return results

        # numpy 回退
        vecs = self._all_vectors()
        if vecs.size == 0:
            return []
        sims = vecs @ q[0]
        order = np.argsort(-sims)[:top_k]
        results = []
        for i in order:
            item = dict(self._metas[int(i)])
            item["score"] = float(sims[i])
            results.append(item)
        return results

    def save(self, directory: str | Path) -> None:
        """保存索引与元数据（原子写）。

        先落临时文件、再 os.replace，保证「向量」与「元数据」要么同时是新版、
        要么同时是旧版；写入中途崩溃/断电不会留下两个文件不一致的半成品
        （旧实现先写 faiss 后写 metadata，中间被打断会导致检索 IndexError）。
        """
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)

        # 1) 先备份旧索引，便于损坏时回滚
        for name in ("index.faiss", "vectors.npy", "metadata.json"):
            old = directory / name
            if old.exists():
                try:
                    shutil.copy2(old, Path(str(old) + ".bak"))
                except OSError:
                    pass

        # 2) 写临时文件
        meta_tmp = directory / "metadata.json.tmp"
        with open(meta_tmp, "w", encoding="utf-8") as f:
            json.dump(self._metas, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        vec_name = ""
        vec_tmp: Path | None = None
        if _HAS_FAISS and self._index is not None:
            vec_name = "index.faiss"
            vec_tmp = directory / "index.faiss.tmp"
            faiss.write_index(self._index, str(vec_tmp))
        elif self._vectors:
            vec_name = "vectors.npy"
            vec_tmp = directory / "vectors.npy.tmp"
            # 注意：np.save(路径) 会在名字后自动补 .npy，必须传文件对象
            with open(vec_tmp, "wb") as f:
                np.save(f, np.vstack(self._vectors))
                f.flush()
                os.fsync(f.fileno())

        # 3) 原子替换。顺序：先元数据后向量——若两者之间被打断，
        #    元数据条目数 >= 向量数，search 只会少返回而不会越界。
        os.replace(meta_tmp, directory / "metadata.json")
        if vec_tmp is not None and vec_name:
            os.replace(vec_tmp, directory / vec_name)

    def load(self, directory: str | Path) -> bool:
        """从磁盘加载索引与元数据，返回是否成功。

        若向量数与元数据条目数不一致（上次写入被打断），自动回退到 .bak 备份；
        仍不一致则截断到较小值，保证检索不会 IndexError。
        """
        directory = Path(directory)
        meta_path = directory / "metadata.json"
        if not meta_path.exists():
            return False
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                metas = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            metas = None
        if not isinstance(metas, list):
            return False

        faiss_path = directory / "index.faiss"
        np_path = directory / "vectors.npy"
        if _HAS_FAISS and faiss_path.exists():
            try:
                index = faiss.read_index(str(faiss_path))
            except Exception:
                index = None
            if index is not None and index.ntotal != len(metas):
                # 不一致 → 用备份重试一次
                index, metas = self._repair(directory, index, metas, "index.faiss")
            self._index = index
            self._dim = index.d if index is not None else None
        elif np_path.exists():
            try:
                arr = np.load(np_path)
            except Exception:
                arr = None
            if arr is not None and arr.shape[0] != len(metas):
                arr2, metas = self._repair_np(directory, arr, metas)
                arr = arr2
            if arr is not None:
                self._vectors = [arr[i] for i in range(arr.shape[0])]
                self._dim = arr.shape[1]

        self._metas = metas
        self._ensure_index()
        return self.size > 0

    # -- 一致性修复 -------------------------------------------------
    @staticmethod
    def _repair(directory: Path, index, metas: list, name: str):
        bak = Path(str(directory / name) + ".bak")
        meta_bak = Path(str(directory / "metadata.json") + ".bak")
        if bak.exists() and meta_bak.exists():
            try:
                alt = faiss.read_index(str(bak))
                with open(meta_bak, "r", encoding="utf-8") as f:
                    alt_meta = json.load(f)
                if isinstance(alt_meta, list) and alt.ntotal == len(alt_meta):
                    return alt, alt_meta
            except Exception:
                pass
        # 无法自动修复：截断到较小值，保证不越界
        n = min(index.ntotal, len(metas))
        return index, metas[:n]

    @staticmethod
    def _repair_np(directory: Path, arr, metas: list):
        bak = Path(str(directory / "vectors.npy") + ".bak")
        meta_bak = Path(str(directory / "metadata.json") + ".bak")
        if bak.exists() and meta_bak.exists():
            try:
                alt = np.load(bak)
                with open(meta_bak, "r", encoding="utf-8") as f:
                    alt_meta = json.load(f)
                if isinstance(alt_meta, list) and alt.shape[0] == len(alt_meta):
                    return alt, alt_meta
            except Exception:
                pass
        n = min(arr.shape[0], len(metas))
        return arr[:n], metas[:n]
