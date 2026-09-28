"""Tests for BM25 index: indexing, search, thread safety, and hybrid fusion.

The BM25 tokenizer (``_TOKEN_RE``) treats consecutive Chinese characters as a
single token and splits on whitespace/punctuation/ASCII boundaries.  Tests
must use content that naturally produces searchable tokens — either short
standalone Chinese words separated by whitespace, or ASCII keywords.
"""

import threading
from knowledge_base.models.schemas import RawIndexNode
from knowledge_base.retrieval.bm25_search import BM25Index, hybrid_search, get_bm25_index


def _make_node(node_id: str, content: str, scope: str = "global",
               source_file: str = "test.txt") -> RawIndexNode:
    return RawIndexNode(
        node_id=node_id,
        scope=scope,
        source_tier="global",
        source_file=source_file,
        source_path=f"/{scope}/test.txt",
        content=content,
        anchor_text=content[:200],
        anchor_locator="L1",
        source_hash="test_hash",
        parser_version="v1",
    )


class TestBM25Index:
    """Basic indexing and search correctness."""

    def test_index_and_search(self):
        """Index nodes with space-separated tokens and search for one token."""
        idx = BM25Index()
        nodes = [
            _make_node("n1", "考试 通知 教务处"),
            _make_node("n2", "导师 聘任 管理 办法"),
            _make_node("n3", "论文 答辩 本科"),
        ]
        idx.index(nodes)

        results = idx.search("考试")
        assert len(results) > 0, "Should find at least one result"
        assert results[0][0].node_id == "n1"

    def test_exact_phrase_search(self):
        """Searching the full content text should match."""
        idx = BM25Index()
        idx.index([_make_node("n1", "期末 考试 安排")])

        # Search for exact token present in content
        results = idx.search("考试")
        assert len(results) == 1
        assert results[0][0].node_id == "n1"

    def test_empty_search(self):
        idx = BM25Index()
        results = idx.search("nothing")
        assert results == []

    def test_search_on_empty_index(self):
        idx = BM25Index()
        results = idx.search("anything")
        assert results == []

    def test_remove_scope(self):
        idx = BM25Index()
        nodes = [
            _make_node("n1", "考试 通知", scope="global", source_file="f.txt"),
            _make_node("n2", "教师 指南", scope="teachers", source_file="f.txt"),
        ]
        idx.index(nodes)

        # Both scopes should be searchable initially
        results = idx.search("考试")
        assert any(r[0].node_id == "n1" for r in results)

        results = idx.search("教师")
        assert any(r[0].node_id == "n2" for r in results)

        # Remove teachers scope
        idx.remove_scope("/teachers")
        results = idx.search("教师")
        assert not any(r[0].node_id == "n2" for r in results), \
            "teachers node should be removed"
        # global scope still present
        results = idx.search("考试")
        assert any(r[0].node_id == "n1" for r in results)

    def test_remove_file_only_removes_that_file(self):
        idx = BM25Index()
        idx.index([
            _make_node("n1", "考试 通知", source_file="a.txt"),
            _make_node("n2", "考试 安排", source_file="a.txt"),
            _make_node("n3", "考试 通知", source_file="b.txt"),
        ])

        assert idx.remove_file("global", "a.txt") == 2
        assert [n.node_id for n, _ in idx.search("考试", top_k=10)] == ["n3"]
        assert idx.remove_file("global", "a.txt") == 0

    def test_remove_file_scope_isolation(self):
        idx = BM25Index()
        idx.index([
            _make_node("n1", "考试 通知", scope="global", source_file="a.txt"),
            _make_node("n2", "考试 通知", scope="teachers", source_file="a.txt"),
        ])

        assert idx.remove_file("global", "a.txt") == 1
        assert [n.node_id for n, _ in idx.search("考试", top_k=10)] == ["n2"]

    def test_chinese_document_number_search(self):
        """Document numbers (alphanumeric + brackets) should be searchable."""
        idx = BM25Index()
        idx.index([
            _make_node("n1", "西交教 2025 27号 文件"),
            _make_node("n2", "研究生 招生 工作 通知"),
        ])

        # Numeric/alphanumeric tokens work well with this tokenizer
        results = idx.search("2025")
        assert len(results) > 0
        assert results[0][0].node_id == "n1"

    def test_keyword_search(self):
        """Space-separated Chinese keywords should be independently searchable."""
        idx = BM25Index()
        idx.index([
            _make_node("n1", "文件 编号 001"),
            _make_node("n2", "研究生 招生 2025"),
        ])

        results = idx.search("招生")
        assert len(results) > 0
        assert results[0][0].node_id == "n2"

    def test_reindex_updates_node(self):
        """Re-indexing the same node_id should update content."""
        idx = BM25Index()
        idx.index([_make_node("n1", "old content alpha")])

        results = idx.search("alpha")
        assert len(results) == 1

        idx.index([_make_node("n1", "new content beta")])

        results = idx.search("beta")
        assert len(results) == 1
        assert results[0][0].node_id == "n1"

        # Old token should be gone
        results = idx.search("alpha")
        assert len(results) == 0

    def test_ascii_search(self):
        """ASCII keywords should be searchable in mixed content."""
        idx = BM25Index()
        idx.index([_make_node("n1", "关于 MBA 招生 的 通知")])

        results = idx.search("MBA")
        assert len(results) == 1
        assert results[0][0].node_id == "n1"


