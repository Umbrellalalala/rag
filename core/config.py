"""配置读写模块。

重要：持久化数据与「代码 / exe 目录」完全解耦。

- ``APP_DIR``   —— 代码或 exe 所在目录，只用于定位只读资源（assets、.env）。
- ``USER_DIR``  —— 真正的持久化数据根目录，默认 ``~/.rag_assistant``
  （可用环境变量 ``RAG_DATA_HOME`` 覆盖），存放 config.json / data（对话、
  笔记、向量索引）。这样重新打包 exe、删除 dist、清理源码目录都不会丢数据。

首次在新位置启动时会自动从旧位置（exe 目录 / D:\\RAGAssistant / 源码目录 /
便携环境根目录）复制已有数据，实现无缝迁移（复制而非移动，旧数据保留）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


def _app_dir() -> Path:
    """代码 / 资源目录（只读资源：assets、.env）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _pointer_path() -> Path:
    """记录「数据目录选在哪」的指针文件。

    必须放在数据目录之外（固定在家目录），否则换了目录就再也找不回来了。
    """
    return Path.home() / ".rag_assistant_location"


def _read_pointer() -> str:
    try:
        p = _pointer_path()
        if p.exists():
            val = p.read_text(encoding="utf-8").strip()
            if val and Path(val).is_dir():
                return val
    except OSError:
        pass
    return ""


def _write_pointer(path: str | Path) -> None:
    try:
        _pointer_path().write_text(str(path), encoding="utf-8")
    except OSError:
        pass


def _pick_default_dir() -> Path:
    """默认数据目录：优先剩余空间最大的非系统盘，别把索引堆在 C 盘。

    向量索引动辄几 GB，落在系统盘上既挤占空间又容易在磁盘满时出问题；
    没有合适的其它盘时才回到家目录。
    """
    home = Path.home() / ".rag_assistant"
    system_drive = (home.drive[:2] or "C:").upper()
    best: tuple[int, str] | None = None
    if os.name == "nt":
        import string

        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if letter + ":" == system_drive or not os.path.exists(root):
                continue
            try:
                free = shutil.disk_usage(root).free
            except OSError:
                continue        # U 盘 / 光驱 / 网络盘会抛异常，跳过
            if free < 4 * 1024 ** 3:
                continue        # 少于 4GB 放不下索引，别选它
            if best is None or free > best[0]:
                best = (free, root)
    if best is None:
        return home
    return Path(best[1]) / "RAGAssistant数据"


def _user_dir() -> Path:
    """持久化数据根目录（与 exe / 源码目录解耦）。

    优先级：环境变量 RAG_DATA_HOME > 指针文件（设置页里改的） >
    家目录下的旧默认位置（已有数据时不动它） > 自动挑一个非系统盘。
    """
    env = os.environ.get("RAG_DATA_HOME")
    if env:
        return Path(env)
    pointed = _read_pointer()
    if pointed:
        return Path(pointed)
    legacy = Path.home() / ".rag_assistant"
    if (legacy / "config.json").exists() or (legacy / "data").exists():
        return legacy
    return _pick_default_dir()


APP_DIR = _app_dir()
USER_DIR = _user_dir()
#: 首次运行自动选定目录时写下的来源说明，供设置页展示
AUTO_PICKED = not os.environ.get("RAG_DATA_HOME") and not _read_pointer()

#: 兼容旧代码：数据根目录（等价于 USER_DIR）
PROJECT_DIR = USER_DIR

CONFIG_PATH = USER_DIR / "config.json"
DATA_DIR = USER_DIR / "data"
INDEX_DIR = DATA_DIR / "index"
CONV_DIR = DATA_DIR / "conversations"
NOTE_DIR = DATA_DIR / "notes"

_LEGACY_MODELS_DIR = r"D:\RAGAssistant\models"


def _legacy_candidates() -> list[Path]:
    """可能的旧数据位置（按优先级排列，去重保序）。"""
    cands: list[Path] = []
    # 便携环境：bootstrap 把主程序解压到 <env>/app，数据在 <env> 根
    env_root = APP_DIR.parent
    if (env_root / "app").exists() and (env_root / "python").exists():
        cands.append(env_root)
    # 单层 exe：数据曾写在 exe 同目录
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent)
    # 旧「轻量启动器」方案
    cands.append(Path(r"D:\RAGAssistant"))
    # 默认目录曾固定在家目录（C 盘）
    cands.append(Path.home() / ".rag_assistant")
    # 纯源码运行：数据曾写在 rag/ 目录
    cands.append(APP_DIR)

    out: list[Path] = []
    seen: set[str] = set()
    for c in cands:
        try:
            key = str(c.resolve()).lower()
        except OSError:
            key = str(c).lower()
        if key in seen or key == str(USER_DIR.resolve()).lower():
            continue
        seen.add(key)
        out.append(c)
    return out


