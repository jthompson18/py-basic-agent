# app/agent/memory/sqlite_store.py
import os
import sqlite3
import json
import numpy as np
from typing import List, Dict, Any
from .base import VectorMemory
from ..embeddings import embed_texts


class SqliteVectorMemory(VectorMemory):
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS docs(
            id INTEGER PRIMARY KEY,
            source TEXT, uri TEXT, meta TEXT, content TEXT, vec BLOB
        )""")
        self.conn.commit()

    async def aupsert(self, docs: List[Dict[str, Any]]) -> None:
        texts = [d["content"] for d in docs]
        embs = await embed_texts(texts)
        with self.conn:
            for d, v in zip(docs, embs):
                self.conn.execute(
                    "INSERT INTO docs(source,uri,meta,content,vec) VALUES(?,?,?,?,?)",
                    (d.get("source"), d.get("uri"), json.dumps(d.get("meta", {})),
                     d["content"], np.asarray(v, dtype="float32").tobytes()),
                )

    async def aquery(self, text: str, k: int = 5) -> List[Dict[str, Any]]:
        import numpy as np
        q = np.asarray((await embed_texts([text]))[0], dtype="float32")
        rows = self.conn.execute(
            "SELECT rowid, source, uri, meta, content, vec FROM docs").fetchall()
        sims = []
        for row in rows:
            v = np.frombuffer(row[5], dtype="float32")
            sim = float(np.dot(q, v) / (np.linalg.norm(q)
                        * np.linalg.norm(v) + 1e-9))
            sims.append((sim, row))
        sims.sort(reverse=True, key=lambda x: x[0])
        return [{"similarity": sim, "source": r[1], "uri": r[2], "meta": json.loads(r[3]), "content": r[4]} for sim, r in sims[:k]]
