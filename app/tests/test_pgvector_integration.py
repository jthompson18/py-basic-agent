# tests/test_pgvector_integration.py
import os
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.pg, pytest.mark.asyncio]


@pytest.mark.skipif(not os.environ.get("AGENT_DB_URL"), reason="AGENT_DB_URL not set")
async def test_pgvector_memory_roundtrip():
    from agent.memory.pg_store import PgVectorMemory
    mem = PgVectorMemory()
    docs = [
        {"content": "NVIDIA founded in 1993 by Jensen Huang, Chris Malachowsky, Curtis Priem.",
            "source": "w", "uri": "u1", "meta": {}},
        {"content": "CUDA introduced by NVIDIA in 2006.",
            "source": "w", "uri": "u2", "meta": {}},
    ]
    await mem.aupsert(docs)
    hits = await mem.aquery("Who founded NVIDIA?", k=2)
    assert hits and "Jensen" in (hits[0].get(
        "content", "") + hits[-1].get("content", ""))
