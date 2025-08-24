from __future__ import annotations
from typing import Any, Dict, List
import math


class SimpleMemory:
    """
    Minimal in-process memory store for unit tests and local runs.

    Implements the Memory protocol: add/aupsert/aquery/adump (+ sync wrappers).
    """

    def __init__(self) -> None:
        # keyed by (source, uri) for simple dedupe; preserves insertion order
        self._items: Dict[tuple[str, str], Dict[str, Any]] = {}

    # ---------- sync API ----------
    def add(self, content: str, *, source: str = "log",
            uri: str | None = None, meta: Dict[str, Any] | None = None) -> None:
        content = "" if content is None else str(content)
        source = str(source or "log")
        uri = str(uri) if uri else f"mem:{len(self._items) + 1}"
        meta = meta or {}
        self._items[(source, uri)] = {
            "content": content, "source": source, "uri": uri, "meta": meta}

    def upsert(self, docs: List[Dict[str, Any]]) -> int:
        if not isinstance(docs, list) or any(not isinstance(d, dict) for d in docs):
            raise ValueError("`remember` requires docs: List[Dict]")
        if len(docs) == 0:
            raise ValueError("`remember` requires at least one doc")
        n = 0
        for d in docs:
            content = str(d.get("content", ""))
            source = str(d.get("source", "mem"))
            uri = str(d.get("uri") or f"mem:{len(self._items) + 1}")
            meta = d.get("meta", {}) or {}
            self._items[(source, uri)] = {
                "content": content, "source": source, "uri": uri, "meta": meta}
            n += 1
        return n

    def query(self, q: str, k: int = 3) -> List[Dict[str, Any]]:
        if not q:
            return []
        ql = q.lower()
        qtok = set(ql.split())
        scored: List[tuple[float, Dict[str, Any]]] = []
        for item in self._items.values():
            tl = item.get("content", "").lower()
            if ql in tl:
                score = 1.0
            else:
                itok = set(tl.split())
                score = 0.0
                if qtok and itok:
                    score = len(qtok & itok) / math.sqrt(len(qtok) * len(itok))
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{**it, "score": s} for s, it in scored[: max(0, k)] if s > 0]

    def dump(self, limit: int = 50) -> str:
        if limit <= 0:
            return ""
        items = list(self._items.values())[-limit:]
        return "\n".join(f"- {it.get('source', '?')}:{it.get('uri', '?')} — {it.get('content', '')}" for it in items)

    # ---------- async wrappers ----------
    async def aadd(self, content: str, *, source: str = "log",
                   uri: str | None = None, meta: Dict[str, Any] | None = None) -> None:
        self.add(content, source=source, uri=uri, meta=meta)

    async def aupsert(self, docs: List[Dict[str, Any]]) -> int:
        return self.upsert(docs)

    async def aquery(self, q: str, k: int = 3) -> List[Dict[str, Any]]:
        return self.query(q, k)

    async def adump(self, limit: int = 50) -> str:
        return self.dump(limit)
