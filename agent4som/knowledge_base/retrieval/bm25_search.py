"""BM25 keyword search — complementary to vector similarity search.

Builds a simple in-memory inverted index from the knowledge base nodes
for exact keyword matching.  Especially useful for queries containing
document numbers (e.g. ``西交教〔2025〕27号``), course codes, or proper
names that embedding models may not capture well.
"""

from __future__ import annotations
from typing import Dict

import logging
import math
import os
import re
import threading
from collections import defaultdict

from knowledge_base.models.schemas import RawIndexNode

logger = logging.getLogger(__name__)

# Chinese-aware tokenisation: split on whitespace, punctuation, and
# keep Chinese character runs as tokens.
_TOKEN_RE = re.compile(r"[一-鿿]+|[a-zA-Z0-9]+|[^\s]")


# Punctuation characters to filter from BM25 tokens (common CJK + ASCII)
_PUNCT_RE = re.compile(r"^[【】（）《》、，。！？；："r"''…·[\]{}()<>.,!?;:]+$")


def _tokenise(text: str) -> list[str]:
    """Tokenise *text* into a list of terms, filtering pure punctuation."""
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)
              if len(t) > 1 or (t.isalpha() and t.isascii())]
    return [t for t in tokens if not _PUNCT_RE.match(t)]


class BM25Index:
    """In-memory BM25 inverted index over RawIndexNode content."""

    # BM25 hyperparameters (standard defaults)
    _k1: float = 1.2
    _b: float = 0.75

    def __init__(self):
        self._lock = threading.Lock()
        self._nodes: dict[str, RawIndexNode] = {}
        # term → {node_id → term_frequency}
        self._inverted: dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._doc_lengths: dict[str, int] = {}
        self._avgdl: float = 0.0
        self._total_docs: int = 0
        # term → document frequency
        self._df: dict[str, int] = defaultdict(int)

    def index(self, nodes: list[RawIndexNode]) -> None:
        """Add or update *nodes* in the index."""
        with self._lock:
            for node in nodes:
                self._add_node(node)

    def _add_node(self, node: RawIndexNode) -> None:
        tokens = _tokenise(node.content)
        nid = node.node_id

        # Remove old entry if re-indexing
        if nid in self._nodes:
            self._remove_node(nid)

        self._nodes[nid] = node
        self._doc_lengths[nid] = len(tokens)
        self._total_docs = len(self._nodes)
        self._avgdl = sum(self._doc_lengths.values()) / max(self._total_docs, 1)

        seen: set[str] = set()
        for token in tokens:
            self._inverted[token][nid] += 1
            if token not in seen:
                self._df[token] += 1
                seen.add(token)

    def _remove_node(self, nid: str) -> None:
        node = self._nodes.get(nid)
        if node is None:
            return
        tokens = _tokenise(node.content)
        for token in set(tokens):
            self._inverted[token].pop(nid, None)
            if not self._inverted[token]:
                del self._inverted[token]
                self._df.pop(token, None)
        self._doc_lengths.pop(nid, None)
        self._nodes.pop(nid, None)
        self._total_docs = len(self._nodes)
        self._avgdl = sum(self._doc_lengths.values()) / max(self._total_docs, 1)

    def search(self, query: str, top_k: int = 5) -> list[tuple[RawIndexNode, float]]:
        """Return top-k nodes ranked by BM25 score."""
        with self._lock:
            return self._search_locked(query, top_k)

    def _search_locked(self, query: str, top_k: int = 5) -> list[tuple[RawIndexNode, float]]:
        """Core BM25 search — caller must hold ``self._lock``."""
        tokens = _tokenise(query)
        if not tokens or self._total_docs == 0:
            return []

        scores: dict[str, float] = defaultdict(float)

        for token in tokens:
            df = self._df.get(token, 0)
            if df == 0:
                continue
            idf = math.log((self._total_docs - df + 0.5) / (df + 0.5) + 1.0)
            for nid, tf in self._inverted[token].items():
                doc_len = self._doc_lengths.get(nid, 1)
                numerator = tf * (self._k1 + 1)
                denominator = tf + self._k1 * (1 - self._b + self._b * doc_len / self._avgdl)
                scores[nid] += idf * numerator / denominator

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [(self._nodes[nid], score) for nid, score in ranked if nid in self._nodes]

    def remove_scope(self, scope_pattern: str) -> None:
        """Remove all nodes whose source_path starts with *scope_pattern*."""
        with self._lock:
            to_remove = [
                nid for nid, node in self._nodes.items()
                if node.source_path.startswith(scope_pattern)
            ]
            for nid in to_remove:
                self._remove_node(nid)

    def remove_file(self, scope: str, source_file: str) -> int:
        """Remove all nodes of *source_file* in *scope*. Returns the count removed."""
        with self._lock:
            to_remove = [
                nid for nid, node in self._nodes.items()
                if node.scope == scope and node.source_file == source_file
            ]
            for nid in to_remove:
                self._remove_node(nid)
            return len(to_remove)


