# app/agent/memory/pg_store.py
from __future__ import annotations

import os
import asyncio
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

# pgvector adapter (preferred); fall back to string if missing
try:
    from pgvector.psycopg import register_vector, Vector  # type: ignore
    HAVE_VECTOR = True
except Exception:  # pragma: no cover
    HAVE_VECTOR = False
    Vector = None  # type: ignore

from ..llm import embed_texts


def _vector_param(vec: Optional[List[float]]) -> Any:
    """
    Produce a parameter compatible with a 'vector' column.

    Preferred: pgvector.psycopg.Vector(vec)
    Fallback:  textual "[1,2,3]" that Postgres vector can parse.
    """
    if not vec:
        return None
    if HAVE_VECTOR and Vector is not None:
        return Vector(vec)  # exact adapter
    # textual fallback
    return "[" + ",".join(str(float(x)) for x in vec) + "]"


class PgVectorMemory:
    """
    Postgres/pgvector-backed memory store.

    Table schema assumed (created by your container init):
      docs(
        id bigserial primary key,
        source text not null,
        uri text not null,
        meta jsonb,
        content text not null,
        embedding vector(<AGENT_EMBED_DIM>)
      );
      unique(source, uri)
    """

    def __init__(self) -> None:
        self.db_url = os.getenv(
            "AGENT_DB_URL",
            "postgresql://agent:agentpass@pgvector:5432/agentdb",
        )
        self.dim = int(os.getenv("AGENT_EMBED_DIM", "768"))
        self.conn = psycopg.connect(self.db_url, autocommit=True)
        if HAVE_VECTOR:
            try:
                register_vector(self.conn)
            except Exception:
                # Non-fatal; we still have fallback
                pass

    # ------------------------
    # Upsert documents (async)
    # ------------------------
    async def aupsert(self, docs: Iterable[Dict[str, Any]]) -> int:
        items = list(docs)
        texts = [str(d.get("content", "")) for d in items]
        embs = await embed_texts(texts)

        def _write() -> int:
            with self.conn.cursor() as cur:
                for d, emb in zip(items, embs):
                    source = str(d.get("source", "note"))
                    uri = str(d.get("uri", ""))
                    meta = d.get("meta") or {}
                    content = str(d.get("content", ""))

                    cur.execute(
                        """
                        INSERT INTO docs (source, uri, meta, content, embedding)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (source, uri)
                        DO UPDATE SET
                          meta = EXCLUDED.meta,
                          content = EXCLUDED.content,
                          embedding = EXCLUDED.embedding
                        """,
                        (
                            source,
                            uri,
                            Json(meta),
                            content,
                            _vector_param(emb),
                        ),
                    )
            return len(items)

        return await asyncio.to_thread(_write)

    # ------------------------
    # Query (semantic w/ fallback)
    # ------------------------
    async def aquery(self, query: str, k: int = 3) -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []

        vecs = await embed_texts([q])
        qemb: Optional[List[float]] = vecs[0] if vecs and isinstance(
            vecs[0], list) and vecs[0] else None

        def _select_vec() -> List[Dict[str, Any]]:
            with self.conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT source, uri, meta, content,
                           1 - (embedding <=> %s) AS score
                    FROM docs
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s
                    LIMIT %s
                    """,
                    (_vector_param(qemb), _vector_param(qemb), k),
                )
                return list(cur.fetchall())

        def _select_text() -> List[Dict[str, Any]]:
            like = f"%{q}%"
            with self.conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT source, uri, meta, content,
                           0.0 AS score
                    FROM docs
                    WHERE content ILIKE %s OR uri ILIKE %s OR source ILIKE %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (like, like, like, k),
                )
                return list(cur.fetchall())

        return await asyncio.to_thread(_select_vec if qemb else _select_text)

    # ------------------------
    # Dump recent notes (async)
    # ------------------------
    async def adump(self, limit: int = 10) -> str:
        def _sel() -> str:
            with self.conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT source, uri, content
                    FROM docs
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
            lines = [
                f"- {r.get('source')}:{r.get('uri')} — {(r.get('content') or '')[:120]}"
                for r in rows
            ]
            return "\n".join(lines)

        return await asyncio.to_thread(_sel)

    # ------------------------
    # Convenience sync 'add' for quick notes/errors (no embedding)
    # ------------------------
    def add(self, text: str, source: str = "note", uri: Optional[str] = None, meta: Optional[Dict[str, Any]] = None) -> None:
        """
        Insert a single plaintext note without embedding. Safe to call from sync code
        (e.g., error paths) and cheap enough to do inline.
        """
        u = uri or f"note_{os.getpid()}_{id(self)}"
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO docs (source, uri, meta, content, embedding)
                VALUES (%s, %s, %s, %s, NULL)
                ON CONFLICT (source, uri) DO NOTHING
                """,
                (source, u, Json(meta or {}), text),
            )

    # add inside class PgVectorMemory

    async def aadd(
        self,
        content: str,
        source: str = "note",
        uri: str | None = None,
        meta: dict | None = None,
    ) -> int | None:
        """
        Add a single note/document to vector memory.
        - Embeds `content` via Ollama (llm.embed_texts).
        - Upserts into docs(source, uri) with meta (JSONB), content, and embedding.
        Returns inserted row id (or None).
        """
        import time
        if not uri:
            uri = f"note:{int(time.time() * 1000)}"

        # 1) embed
        try:
            vecs = await llm.embed_texts([content])
            emb = vecs[0] if vecs else [0.0] * self.dim
        except Exception:
            # fall back to zeros if embed fails
            emb = [0.0] * self.dim

        # 2) write (in a thread to avoid blocking the loop)
        def _insert() -> int | None:
            from psycopg.rows import dict_row
            with self.conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    INSERT INTO docs(source, uri, meta, content, embedding)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (source, uri)
                    DO UPDATE
                        SET meta = EXCLUDED.meta,
                            content = EXCLUDED.content,
                            embedding = EXCLUDED.embedding
                    RETURNING id
                    """,
                    (source, uri, Json(meta or {}), content, emb),
                )
                row = cur.fetchone()
                return int(row["id"]) if row and "id" in row else None

        return await asyncio.to_thread(_insert)
