from __future__ import annotations

import logging
import os

import requests

from knowledge_base.models.schemas import RawIndexNode
from knowledge_base.retrieval.reranker import Reranker

logger = logging.getLogger(__name__)


class Qwen3Reranker(Reranker):
    """Qwen3 Reranker via /v1/rerank endpoint (standard Rerank API format)."""

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        timeout: int = 60,
        top_k: int = 3,
    ):
        # Prefer QWEN3_RERANKER_URL, fall back to BGE_RERANKER_URL for
        # backward compatibility (P2-12 fix).
        base_url = (
            base_url
            or os.environ.get("QWEN3_RERANKER_URL", "")
            or os.environ.get("BGE_RERANKER_URL", "")
        )
        if not base_url:
            raise ValueError(
                "Qwen3Reranker base_url is required. "
                "Set QWEN3_RERANKER_URL (or BGE_RERANKER_URL) in .env "
                "or pass base_url explicitly."
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._top_k = top_k

    def rerank(self, query: str, nodes: list[RawIndexNode], top_k: int | None = None) -> list[RawIndexNode]:
        if not nodes:
            return []
        k = top_k or self._top_k

        documents = [n.content for n in nodes]
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            resp = requests.post(
                self._base_url,
                headers=headers,
                json={
                    "model": "qwen3-reranker",
                    "query": query,
                    "documents": documents,
                    "top_n": k,
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            ranked = sorted(
                data.get("results", []),
                key=lambda x: x.get("relevance_score", 0),
                reverse=True,
            )
            return [nodes[r["index"]] for r in ranked[:k]]
        except Exception as exc:
            logger.warning(
                "Qwen3Reranker failed (%s), returning unranked results. ndocs=%d",
                exc, len(nodes),
            )
            return nodes[:k]