# ── Global singleton ────────────────────────────────────────────────

_bm25_index: BM25Index | None = None
_bm25_lock = threading.Lock()
_bm25_populated: bool = False


def get_bm25_index() -> BM25Index:
    global _bm25_index, _bm25_populated
    if _bm25_index is None:
        with _bm25_lock:
            if _bm25_index is None:
                _bm25_index = BM25Index()
    # Lazy-populate from ChromaDB on first use (once per process lifetime).
    if not _bm25_populated:
        with _bm25_lock:
            if not _bm25_populated:
                _bm25_populated = True
                try:
                    _populate_from_chroma(_bm25_index)
                except Exception:
                    logger.warning("BM25 lazy population failed, keyword search disabled", exc_info=True)
    return _bm25_index


async def preload_bm25_index() -> int:
    """Build the BM25 index off the event loop (used for startup preload).

    ``get_bm25_index()`` is synchronous and can take tens of seconds on a cold
    ChromaDB (it bulk-reads every node). Running it via ``asyncio.to_thread``
    keeps the gateway's event loop responsive — the platform adapters are
    already accepting connections by the time the ``gateway:startup`` hook
    fires, so blocking here surfaces as connection backlogs / 502s.

    Returns the number of indexed documents, or -1 if unknown.
    """
    import asyncio

    bm25 = await asyncio.to_thread(get_bm25_index)
    return getattr(bm25, "_total_docs", -1)


def _populate_from_chroma(bm25: BM25Index) -> None:
    """Populate *bm25* with all nodes from ChromaDB (called once at startup)."""
    import os as _os
    from knowledge_base.repository.chroma_repository import get_chroma_client
    from knowledge_base.models.schemas import RawIndexNode

    client = get_chroma_client(_os.environ.get("CHROMA_DB_PATH", "data/chroma"))
    col = client.get_collection("raw_nodes")
    bm25_limit = int(_os.environ.get("BM25_INDEX_LIMIT", "10000"))
    result = col.get(include=["documents", "metadatas"], limit=bm25_limit)
    ids_list = result.get("ids", [])
    docs_list = result.get("documents", []) or []
    metas_list = result.get("metadatas", []) or []

    if not ids_list:
        return

    if len(ids_list) >= bm25_limit:
        logger.warning(
            "BM25 index reached limit of %d nodes — %d nodes beyond this limit "
            "are invisible to keyword search. Set BM25_INDEX_LIMIT env var to increase.",
            bm25_limit, max(0, col.count() - bm25_limit),
        )

    nodes = []
    for i, nid in enumerate(ids_list):
        doc = docs_list[i] if i < len(docs_list) else ""
        meta = metas_list[i] if i < len(metas_list) else {}
        if not meta:
            continue
        nodes.append(RawIndexNode(
            node_id=nid,
            scope=meta.get("scope", ""),
            source_tier=meta.get("source_tier", ""),
            source_file=meta.get("source_file", ""),
            source_path=meta.get("source_path", ""),
            content=doc,
            anchor_text=doc[:200] if doc else "",
            anchor_locator=meta.get("anchor_locator", ""),
            source_hash=meta.get("source_hash", ""),
            parser_version=meta.get("parser_version", ""),
            prev_node_id=meta.get("prev_node_id"),
            next_node_id=meta.get("next_node_id"),
        ))

    bm25.index(nodes)
    logger.info("BM25 index lazy-loaded with %d nodes", len(nodes))


def _dat_alpha(query: str, dense_top1: str, bm25_top1: str) -> float | None:
    """Dynamic Alpha Tuning (Hsu & Tzeng, 2025): use LLM to score each
    retriever's top-1 and compute per-query α = dense/(dense+bm25).

    Returns alpha in [0.1, 0.9], or None if LLM call fails.
    """
    import os
    import requests
    api_key = (
        os.environ.get("STEP_BACK_API_KEY", "")
        or os.environ.get("QWEN_API_KEY", "")
        or os.environ.get("DEEPSEEK_API_KEY", "")
    )
    url = os.environ.get("STEP_BACK_MODEL_URL", "")
    model = os.environ.get("STEP_BACK_MODEL_NAME", "")
    if not url or not model:
        # 未显式配置 step-back 模型时直接关闭 DAT；不再回落到写死的云端模型，
        # 否则会把请求打到不存在的模型上（本地 vLLM 会 404）。
        logger.debug("DAT skipped: STEP_BACK_MODEL_URL/STEP_BACK_MODEL_NAME not configured")
        return None
    prompt = (
        "你是一个检索质量评估助手。给定用户问题和两段检索结果，"
        "请分别评估每段结果与问题的相关程度。\n\n"
        f"用户问题: {query[:200]}\n\n"
        f"[结果A - 语义搜索]\n{dense_top1[:300]}\n\n"
        f"[结果B - 关键词搜索]\n{bm25_top1[:300]}\n\n"
        "请只输出两个0-5的数字(格式: A=数字 B=数字)，不要任何解释。"
    )
    try:
        resp = requests.post(
            f"{url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": [
                {"role": "user", "content": prompt}
            ], "max_tokens": 20, "temperature": 0},
            timeout=10,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        import re
        nums = re.findall(r'[0-5]\.?\d*', text)
        if len(nums) >= 2:
            a, b = float(nums[0]), float(nums[1])
            alpha = a / max(a + b, 0.01)
            return max(0.1, min(0.9, alpha))
    except Exception:
        logger.warning("DAT LLM call failed, falling back to fixed BM25 weight", exc_info=True)
    return None


