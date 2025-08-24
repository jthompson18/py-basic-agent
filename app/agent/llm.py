from __future__ import annotations

import os
import asyncio
import httpx
from typing import Any, Dict, Iterable, List, Sequence
from .schemas import Message
from .config import settings

# ---------- config ----------

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
CHAT_MODEL = os.getenv("AGENT_MODEL", "llama3.1:8b")
EMBED_MODEL = os.getenv("AGENT_EMBED_MODEL", "nomic-embed-text")
# used by pgvector schema, not here directly
EMBED_DIM = int(os.getenv("AGENT_EMBED_DIM", "768"))


# New: configurable timeouts via env, with sensible defaults
READ_TIMEOUT = float(os.getenv("AGENT_LLM_TIMEOUT_READ", "120"))
CONNECT_TIMEOUT = float(os.getenv("AGENT_LLM_TIMEOUT_CONNECT", "10"))
WRITE_TIMEOUT = float(os.getenv("AGENT_LLM_TIMEOUT_WRITE", "60"))
POOL_TIMEOUT = float(os.getenv("AGENT_LLM_TIMEOUT_POOL", "60"))

# New: keep_alive hint so Ollama keeps the model warm between steps
KEEP_ALIVE = os.getenv("AGENT_LLM_KEEP_ALIVE", "30m")

# Hard cap per-message text to avoid giant contexts causing slow/timeout
_PER_MSG_CHARS = int(os.getenv("AGENT_LLM_PER_MSG_LIMIT", "4000"))


SYSTEM_PROMPT = """You are a research + data agent.

TOOLS: Call ONE tool per step by replying ONLY with JSON inside a fenced block:

```json
{"tool":"search","input":{"query":"..."}}
{"tool":"fetch","input":{"url":"https://..."}}
{"tool":"memory","input":{"op":"remember","docs":[{"content":"...","source":"web","uri":"https://...","meta":{"title":"..."}}]}}
{"tool":"memory","input":{"op":"recall","query":"...","k":5}}
{"tool":"etl","input":{"op":"load_csv","path":"./data/example.csv"}}
{"tool":"etl","input":{"op":"transform","path":"./data/example.csv","spec":{"select":["colA","colB"],"rename":{"old":"new"},"limit":100},"save":{"format":"csv","path":"./data/out.csv"}}}

RULES:

One tool per step. No extra prose outside the fenced JSON blocks.

Prefer to memory.recall before searching if prior notes might exist.

After search, consider fetch for top results that look promising, then memory.remember to store key facts/snippets with title & URL.

Use ETL only when asked to transform data or when it clearly helps answer the task.

Keep JSON valid (no trailing commas) and minimal.

WHEN DONE, reply with a single fenced JSON block:

{
  "final": "Summary:\n• A concise, plain-English synthesis of your findings.\n\nKey facts:\n• Bullet points of the most important answers.\n\nSources:\n1) <Title> — <URL>\n2) <Title> — <URL>\n\nSaved datasets (if any):\n• ./data/out.csv\n"
}

Your final MUST include a short, human-readable “Summary”, a few “Key facts” bullets, and a “Sources” list with titles and URLs used. If you created files, include a “Saved datasets” section with their paths.
"""

# ---------- core chat ----------


def _as_chat_payload(messages: List[Message], temperature: float) -> Dict:
    # Trim each message content to keep request snappy
    safe_msgs: List[Dict[str, str]] = []
    for m in messages:
        content = m.content or ""
        if isinstance(content, str) and len(content) > _PER_MSG_CHARS:
            content = content[:_PER_MSG_CHARS] + " …(truncated)"
        safe_msgs.append({"role": m.role, "content": content})

    return {
        "model": settings.model or "llama3.1:8b",
        "messages": safe_msgs,
        "stream": False,
        "options": {
            "temperature": temperature,
            # keep the model loaded so subsequent steps don’t pay cold-start cost
            "keep_alive": KEEP_ALIVE,
        },
    }


async def chat(messages: List[Message], temperature: float = 0.2) -> str:
    payload = _as_chat_payload(messages, temperature)

    timeouts = httpx.Timeout(
        connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=WRITE_TIMEOUT, pool=POOL_TIMEOUT
    )
    # A couple quick retries handle cold model spins or flaky networking.
    retriable = (httpx.ReadTimeout, httpx.RemoteProtocolError)

    async with httpx.AsyncClient(base_url=OLLAMA_HOST, timeout=timeouts) as client:
        for attempt in range(3):
            try:
                r = await client.post("/api/chat", json=payload)
                r.raise_for_status()
                data = r.json()
                # Ollama can return {"message":{"content":...}} or {"response":...}
                text = (data.get("message", {}) or {}).get(
                    "content") or data.get("response") or ""
                return (text or "").strip()
            except retriable as e:
                if attempt == 2:
                    raise
                # small backoff; first retry is quick, second a bit longer
                await asyncio.sleep(1 + attempt)


# ---------- embeddings (used by pgvector memory) ----------


async def embed_texts(texts: Iterable[str]) -> List[List[float]]:
    """Return a list of embedding vectors for the given texts via Ollama."""
    texts = [t if isinstance(t, str) else str(t) for t in texts]
    async with httpx.AsyncClient(base_url=OLLAMA_HOST, timeout=30.0) as client:
        async def _embed_one(t: str) -> List[float]:
            r = await client.post("/api/embeddings", json={"model": EMBED_MODEL, "input": t})
            r.raise_for_status()
            data = r.json()
            # Ollama returns {"embedding": [..]}
            vec = data.get("embedding")
            if not isinstance(vec, list):
                return []
            return [float(x) for x in vec]

        # Batch concurrently but avoid huge fan-out
        results: List[List[float]] = []
        B = 16
        for i in range(0, len(texts), B):
            chunk = texts[i: i + B]
            results.extend(await asyncio.gather(*[_embed_one(t) for t in chunk]))
        return results


# ---------- summarizers used by REPL verbose output ----------


async def summarize_search(payload: Dict[str, Any]) -> str:
    """
    Summarize a search step (serper + fetched pages).
    payload shape is set by core/tools; we just stringify highlights.
    """
    msgs = [
        {"role": "system", "content": "Summarize the following search results succinctly."},
        {"role": "user", "content": str(payload)},
    ]
    try:
        return await chat(msgs, temperature=0.0)
    except Exception:
        return "(search summary unavailable)"


async def summarize_etl(payload: Dict[str, Any]) -> str:
    """Summarize an ETL run: what was loaded, how it was transformed, and where saved."""
    msgs = [
        {"role": "system", "content": "Summarize this ETL process for a changelog."},
        {"role": "user", "content": str(payload)},
    ]
    try:
        return await chat(msgs, temperature=0.0)
    except Exception:
        return "(etl summary unavailable)"
