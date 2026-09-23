"""模型目录：候选 embedding 模型与 chat 模型，含下载/部署信息。"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------
# Embedding 模型目录
# ---------------------------------------------------------------
EMBEDDING_MODELS: list[dict[str, Any]] = [
    {
        "id": "BAAI/bge-small-zh-v1.5",
        "name": "bge-small-zh-v1.5",
        "dim": 512,
        "size_gb": 0.1,
        "lang": "中文",
        "min_tier": "cpu",
        "desc": "轻量中文向量模型，速度快、占用低，适合 CPU 或低配环境。",
        "hf_url": "https://huggingface.co/BAAI/bge-small-zh-v1.5",
    },
    {
        "id": "BAAI/bge-base-zh-v1.5",
        "name": "bge-base-zh-v1.5",
        "dim": 768,
        "size_gb": 0.4,
        "lang": "中文",
        "min_tier": "low",
        "desc": "平衡型中文向量模型，效果与速度兼顾。",
        "hf_url": "https://huggingface.co/BAAI/bge-base-zh-v1.5",
    },
    {
        "id": "BAAI/bge-large-zh-v1.5",
        "name": "bge-large-zh-v1.5",
        "dim": 1024,
        "size_gb": 1.3,
        "lang": "中文",
        "min_tier": "medium",
        "desc": "高质量中文向量模型，检索精度更高，需要更强算力。",
        "hf_url": "https://huggingface.co/BAAI/bge-large-zh-v1.5",
    },
    {
        "id": "BAAI/bge-m3",
        "name": "bge-m3",
        "dim": 1024,
        "size_gb": 2.3,
        "lang": "多语言",
        "min_tier": "high",
        "desc": "多语言、多粒度向量模型，支持中英混合场景，效果最佳。",
        "hf_url": "https://huggingface.co/BAAI/bge-m3",
    },
    {
        "id": "sentence-transformers/all-MiniLM-L6-v2",
        "name": "all-MiniLM-L6-v2",
        "dim": 384,
        "size_gb": 0.09,
        "lang": "英文",
        "min_tier": "cpu",
        "desc": "经典轻量英文向量模型，英文文档场景的首选。",
        "hf_url": "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2",
    },
    {
        "id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "name": "multilingual-MiniLM-L12-v2",
        "dim": 384,
        "size_gb": 0.47,
        "lang": "多语言",
        "min_tier": "cpu",
        "desc": "轻量多语言向量模型，无需 GPU 即可运行，适合通用场景。",
        "hf_url": "https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    },
]

# ---------------------------------------------------------------
# 本地 chat 模型目录（通过 Ollama 部署）
# ---------------------------------------------------------------
OLLAMA_MODELS: list[dict[str, Any]] = [
    {
        "id": "qwen2.5:1.5b",
        "name": "Qwen2.5 1.5B",
        "size_gb": 1.0,
        "min_tier": "cpu",
        "desc": "最小的实用中文对话模型，CPU 也能跑。",
        "ollama_cmd": "ollama pull qwen2.5:1.5b",
        "ollama_url": "https://ollama.com/library/qwen2.5:1.5b",
    },
    {
        "id": "qwen2.5:3b",
        "name": "Qwen2.5 3B",
        "size_gb": 2.0,
        "min_tier": "low",
        "desc": "小体积中文模型，4GB 显存即可流畅运行，推荐默认选择。",
        "ollama_cmd": "ollama pull qwen2.5:3b",
        "ollama_url": "https://ollama.com/library/qwen2.5:3b",
    },
    {
        "id": "qwen2.5:7b",
        "name": "Qwen2.5 7B",
        "size_gb": 4.7,
        "min_tier": "medium",
        "desc": "主流 7B 中文模型，8GB 显存即可流畅运行。",
        "ollama_cmd": "ollama pull qwen2.5:7b",
        "ollama_url": "https://ollama.com/library/qwen2.5:7b",
    },
    {
        "id": "qwen2.5:14b",
        "name": "Qwen2.5 14B",
        "size_gb": 9.0,
        "min_tier": "high",
        "desc": "更强的中文理解与推理能力，建议 16GB 以上显存。",
        "ollama_cmd": "ollama pull qwen2.5:14b",
        "ollama_url": "https://ollama.com/library/qwen2.5:14b",
    },
    {
        "id": "llama3.1:8b",
        "name": "Llama 3.1 8B",
        "size_gb": 4.9,
        "min_tier": "medium",
        "desc": "Meta 开源英文为主模型，英文问答能力强。",
        "ollama_cmd": "ollama pull llama3.1:8b",
        "ollama_url": "https://ollama.com/library/llama3.1:8b",
    },
    {
        "id": "deepseek-r1:7b",
        "name": "DeepSeek-R1 7B",
        "size_gb": 4.7,
        "min_tier": "medium",
        "desc": "带思考链（CoT）的推理模型，适合复杂问题。",
        "ollama_cmd": "ollama pull deepseek-r1:7b",
        "ollama_url": "https://ollama.com/library/deepseek-r1:7b",
    },
]

# ---------------------------------------------------------------
# 云端 chat 模型目录（OpenAI 兼容 API）
# ---------------------------------------------------------------
CLOUD_MODELS: list[dict[str, Any]] = [
    {
        "provider": "deepseek",
        "id": "deepseek-chat",
        "name": "DeepSeek Chat (V3)",
        "desc": "国内可用的高性价比通用模型。",
        "signup_url": "https://platform.deepseek.com",
        "base_url": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
    },
    {
        "provider": "dashscope",
        "id": "qwen-plus",
        "name": "通义千问 Qwen-Plus",
        "desc": "阿里云百炼平台的通义千问模型。",
        "signup_url": "https://bailian.console.aliyun.com",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_env": "DASHSCOPE_API_KEY",
    },
    {
        "provider": "moonshot",
        "id": "moonshot-v1-8k",
        "name": "Moonshot Kimi",
        "desc": "长上下文能力突出的国产模型。",
        "signup_url": "https://platform.moonshot.cn",
        "base_url": "https://api.moonshot.cn/v1",
        "key_env": "MOONSHOT_API_KEY",
    },
    {
        "provider": "openai",
        "id": "gpt-4o-mini",
        "name": "OpenAI GPT-4o-mini",
        "desc": "OpenAI 的轻量快速模型。",
        "signup_url": "https://platform.openai.com",
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
    },
]

def get_ollama_model(mid: str) -> dict[str, Any] | None:
    for m in OLLAMA_MODELS:
        if m["id"] == mid:
            return m
    return None


def get_cloud_model(mid: str) -> dict[str, Any] | None:
    for m in CLOUD_MODELS:
        if m["id"] == mid:
            return m
    return None