def _bm25_within_allowed_scopes(
    bm25_results: list[tuple[RawIndexNode, float]],
    vector_results: list[RawIndexNode],
) -> list[tuple[RawIndexNode, float]]:
    """ACL defense: 把 BM25 命中过滤到向量结果（已过 ACL）出现的 scope 集合内。

    BM25 索引是全局加载（无 scope 过滤），命中可能落在调用方无权的 scope；
    不过滤则越权节点会占用 top-k 名额并进入 reranker。
    """
    allowed_scopes = {n.scope for n in vector_results if n.scope}
    if not allowed_scopes:
        return bm25_results
    return [(n, s) for n, s in bm25_results if n.scope in allowed_scopes]


def _resolve_bm25_weight(
    query: str,
    vector_results: list[RawIndexNode],
    bm25_results: list[tuple[RawIndexNode, float]],
    default_weight: float,
) -> float:
    """DAT (Dynamic Alpha Tuning, 2025)：逐查询 LLM 评分 α 取代固定 bm25_weight。

    DAT 关闭或 LLM 调用失败时回落默认权重。
    """
    if os.environ.get("DAT_ENABLED", "false").lower() != "true":
        return default_weight
    if not (vector_results and bm25_results):
        return default_weight
    dense_text = vector_results[0].content
    bm25_text = bm25_results[0][0].content
    dat_alpha = _dat_alpha(query, dense_text[:500], bm25_text[:500])
    if dat_alpha is not None:
        return 1.0 - dat_alpha  # convert α to bm25_weight
    return default_weight


def _fuse_scores(
    vector_results: list[RawIndexNode],
    bm25_results: list[tuple[RawIndexNode, float]],
    bm25_weight: float,
) -> dict[str, float]:
    """Score fusion with real cosine distances and BM25.

    Dense: cosine distance → similarity, clipped to [0,1].
    BM25: normalised by max score. Min-max tested and rejected (0.75 vs 0.77):
      it amplifies noise when BM25 scores are uniformly low for queries
      without keyword matches.
    """
    scores: dict[str, float] = {}
    for node in vector_results:
        dist = getattr(node, 'distance', 0.5) or 0.5
        dense_score = max(0.0, 1.0 - dist)
        scores[node.node_id] = dense_score * (1.0 - bm25_weight)
    if bm25_results:
        max_bm25 = max(s for _, s in bm25_results)
        for node, bm25_score in bm25_results:
            normalised = bm25_score / max(max_bm25, 0.001)
            if node.node_id in scores:
                scores[node.node_id] += normalised * bm25_weight
            else:
                scores[node.node_id] = normalised * bm25_weight
    return scores


def hybrid_search(
    bm25: BM25Index,
    vector_results: list[RawIndexNode],
    query: str,
    top_k: int = 5,
    bm25_weight: float = 0.3,
) -> list[RawIndexNode]:
    """Combine BM25 keyword results with vector similarity results.

    Args:
        bm25: The BM25 index instance.
        vector_results: Top-k results from vector similarity search.
        query: The original user query.
        top_k: Final number of results to return.
        bm25_weight: Weight for BM25 scores (0.0 = vector-only, 1.0 = BM25-only).

    Returns:
        Combined and re-ranked list of nodes.
    """
    if not bm25 or bm25_weight <= 0.0:
        return vector_results[:top_k]

    bm25_results = _bm25_within_allowed_scopes(
        bm25.search(query, top_k=top_k), vector_results,
    )
    bm25_weight = _resolve_bm25_weight(query, vector_results, bm25_results, bm25_weight)
    scores = _fuse_scores(vector_results, bm25_results, bm25_weight)

    id_to_node = {n.node_id: n for n in vector_results}
    for n, _ in bm25_results:
        if n.node_id not in id_to_node:
            id_to_node[n.node_id] = n

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    return [id_to_node[nid] for nid, _ in ranked if nid in id_to_node]
