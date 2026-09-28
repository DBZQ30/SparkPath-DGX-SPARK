from __future__ import annotations

import logging
import os

import requests

from knowledge_base.models.schemas import RawIndexNode
from knowledge_base.retrieval.reranker import Reranker

logger = logging.getLogger(__name__)


class BGEReranker(Reranker):
    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        timeout: int = 60,
        top_k: int = 3,
    ):
        base_url = base_url or os.environ.get("BGE_RERANKER_URL", "")
        if not base_url:
            raise ValueError(
                "BGEReranker base_url is required. "
                "Set BGE_RERANKER_URL in .env or pass base_url explicitly."
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._top_k = top_k

    def rerank(self, query: str, nodes: list[RawIndexNode], top_k: int | None = None) -> list[RawIndexNode]:
        if not nodes:
            return []
        k = top_k or self._top_k

        # Adaptive retry: try full batch, on 5xx halve and retry.
        # Avoids hardcoding token limits — adapts to any model/deployment.
        batch = nodes
        while True:
            documents = [n.content for n in batch]
            try:
                headers = {"Content-Type": "application/json"}
                if self._api_key:
                    headers["Authorization"] = f"Bearer {self._api_key}"
                resp = requests.post(
                    self._base_url,
                    headers=headers,
                    json={"query": query, "documents": documents, "top_k": k},
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                ranked = sorted(
                    data.get("results", []),
                    key=lambda x: x.get("relevance_score", 0),
                    reverse=True,
                )
                return [batch[r["index"]] for r in ranked[:k]]
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status == 500 and len(batch) > 1:
                    batch = batch[:max(1, len(batch) // 2)]
                    logger.debug("Reranker 500, retrying with %d docs (status=%s)",
                                 len(batch), status)
                    continue
                logger.warning(
                    "Reranker request failed (%s), returning unranked results. "
                    "ndocs=%d total_chars=%d",
                    exc, len(documents), sum(len(d) for d in documents),
                )
                return batch[:k]
            except Exception as exc:
                logger.warning(
                    "Reranker request failed (%s), returning unranked results. "
                    "ndocs=%d total_chars=%d",
                    exc, len(documents), sum(len(d) for d in documents),
                )
                return batch[:k]
