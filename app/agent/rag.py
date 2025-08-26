from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .memory import get_memory
from . import llm
from .schemas import Message

DEFAULT_PATTERNS = ("**/*.md", "**/*.txt")
CHUNK_WORDS = 800
OVERLAP_WORDS = 150


def _read_files(root: Path, patterns: Iterable[str]) -> List[Tuple[Path, str]]:
    out: List[Tuple[Path, str]] = []
    for pat in patterns:
        for p in root.rglob(pat):
            if p.is_file():
                try:
                    out.append((p, p.read_text(encoding="utf-8")))
                except Exception:
                    pass
    return out


def _chunk_words(text: str, n: int = CHUNK_WORDS, overlap: int = OVERLAP_WORDS) -> List[str]:
    words = text.split()
    if not words:
        return []
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i:i + n]).strip()
        if chunk:
            chunks.append(chunk)
        if i + n >= len(words):
            break
        i += n - overlap
    return chunks


async def ingest_dir(path: str, patterns: Iterable[str] = DEFAULT_PATTERNS) -> Dict[str, int]:
    """Walk a folder and upsert .md/.txt chunks into vector memory (pgvector)."""
    mem = get_memory()
    kb = Path(path).resolve()
    files = _read_files(kb, patterns)
    total_chunks = 0
    for fpath, text in files:
        chunks = _chunk_words(text)
        for idx, chunk in enumerate(chunks):
            meta = {"chunk": idx + 1, "chunks": len(chunks)}
            await mem.aadd(content=chunk, source=fpath.name, uri=str(fpath), meta=meta)
            total_chunks += 1
    return {"files": len(files), "chunks": total_chunks}


async def add_text(text: str, *, source: str = "adhoc", uri: str = "mem://adhoc", meta: Dict[str, Any] | None = None) -> None:
    """Add an ad-hoc snippet into memory."""
    mem = get_memory()
    await mem.aadd(content=text, source=source, uri=uri, meta=meta or {})


async def retrieve(query: str, k: int = 6) -> List[Dict[str, Any]]:
    """Vector retrieval (with text fallback) via pgvector store."""
    mem = get_memory()
    hits = await mem.aquery(query, k=k)
    return [
        {
            "score": h.get("score"),
            "source": h.get("source"),
            "uri": h.get("uri"),
            "text": h.get("content"),
            "meta": h.get("meta"),
        }
        for h in hits
    ]


async def ask_with_context(question: str, k: int = 6) -> Dict[str, Any]:
    """Retrieve context and answer using your existing LLM client (llm.chat)."""
    hits = await retrieve(question, k=k)
    context = "\n\n---\n\n".join(
        f"[{h['source'] or 'doc'}] {h['text']}" for h in hits)

    # Uses agent/llm.py::chat (Message list).  :contentReference[oaicite:5]{index=5}
    system = "You are a precise assistant. Answer ONLY from CONTEXT. If not in CONTEXT, reply 'I don't know.'"
    user = f"QUESTION:\n{question}\n\nCONTEXT:\n{context}"
    msgs = [Message(role="system", content=system),
            Message(role="user", content=user)]
    answer = await llm.chat(msgs, temperature=0.0)

    return {"answer": answer, "hits": hits}
