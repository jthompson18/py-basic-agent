# app/agent/llm.py
import httpx
from .config import settings

# Keep this in sync with your tools (search/fetch/memory/etl)
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
        # Native responses usually include message.content
        if isinstance(data, dict):
            if "message" in data and isinstance(data["message"], dict):
                if "content" in data["message"]:
                    return data["message"]["content"]
            # Some builds return a top-level content/response
            return data.get("content") or data.get("response") or ""
        return ""


async def chat(messages: list[dict], temperature: float | None = None) -> str:
    """
    Sends a chat to Ollama. Expects messages already include a system prompt.
    Falls back from OpenAI-compatible to native Ollama if needed.
    """
    try:
        return await _chat_v1(messages, temperature)
    except Exception as e1:
        try:
            return await _chat_native(messages, temperature)
        except Exception as e2:
            raise RuntimeError(
                f"Failed to reach Ollama at {settings.ollama_base_url}. "
                f"Tried /v1/chat/completions and /api/chat. "
                f"Errors: {type(e1).name}: {e1}; {type(e2).name}: {e2}. "
                "Is Ollama running, and is OLLAMA_BASE_URL correct?"
            )

all = ["SYSTEM_PROMPT", "chat"]
