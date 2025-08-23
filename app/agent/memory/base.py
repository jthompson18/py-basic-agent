# app/agent/memory/base.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any


class VectorMemory(ABC):
    @abstractmethod
    async def aupsert(self, docs: List[Dict[str, Any]]) -> None: ...
    @abstractmethod
    async def aquery(self, text: str, k: int = 5) -> List[Dict[str, Any]]: ...