def _has_data(src: Path) -> bool:
    return (src / "config.json").exists() or (src / "data").exists()


def _latest_mtime(src: Path) -> float:
    """候选目录下数据的最新修改时间。

    同一份数据可能散落在多个旧位置（便携环境旧方案 / exe 目录 / 源码目录），
    迁移时应挑「最近还在用」的那一份，而不是写死的优先级。
    """
    best = 0.0
    try:
        for rel in ("config.json", "data"):
            p = src / rel
            if p.exists():
                best = max(best, p.stat().st_mtime)
        d = src / "data"
        if d.is_dir():
            for f in d.rglob("*"):
                try:
                    best = max(best, f.stat().st_mtime)
                except OSError:
                    pass
    except OSError:
        pass
    return best


def migrate_legacy_data() -> str | None:
    """把旧位置的数据迁移到 USER_DIR。仅在 USER_DIR 完全为空时执行。

    使用复制而非移动：迁移失败或用户不满意时旧数据仍然完好。
    返回来源路径（未迁移则返回 None）。
    """
    try:
        if (USER_DIR / "config.json").exists() or (USER_DIR / "data").exists():
            return None
        cands = [c for c in _legacy_candidates() if _has_data(c)]
        # 按「最近修改时间」降序，优先迁移仍在使用的那份数据
        cands.sort(key=_latest_mtime, reverse=True)
        for src in cands:
            try:
                USER_DIR.mkdir(parents=True, exist_ok=True)
                if (src / "config.json").exists():
                    shutil.copy2(src / "config.json", USER_DIR / "config.json")
                if (src / "data").exists():
                    shutil.copytree(src / "data", USER_DIR / "data", dirs_exist_ok=True)
                note = (
                    f"本目录（{USER_DIR}）是 RAG 助手的持久化数据目录，与程序安装位置无关。\n"
                    f"以下旧位置的数据已于首次启动时复制到此处（原位置数据保留未删除）：\n{src}\n"
                    "确认新目录数据无误后，可自行删除旧位置的数据以释放空间。\n"
                )
                (USER_DIR / "数据来源说明.txt").write_text(note, encoding="utf-8")
                return str(src)
            except Exception:
                continue
    except Exception:
        pass
    return None


def ensure_data_dirs() -> None:
    for p in (DATA_DIR, INDEX_DIR, CONV_DIR, NOTE_DIR):
        p.mkdir(parents=True, exist_ok=True)


def set_data_home(path: str | Path, persist_env: bool = False) -> None:
    """把「数据目录在哪」记进指针文件，下次启动即生效。

    persist_env 只在用户在设置页主动改位置时才传 True：环境变量
    RAG_DATA_HOME 的优先级高于指针文件，不同步的话会出现「设置页改了、
    环境变量还指着老目录」。首次运行自动选盘则只写指针，不去动注册表。
    """
    target = Path(path)
    if persist_env:
        os.environ["RAG_DATA_HOME"] = str(target)
    _write_pointer(target)
    if persist_env and os.name == "nt":
        try:
            subprocess.run(["setx", "RAG_DATA_HOME", str(target)],
                           capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass


def dir_size_gb(path: str | Path) -> float:
    """目录占用（GB）。走一遍文件树，几百个文件时开销可忽略。"""
    total = 0
    try:
        for f in Path(path).rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total / 1024 ** 3


def free_gb(drive: str | Path) -> float:
    """某个盘/路径所在分区的剩余空间（GB），取不到返回 -1。"""
    root = Path(drive)
    while str(root) not in ("", ".", "\\") and not root.exists():
        root = root.parent
    try:
        return shutil.disk_usage(root if str(root) else drive).free / 1024 ** 3
    except OSError:
        return -1.0


# 首次启动：迁移旧数据 + 建目录（必须在任何读写之前完成）
MIGRATED_FROM = migrate_legacy_data()
ensure_data_dirs()
if AUTO_PICKED:
    # 自动挑的盘要记下来：否则剩余空间一变，下次启动就换到另一个目录去了
    _write_pointer(USER_DIR)

# 加载 .env（优先程序目录，其次数据目录）
load_dotenv(APP_DIR / ".env")
load_dotenv(USER_DIR / ".env")

DEFAULTS: dict = {
    "personal_space": "",          # 个人空间文件夹路径
    "theme": "light",              # 界面主题：light（默认）/ dark
    "sidebar_collapsed": False,    # 侧边栏是否收起（图标栏）
    "models_dir": _LEGACY_MODELS_DIR,  # 模型下载/缓存路径（默认 D 盘）
    "embedding_device": "auto",    # embedding 计算设备：auto / cuda / cpu
    "auto_index": True,            # 是否自动增量索引（监听文件夹变化）
    "embedding_model": "BAAI/bge-small-zh-v1.5",  # 已选择的 embedding 模型名
    "chat_provider": "ollama",     # 聊天后端：ollama（本地）/ openai（OpenAI 兼容云端 API）
    "chat_model": "qwen2.5:3b",    # 聊天模型名（Ollama 模型名或云端模型 id）
    "cloud_provider": "deepseek",  # 云端服务商（chat_provider=openai 时生效）
    "chat_base_url": "",           # 服务地址：云端 API 地址 / 本地 Ollama 地址（可留空）
    "chunk_size": 500,             # 分块字符数
    "chunk_overlap": 50,           # 分块重叠字符数
    "top_k": 4,                    # 检索返回的片段数
    "search_scope": "doc",         # 检索范围：doc 只搜笔记文档 / all 含代码等全部文件
    "system_prompt": (
        "你是用户的个人文件助手。请仅根据下面提供的【检索到的文件片段】回答问题。"
        "如果片段中没有足够信息，请明确说明无法从个人空间找到答案，"
        "不要编造内容，并尽量给出可能相关的文件路径作为线索。"
    ),
}


# ------------------------------------------------------------------
# 原子写：先写 .tmp 再 os.replace，避免写入中途崩溃/断电把文件截断成空
# ------------------------------------------------------------------
def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding=encoding) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # Windows 上为原子替换