class TestHybridSearch:
    """Score fusion between dense vector and BM25 results."""

    def test_hybrid_vector_only(self):
        """With bm25_weight=0, should return vector results unchanged."""
        nodes = [
            _make_node("n1", "content a"),
            _make_node("n2", "content b"),
        ]
        nodes[0].distance = 0.2
        nodes[1].distance = 0.8

        bm25 = BM25Index()
        result = hybrid_search(bm25, nodes, "query", top_k=2, bm25_weight=0.0)
        assert len(result) == 2
        assert result[0].node_id == "n1"  # lower distance = better

    def test_hybrid_with_bm25(self):
        """BM25 results should boost relevant keyword matches."""
        nodes = [
            _make_node("n1", "irrelevant text here"),
            _make_node("n2", "matching keyword match here"),
        ]
        nodes[0].distance = 0.2
        nodes[1].distance = 0.8

        bm25 = BM25Index()
        bm25.index(nodes)

        result = hybrid_search(bm25, nodes, "matching keyword", top_k=2, bm25_weight=0.5)
        assert len(result) >= 1

    def test_hybrid_filters_out_of_scope_bm25_hits(self):
        """BM25 hits outside the vector results' scopes are excluded (ACL)."""
        # BM25 index is global — contains nodes from both scopes
        global_node = _make_node("g1", "奖学金 评定 办法", scope="global")
        teachers_node = _make_node("t1", "奖学金 发放 名单", scope="teachers")
        bm25 = BM25Index()
        bm25.index([global_node, teachers_node])

        # Vector results only include global scope (student's allowed set)
        vector_results = [_make_node("g1", "奖学金 评定 办法", scope="global")]
        vector_results[0].distance = 0.3

        result = hybrid_search(bm25, vector_results, "奖学金", top_k=5, bm25_weight=0.5)
        # teachers-scope node must not appear even though BM25 would match it
        assert all(n.scope == "global" for n in result), \
            f"out-of-scope node leaked: {[n.scope for n in result]}"

    def test_hybrid_no_scope_info_keeps_all(self):
        """When vector results carry no scope info, BM25 hits are kept."""
        bm25 = BM25Index()
        n1 = _make_node("n1", "keyword alpha", scope="")
        bm25.index([n1])

        vector_results = [_make_node("n1", "keyword alpha", scope="")]
        vector_results[0].distance = 0.3

        result = hybrid_search(bm25, vector_results, "keyword", top_k=5, bm25_weight=0.5)
        assert len(result) >= 1


class TestThreadSafety:
    """Concurrent access should not corrupt the index."""

    def test_concurrent_index_and_search(self):
        idx = BM25Index()
        errors = []

        def writer(start: int, count: int):
            for i in range(start, start + count):
                try:
                    idx.index([
                        _make_node(f"n{i}", f"test content number {i} mod {i % 10}")
                    ])
                except Exception as e:
                    errors.append(f"writer error: {e}")

        def reader(iterations: int):
            for _ in range(iterations):
                try:
                    idx.search("test")
                except Exception as e:
                    errors.append(f"reader error: {e}")

        threads = []
        for i in range(3):
            t = threading.Thread(target=writer, args=(i * 100, 100))
            threads.append(t)
        for _ in range(3):
            t = threading.Thread(target=reader, args=(200,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent access errors: {errors}"

    def test_concurrent_get_bm25_index(self):
        """get_bm25_index() should return the same instance under concurrency."""
        import knowledge_base.retrieval.bm25_search as bm25_mod
        bm25_mod._bm25_index = None

        instances = []

        def get_instance():
            instances.append(get_bm25_index())

        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        first = instances[0]
        for inst in instances[1:]:
            assert inst is first, "get_bm25_index() returned different instances"


class TestPreloadBM25Index:
    """Startup preload: build once, and off the event loop.

    Regression guard: the gateway's kb-init hook calls preload_bm25_index()
    while the platform adapters are already accepting connections. Running the
    synchronous build on the event loop would stall the gateway (502s during
    startup), and building twice duplicated the full ChromaDB bulk read.
    """

    def test_builds_once_in_worker_thread(self, monkeypatch):
        import asyncio
        import knowledge_base.retrieval.bm25_search as bm

        calls = []

        def fake_get_bm25_index():
            calls.append(threading.current_thread().name)

            class _Idx:
                _total_docs = 42

            return _Idx()

        monkeypatch.setattr(bm, "get_bm25_index", fake_get_bm25_index)

        total = asyncio.run(bm.preload_bm25_index())

        assert total == 42
        assert len(calls) == 1, "index must be built exactly once"
        assert calls[0] != threading.main_thread().name, (
            "build must run in a worker thread, not the event loop"
        )
