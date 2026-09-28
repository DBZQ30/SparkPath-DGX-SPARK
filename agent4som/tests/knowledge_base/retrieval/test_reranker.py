"""Tests for Reranker interface, BGEReranker, and Qwen3Reranker."""

from unittest.mock import MagicMock, patch

import pytest

from knowledge_base.models.schemas import RawIndexNode
from knowledge_base.retrieval.reranker import Reranker
from knowledge_base.retrieval.bge_reranker import BGEReranker
from knowledge_base.retrieval.qwen3_reranker import Qwen3Reranker


@pytest.fixture(autouse=True)
def _reranker_env(monkeypatch):
    """离线可跑：无参构造走环境变量时提供测试地址（HTTP 均被 mock，不真实请求）。"""
    monkeypatch.setenv("QWEN3_RERANKER_URL", "http://reranker-test:8002/v1/rerank")
    monkeypatch.setenv("BGE_RERANKER_URL", "http://reranker-test:8002/v1/rerank")


def sample_node(content: str, node_id: str = "n1") -> RawIndexNode:
    return RawIndexNode(
        node_id=node_id,
        source_tier="user",
        source_file="f.txt",
        source_path="/f.txt",
        content=content,
        anchor_text=content[:50],
        anchor_locator="L1",
        source_hash="h",
        parser_version="v1",
    )


def test_reranker_is_abstract():
    """Reranker cannot be instantiated directly."""
    with pytest.raises(TypeError):
        Reranker()  # type: ignore[abstract]


def test_bge_reranker_returns_top_k():
    """BGEReranker returns top-k ranked results."""
    nodes = [sample_node(f"doc {i}", f"n{i}") for i in range(5)]
    mock_response = {
        "results": [
            {"index": 3, "relevance_score": 0.95},
            {"index": 1, "relevance_score": 0.80},
            {"index": 0, "relevance_score": 0.70},
        ]
    }
    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = mock_response

        reranker = BGEReranker(base_url="http://test:8004/v1/rerank", top_k=3)
        result = reranker.rerank("test query", nodes)

    assert len(result) == 3
    assert result[0].node_id == "n3"
    assert result[1].node_id == "n1"
    assert result[2].node_id == "n0"


def test_bge_reranker_empty_nodes():
    """Empty node list returns empty result."""
    reranker = BGEReranker()
    result = reranker.rerank("test query", [])
    assert result == []


def test_bge_reranker_fallback_on_error():
    """On API error, returns unranked top-k results."""
    nodes = [sample_node(f"doc {i}", f"n{i}") for i in range(5)]
    with patch("requests.post") as mock_post:
        mock_post.side_effect = Exception("connection failed")
        reranker = BGEReranker(base_url="http://test:8004/v1/rerank", top_k=3)
        result = reranker.rerank("test query", nodes)

    assert len(result) == 3


# ── Qwen3Reranker tests ──────────────────────────────────────────────

class TestQwen3Reranker:

    def test_empty_nodes_returns_empty(self):
        reranker = Qwen3Reranker()
        assert reranker.rerank("query", []) == []

    @patch("knowledge_base.retrieval.qwen3_reranker.requests.post")
    def test_rerank_orders_by_relevance(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "results": [
                {"index": 2, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.80},
                {"index": 1, "relevance_score": 0.30},
            ]
        }
        mock_post.return_value = mock_resp
        nodes = [sample_node(f"c{i}", f"n{i}") for i in range(3)]
        reranker = Qwen3Reranker(api_key="test")
        result = reranker.rerank("q", nodes, top_k=3)
        assert result[0].node_id == "n2"

    @patch("knowledge_base.retrieval.qwen3_reranker.requests.post")
    def test_rerank_failure_falls_back(self, mock_post):
        mock_post.side_effect = Exception("fail")
        nodes = [sample_node("a", "n1"), sample_node("b", "n2")]
        reranker = Qwen3Reranker(api_key="test", top_k=2)
        result = reranker.rerank("q", nodes)
        assert len(result) == 2
