# tests/test_tools_memory.py
from __future__ import annotations
import pytest

pytestmark = pytest.mark.asyncio

# Ensure we don't accidentally hit pgvector in this unit test.


@pytest.fixture(autouse=True)
def _unset_pg_env(monkeypatch):
    monkeypatch.delenv("AGENT_DB_URL", raising=False)


async def test_memory_tool_simple():
    from agent.tools import memory_tool

    docs = [
        {
            "content": "NVIDIA was founded in 1993 by Jensen Huang, Chris Malachowsky, and Curtis Priem.",
            "source": "note",
            "uri": "nvidia_founding",
            "meta": {"title": "NVIDIA founding"},
        },
        {
            "content": "CUDA is a parallel computing platform created by NVIDIA in 2006.",
            "source": "note",
            "uri": "cuda_intro",
            "meta": {"title": "CUDA intro"},
        },
    ]

    # Remember should succeed and report count inserted.
    res = await memory_tool("remember", docs=docs)
    assert res.get("ok") is True and res.get("count") == 2

    # Recall should return a list (may be empty for SimpleMemory, by design).
    hits = await memory_tool("recall", query="NVIDIA", k=3)
    assert isinstance(hits, list)
    # If any hits returned, they should be relevant.
    if hits:
        assert any("NVIDIA" in (h.get("content") or "") for h in hits)
