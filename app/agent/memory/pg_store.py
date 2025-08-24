# app/agent/memory/pg_store.py
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List

import psycopg
from psycopg.rows import dict_row
from pgvector.psycopg import register_vector

DB_URL = os.getenv(
    "AGENT_DB_URL", "postgresql://agent:agentpass@pgvector:5432/agentdb")
TABLE = os.getenv("AGENT_PGVECTOR_TABLE", "docs")
# must match your embedding model
EMBED_DIM = int(os.getenv("AGENT_EMBED_DIM", "768"))


class PgVectorMemory:
    """
    Simple pgvector-backed memory with async insert/query.
    Requires:
      - PostgreSQL with pgvector installed
      - Table with embedding VECTOR(EMBED_DIM)
      - An async embedder (agent.llm.embed_texts)
    """

    def __init__(self, db_url: str | None = None):
        self.db_url = db_url or DB_URL
        self.conn = psycopg.connect(self.db_url, autocommit=True)
        register_vector(self.conn)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.conn.cursor() as cur:
            # Ensure extension & table exist
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE} (
                  id BIGSERIAL PRIMARY KEY,
                  source   TEXT NOT NULL,
                  uri      TEXT NOT NULL,
                  meta     JSONB,
                  content  TEXT NOT NULL,
                  embedding VECTOR({EMBED_DIM}),
                  UNIQUE (source, uri)
                );
                """
            )
            # Optional indexes:
            # cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_embedding ON {TABLE} USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);")

    # ---------- public async API ----------

    async def aupsert(self, docs: List[Dict[str, Any]]) -> int:
        """
        Insert or update docs with fresh embeddings.
        doc: {content:str, source:str, uri:str, meta:dict}
        """
        contents = [d.get("content", "") for d in docs]
        # embed
        from ..llm import embed_texts
        vectors = await embed_texts(contents)

        with self.conn.cursor() as cur:
            for d, vec in zip(docs, vectors):
                cur.execute(
                    f"""
                    INSERT INTO {TABLE} (source, uri, meta, content, embedding)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (source, uri) DO UPDATE
                    SET meta = EXCLUDED.meta,
                        content = EXCLUDED.content,
                        embedding = EXCLUDED.embedding;
                    """,
                    (
                        d.get("source", ""),
                        d.get("uri", ""),
                        json.dumps(d.get("meta", {})),
                        d.get("content", ""),
                        vec,  # pgvector adapts Python list automatically
                    ),
                )
        return len(docs)

    async def aquery(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """
        Vector similarity search using cosine distance (<=>).
        Returns rows ordered by best match.
        """
        from ..llm import embed_texts
        qvec = (await embed_texts([query]))[0]
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT source, uri, meta, content,
                       1 - (embedding <=> %s) AS score
                FROM {TABLE}
                ORDER BY embedding <=> %s
                LIMIT %s;
                """,
                (qvec, qvec, k),
            )
            rows = cur.fetchall()
        return rows

    # ---------- optional sync wrappers ----------

    def upsert(self, docs: List[Dict[str, Any]]) -> int:
        return asyncio.run(self.aupsert(docs))

    def query(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        return asyncio.run(self.aquery(query, k))
