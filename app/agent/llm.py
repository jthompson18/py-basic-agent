# app/agent/llm.py
import httpx
import json
import os
import asyncio
from .config import settings
from __future__ import annotations
from typing import List

OLLAMA_HOST = os.getenv(
    "OLLAMA_HOST", "http://host.docker.internal:11434").rstrip("/")
EMBED_MODEL = os.getenv("AGENT_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = int(os.getenv("AGENT_EMBED_DIM", "768"))

SYSTEM_PROMPT = """You are a research + data agent.

TOOLS: Call ONE tool per step by replying ONLY with JSON inside a fenced block:

```json
{"tool":"search","input":{"query":"..."}}
{"tool":"fetch","input":{"url":"https://..."}}
{"tool":"memory","input":{"op":"remember","docs":[{"content":"...","source":"web","uri":"https://...","meta":{"title":"..."}}]}}
{"tool":"memory","input":{"op":"recall","query":"...","k":5}}
{"tool":"etl","input":{"op":"load_csv","path":"./data/example.csv"}}
{"tool":"etl","input":{"op":"transform","path":"./data/example.csv","spec":{"select":["colA","colB"],"filter":{"expr":"colA>10"},"derive":{"colC":"colB*1.1"}},"save":{"format":"parquet","path":"./data/out.parquet"}}}

When done, reply with:
{"final":"...answer with sources and any saved dataset paths..."}
"""


async def _embed_one(client: httpx.AsyncClient, text: str) -> List[float]:
    """
    Call Ollama /api/embeddings for a single text.
    Tries 'input' first (current API), falls back to 'prompt' for older daemons.
    Returns a list[float].
    """
    # Try 'input'
    r = await client.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": EMBED_MODEL, "input": text},
        timeout=30.0,
    )
    if r.status_code >= 400:
        # Fallback: older servers expect 'prompt'
        r = await client.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=30.0,
        )
    r.raise_for_status()
    data = r.json()
    # Common shapes: {"embedding":[...]} or {"data":[{"embedding":[...]}]}
    if "embedding" in data and isinstance(data["embedding"], list):
        return data["embedding"]
    if "data" in data and isinstance(data["data"], list) and data["data"]:
        item = data["data"][0]
        if isinstance(item, dict) and isinstance(item.get("embedding"), list):
            return item["embedding"]
    raise RuntimeError(f"Unexpected embeddings response shape: {data}")


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Batch embed via Ollama. Sequential calls keep it simple/reliable.
    """
    if not texts:
        return []
    async with httpx.AsyncClient() as client:
        out: List[List[float]] = []
        for t in texts:
            vec = await _embed_one(client, t)
            if EMBED_DIM and len(vec) != EMBED_DIM:
                # If dimension mismatch, you can either resize or raise.
                # We'll raise so you notice misconfig (e.g. using an 1024-d model).
                raise ValueError(
                    f"Embedding dim {len(vec)} != expected {EMBED_DIM}. "
                    "Set AGENT_EMBED_DIM to match your model."
                )
            out.append(vec)
        return out

# ---- (optional) stubs other code may import ----------------------------------


async def _chat_v1(messages: list[dict], temperature: float | None) -> str:
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": temperature if temperature is not None else settings.temperature,
    }
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=120) as client:
        r = await client.post("/v1/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]


async def _chat_native(messages: list[dict], temperature: float | None) -> str:
    payload = {
        "model": settings.model,
        "messages": messages,
        "options": {"temperature": temperature if temperature is not None else settings.temperature},
    }
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=120) as client:
        r = await client.post("/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            if "message" in data and isinstance(data["message"], dict):
                if "content" in data["message"]:
                    return data["message"]["content"]
            return data.get("content") or data.get("response") or ""
        return ""


async def chat(messages: list[dict], temperature: float | None = None) -> str:
    try:
        return await _chat_v1(messages, temperature)
    except Exception:
        return await _chat_native(messages, temperature)

# ---- tiny helpers for summaries - ---


async def summarize_text(text: str, purpose: str) -> str:
    """Generic short summary (bulleted, <=6 bullets)."""
    sys = {"role": "system",
           "content": f"You are a concise assistant. Summarize for: {purpose}. Use 3-6 bullets."}
    usr = {"role": "user", "content": text[:12000]}
    try:
        out = await chat([sys, usr], temperature=0.1)
        return out.strip()
    except Exception as e:
        return f"(summary error: {e})"


async def summarize_search(results: list[dict]) -> str:
    # Turn results into a short JSON and summarize
    payload = json.dumps(results, ensure_ascii=False)
    return await summarize_text(payload, "explain what these search results contain and how they relate to the user query")


async def summarize_etl(result_obj: dict) -> str:
    payload = json.dumps(result_obj, ensure_ascii=False)
    return await summarize_text(payload, "explain what ETL was performed, the key columns/row count, and where outputs were saved")
