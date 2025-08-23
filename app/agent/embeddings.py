import httpx
import numpy as np
import json
import os
from .config import settings


async def embed_texts(texts: list[str]) -> np.ndarray:
    payload = {"model": settings.embed_model, "input": texts}
    # Try OpenAI-compatible first, then fallback to native
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=180) as client:
        try:
            r = await client.post("/v1/embeddings", json=payload)
            r.raise_for_status()
            data = r.json()
            vecs = [d["embedding"] for d in data["data"]]
        except Exception:
            r = await client.post("/api/embeddings", json={"model": settings.embed_model, "prompt": texts})
            r.raise_for_status()
            data = r.json()
            vecs = data["embeddings"]
    return np.array(vecs, dtype="float32")
