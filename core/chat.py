"""RAG 聊天引擎：检索 + 构建提示词 + 调用大模型（OpenAI 兼容 / Ollama）。"""
from __future__ import annotations

from typing import Any, Iterator

import requests

from .indexer import Indexer


def _build_context(hits: list[dict[str, Any]]) -> str:
    """把检索结果拼成给模型的上下文。"""
    if not hits:
        return "（未检索到相关片段）"
    parts = []
    for i, h in enumerate(hits, 1):
        parts.append(f"[片段 {i}] 来源文件: {h['file']}\n{h['text']}")
    return "\n\n".join(parts)


class ChatEngine:
    """封装 OpenAI 兼容 / Ollama 两种后端的对话。"""

    def __init__(self, provider: str, model: str, base_url: str = "", api_key: str = ""):
        self.provider = provider  # "openai" 或 "ollama"
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self._client = None
        if provider == "openai":
            from openai import OpenAI

            kwargs: dict[str, Any] = {"api_key": api_key or "EMPTY"}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = OpenAI(**kwargs)

    # ---------- 聊天接口 ----------
    def chat_stream(
        self,
        system_prompt: str,
        question: str,
        context: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> Iterator[str]:
        """流式返回回复。history: [{"role": "user"/"assistant", "content": ...}]"""
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        if context:
            messages.append(
                {"role": "user", "content": f"【检索到的文件片段】\n{context}\n\n【用户问题】\n{question}"}
            )
        else:
            messages.append({"role": "user", "content": question})

        if self.provider == "ollama":
            yield from self._ollama_stream(messages)
        else:
            yield from self._openai_stream(messages)

    def _openai_stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            temperature=0.3,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    def _ollama_stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
        url = (self.base_url or "http://localhost:11434").rstrip("/") + "/api/chat"
        payload = {"model": self.model, "messages": messages, "stream": True}
        with requests.post(url, json=payload, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                import json

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                piece = data.get("message", {}).get("content")
                if piece:
                    yield piece
                if data.get("done"):
                    break


class RAGAssistant:
    """组合 Indexer 与 ChatEngine，提供带检索的问答。"""

    def __init__(self, indexer: Indexer, engine: ChatEngine, system_prompt: str,
                 top_k: int = 4, scope: str = "all"):
        self.indexer = indexer
        self.engine = engine
        self.system_prompt = system_prompt
        self.top_k = top_k
        self.scope = scope      # "doc" 只搜笔记文档 / "all" 含代码

    def answer_stream(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[list[dict[str, Any]], Iterator[str]]:
        """返回 (检索到的片段列表, 流式回复生成器)。"""
        hits = self.indexer.search(question, self.top_k, self.scope)
        context = _build_context(hits)
        stream = self.engine.chat_stream(self.system_prompt, question, context, history)
        return hits, stream
