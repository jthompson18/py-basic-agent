from __future__ import annotations
import os

from .types import Memory
from .simple_memory import SimpleMemory


def get_memory() -> Memory:
    """
    Factory: returns PgVectorMemory if AGENT_DB_URL is set, otherwise SimpleMemory.
    """
    if os.environ.get("AGENT_DB_URL"):
        from .pg_store import PgVectorMemory
        return PgVectorMemory()
    return SimpleMemory()


__all__ = ["Memory", "SimpleMemory", "get_memory"]
