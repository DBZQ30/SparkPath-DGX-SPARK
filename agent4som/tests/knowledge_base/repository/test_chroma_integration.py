"""Integration smoke tests for ChromaRepository with a real PersistentClient.

Phase 1a.0: Store → search round-trip with BuiltinEmbeddingFunction (local ONNX).
"""

import os

import chromadb
import pytest
from knowledge_base.repository.chroma_repository import ChromaRepository
from knowledge_base.models.schemas import RawIndexNode

# lazy 自初始化路径会走 bootstrap 读完整 .env（QWEN_API_KEY / CHROMA_HOST 等），
# 只有部署机（或 .env 齐备）才具备条件；其余环境整类用例保留、此用例跳过。
_ENV_READY = bool(os.environ.get("QWEN_API_KEY") and os.environ.get("CHROMA_HOST"))


@pytest.fixture
def repo(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    return ChromaRepository(client)


def test_store_and_search_roundtrip(repo):
    """Store a node, search for it, verify it comes back."""
    node = RawIndexNode(
        node_id="smoke-001",
        source_tier="user",
        source_file="test.txt",
        source_path="/tmp/test.txt",
        content="MBA培养方案要求总学分为45学分",
        anchor_text="MBA培养方案要求总学分为45学分",
        anchor_locator="L1-L5",
        source_hash="abc123",
        parser_version="chunk-v1",
    )
    repo.store_nodes("global", [node])

    results = repo.search_nodes(scopes=["global"], query_embedding=[0.0] * 384, top_k=5)

    assert len(results) > 0
    assert results[0].node_id == "smoke-001"
    assert "45学分" in results[0].content


def test_store_and_search_scope_isolation(repo):
    """Nodes stored under one scope are not visible when searching another."""
    node_a = RawIndexNode(
        node_id="scope-a",
        source_tier="user",
        source_file="a.txt",
        source_path="/tmp/a.txt",
        content="scope A content",
        anchor_text="scope A",
        anchor_locator="L1",
        source_hash="def",
        parser_version="chunk-v1",
    )
    node_b = RawIndexNode(
        node_id="scope-b",
        source_tier="user",
        source_file="b.txt",
        source_path="/tmp/b.txt",
        content="scope B content",
        anchor_text="scope B",
        anchor_locator="L1",
        source_hash="ghi",
        parser_version="chunk-v1",
    )
    repo.store_nodes("global", [node_a])
    repo.store_nodes("users/test", [node_b])

    results = repo.search_nodes(scopes=["global"], query_embedding=[0.0] * 384, top_k=5)
    assert any("scope A" in n.content for n in results)
    assert not any("scope B" in n.content for n in results)


def test_search_across_multiple_scopes(repo):
    """Searching multiple scopes returns nodes from all of them."""
    node = RawIndexNode(
        node_id="multi-001",
        source_tier="assistant",
        source_file="multi.txt",
        source_path="/tmp/multi.txt",
        content="shared content",
        anchor_text="shared",
        anchor_locator="L1",
        source_hash="jkl",
        parser_version="chunk-v1",
    )
    repo.store_nodes("users/mba", [node])

    results = repo.search_nodes(
        scopes=["global", "users/mba"],
        query_embedding=[0.0] * 384,
        top_k=5,
    )
    assert len(results) > 0


class TestSingleton:
    """ChromaRepository singleton (Phase 1a.5 / VC-2 / VC-7)."""

    def teardown_method(self):
        ChromaRepository._instance = None

    def test_init_instance_creates_singleton(self, tmp_path):
        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        inst = ChromaRepository.init_instance(client)
        assert ChromaRepository.instance() is inst
        assert ChromaRepository.is_ready() is True

    def test_init_instance_raises_on_double_init(self, tmp_path):
        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        ChromaRepository.init_instance(client)
        with pytest.raises(RuntimeError, match="already initialized"):
            ChromaRepository.init_instance(client)

    @pytest.mark.integration
    @pytest.mark.skipif(not _ENV_READY, reason="lazy 自初始化需要完整 .env（QWEN_API_KEY/CHROMA_HOST），仅在部署机执行")
    def test_instance_auto_init_before_init(self):
        """instance() auto-initialises when not yet initialised (lazy init).

        Previously raised RuntimeError; since 2026-07-23 the singleton
        self-initialises on first use so RAG tools never depend on the
        kb_init hook having fired.

        打 integration 标记：lazy init 读取 .env 的 CHROMA_HOST/PORT，
        需要本机 chroma-server 可达；离线开发机用 -m "not integration" 跳过。
        """
        assert ChromaRepository.instance() is not None
        assert ChromaRepository.is_ready() is True

    def test_is_ready_false_before_init(self):
        assert ChromaRepository.is_ready() is False
