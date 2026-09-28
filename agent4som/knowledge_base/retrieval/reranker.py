from __future__ import annotations
from abc import ABC, abstractmethod

from knowledge_base.models.schemas import RawIndexNode


class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, nodes: list[RawIndexNode], top_k: int = 3) -> list[RawIndexNode]:
        ...
