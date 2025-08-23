# app/agent/llm.py
import httpx
import json
from .config import settings

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