def atomic_write_json(path: str | Path, data, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent))


def read_json(path: str | Path):
    """读取 JSON，损坏/不可读时返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def _backup_path(path: Path) -> Path:
    return Path(str(path) + ".bak")


def _resolve_models_dir(cfg: dict) -> str:
    """模型缓存路径：沿用历史默认 D:\\RAGAssistant\\models，若该盘不存在
    则回落到持久化数据目录内的 models/。"""
    md = cfg.get("models_dir") or ""
    if md.strip():
        return md
    if Path(_LEGACY_MODELS_DIR).parent.exists():
        return _LEGACY_MODELS_DIR
    return str(USER_DIR / "models")


def load_config() -> dict:
    """读取 config.json，缺失的键用默认值补齐。

    文件损坏时：保留损坏现场为 config.json.corrupt，并自动回退到上一版备份
    config.json.bak，绝不静默返回空配置。
    """
    cfg = dict(DEFAULTS)
    data = None
    if CONFIG_PATH.exists():
        data = read_json(CONFIG_PATH)
        if not isinstance(data, dict):
            data = None
            try:
                shutil.copy2(CONFIG_PATH, CONFIG_PATH.with_name(CONFIG_PATH.name + ".corrupt"))
            except OSError:
                pass
            bak = read_json(_backup_path(CONFIG_PATH))
            if isinstance(bak, dict):
                data = bak
    if isinstance(data, dict):
        cfg.update(data)
    cfg["models_dir"] = _resolve_models_dir(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    """将配置写回 config.json（先备份上一版，再原子替换）。"""
    ensure_data_dirs()
    try:
        if CONFIG_PATH.exists():
            shutil.copy2(CONFIG_PATH, _backup_path(CONFIG_PATH))
    except OSError:
        pass
    try:
        atomic_write_json(CONFIG_PATH, cfg)
    except OSError:
        # 兜底：尽量直接写，不要因为 fsync 失败就丢掉设置
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    # 首次保存后补一份备份，保证之后任何时候都有可回滚的版本
    try:
        bak = _backup_path(CONFIG_PATH)
        if not bak.exists() and CONFIG_PATH.exists():
            shutil.copy2(CONFIG_PATH, bak)
    except OSError:
        pass


def get_api_key(provider: str) -> str:
    """根据云端服务商读取对应的 API Key。"""
    env_map = {
        "deepseek": "DEEPSEEK_API_KEY",
        "dashscope": "DASHSCOPE_API_KEY",
        "openai": "OPENAI_API_KEY",
        "moonshot": "MOONSHOT_API_KEY",
    }
    return os.getenv(env_map.get(provider, ""), "") or ""


def get_base_url(provider: str) -> str:
    """根据云端服务商读取默认 base_url。"""
    env_map = {
        "deepseek": "DEEPSEEK_BASE_URL",
        "dashscope": "DASHSCOPE_BASE_URL",
        "openai": "OPENAI_BASE_URL",
        "moonshot": "MOONSHOT_BASE_URL",
    }
    return os.getenv(env_map.get(provider, ""), "") or ""
