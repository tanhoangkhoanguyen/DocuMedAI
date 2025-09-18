# my_graph.py
from __future__ import annotations
import os
from typing import AsyncGenerator, Dict, Any, List, Literal, Optional

from openai import AsyncOpenAI, OpenAI
import asyncio

from constants.settings import settings

_MODEL = "gpt-4o-mini"
_SYSTEM_PROMPT = (
    "You are a helpful, concise assistant. Reply directly, avoid fluff. "
    "If unsure, say you don't know."
)

# Shared clients (one per process)
_async_client = AsyncOpenAI(api_key=settings.openai_api_key)
_sync_client = OpenAI(api_key=settings.openai_api_key)


def _to_messages(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    user_msg = payload["message"]
    msgs: List[Dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": user_msg["role"], "content": user_msg["content"]},
    ]
    return msgs


class Mini4oGraph:
    model: str

    def __init__(self, model: str = _MODEL):
        self.model = model

    async def astream(self, inputs: Dict[str, Any]) -> AsyncGenerator[str, None]:
        messages = _to_messages(inputs)
        stream = await _async_client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.2,
            stream=True,
        )
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
            except Exception:
                continue

    def invoke(self, inputs: Dict[str, Any]) -> str:
        messages = _to_messages(inputs)
        resp = _sync_client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.2,
            stream=False,
        )
        return resp.choices[0].message.content or ""


simple_graph = Mini4oGraph()
