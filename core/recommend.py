"""根据硬件配置推荐 embedding / chat 模型。"""
from __future__ import annotations

from typing import Any

from .hardware import get_hardware_summary
from .models_catalog import (
    CLOUD_MODELS,
    EMBEDDING_MODELS,
    OLLAMA_MODELS,
)

# 算力等级顺序（从低到高）
_TIER_ORDER = ["cpu", "low", "medium", "high"]


def _tier_index(tier: str) -> int:
    return _TIER_ORDER.index(tier) if tier in _TIER_ORDER else 0


def recommend_embedding(tier: str, lang_hint: str = "中文") -> dict[str, Any]:
    """推荐一个 embedding 模型。lang_hint: 中文 / 英文 / 多语言。"""
    # 只保留算力满足的候选
    candidates = [m for m in EMBEDDING_MODELS if _tier_index(m["min_tier"]) <= _tier_index(tier)]
    if not candidates:
        candidates = [m for m in EMBEDDING_MODELS if m["min_tier"] == "cpu"]

    # 优先匹配语言，其次选等级最高（更强）的
    lang_rank = {"多语言": 2, "中文": 2 if lang_hint == "中文" else 1, "英文": 2 if lang_hint == "英文" else 1}

    def score(m: dict) -> tuple[int, int]:
        return (lang_rank.get(m["lang"], 0), _tier_index(m["min_tier"]))

    best = max(candidates, key=score)
    return best


def recommend_chat(tier: str) -> dict[str, Any]:
    """推荐 chat 部署方式与模型。返回本地 + 云端两套建议。"""
    local_candidates = [m for m in OLLAMA_MODELS if _tier_index(m["min_tier"]) <= _tier_index(tier)]
    local = max(local_candidates, key=lambda m: _tier_index(m["min_tier"])) if local_candidates else OLLAMA_MODELS[0]

    # 云端默认推荐 DeepSeek，若配置了其他 Key 再调整
    cloud = CLOUD_MODELS[0]

    return {
        "tier": tier,
        "local": local,
        "cloud": cloud,
        "prefer_cloud": tier == "cpu",
    }


def get_recommendation(lang_hint: str = "中文") -> dict[str, Any]:
    """汇总：硬件 + 推荐结果。"""
    hw = get_hardware_summary()
    tier = hw["tier"]
    return {
        "hardware": hw,
        "embedding": recommend_embedding(tier, lang_hint),
        "chat": recommend_chat(tier),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(get_recommendation(), ensure_ascii=False, indent=2))
