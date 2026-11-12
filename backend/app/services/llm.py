"""Thin async wrapper around the Ollama Python client (Llama 3.1 + embeddings)."""
import json
from typing import AsyncIterator

import ollama

from ..config import settings

client = ollama.AsyncClient(host=settings.ollama_host)


async def chat_json(system: str, user: str, model: str | None = None) -> dict:
    resp = await client.chat(
        model=model or settings.llm_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        format="json",
        keep_alive=settings.keep_alive,
        options={"temperature": 0, "num_ctx": settings.num_ctx, "num_predict": settings.extract_max_tokens},
    )
    try:
        return json.loads(resp["message"]["content"])
    except json.JSONDecodeError:
        return {}


async def chat_stream(messages: list[dict], temperature: float = 0.2, num_ctx: int | None = None) -> AsyncIterator[str]:
    stream = await client.chat(
        model=settings.llm_model,
        messages=messages,
        stream=True,
        keep_alive=settings.keep_alive,
        options={"temperature": temperature, "num_ctx": num_ctx or settings.num_ctx},
    )
    async for part in stream:
        token = part["message"]["content"]
        if token:
            yield token


async def vision_json(prompt: str, image: bytes, temperature: float = 0.0) -> dict:
    """Ask the on-prem vision model (Gemma) about an image; returns parsed JSON or {}."""
    resp = await client.chat(
        model=settings.vision_model,
        messages=[{"role": "user", "content": prompt, "images": [image]}],
        format="json",
        keep_alive=settings.keep_alive,
        options={"temperature": temperature, "num_ctx": settings.num_ctx, "num_predict": settings.vision_max_tokens},
    )
    try:
        return json.loads(resp["message"]["content"])
    except json.JSONDecodeError:
        return {}


async def embed(texts: list[str]) -> list[list[float]]:
    resp = await client.embed(model=settings.embed_model, input=texts)
    return resp["embeddings"]