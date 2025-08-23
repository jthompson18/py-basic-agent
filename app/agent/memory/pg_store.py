# app/agent/memory/pg_store.py
from typing import List, Dict, Any
import os
import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Json
from ..embeddings import embed_texts
from ..config import settings


class PgVectorMemory:
    def __init__(self):
        # autocommit so DDL is immediate
        self.conn = psycopg.connect(
            host=settings.pghost,
            port=settings.pgport,
            user=settings.pguser,
            password=settings.pgpassword,
            dbname=settings.pgdatabase,
            autocommit=True,
        )
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        # register pgvector adapters with this connection
        register_vector(self.conn)

        # choose dim without calling embeddings here
        dim = int(os.getenv("EMBED_DIM") or "768")

        with self.conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS docs(
                    id BIGSERIAL PRIMARY KEY,
                    source   TEXT,
                    uri      TEXT,
                    meta     JSONB,
                    content  TEXT,
                    embedding VECTOR({dim})
                );
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS docs_uri_idx ON docs (uri);")
            cur.execute("""
                CREATE INDEX IF NOT EXISTS docs_embedding_idx
                ON docs USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
            """)

    # ---- async API ----

    async def aupsert(self, docs: List[Dict[str, Any]]) -> None:
        texts = [d["content"] for d in docs]
        embs = await embed_texts(texts)  # -> list of lists
        with self.conn.cursor() as cur:
            for d, v in zip(docs, embs):
                # psycopg3 pgvector adapter expects numpy
                vec = np.asarray(v, dtype="float32")
                cur.execute(
                    """
                    INSERT INTO docs (source, uri, meta, content, embedding)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        d.get("source"),
                        d.get("uri"),
                        Json(d.get("meta") or {}),
                        d["content"],
                        vec,
                    ),
                )

    async def aquery(self, text: str, k: int = 5) -> List[Dict[str, Any]]:
        q = (await embed_texts([text]))[0]
        qv = np.asarray(q, dtype="float32")
        with self.conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT source, uri, meta, content,
                       1 - (embedding <=> %s) AS similarity
                FROM docs
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (qv, qv, k),
            )
            return cur.fetchall()
