"""Tests for context_stitcher: build_passages, window merging, neighbour expansion."""

from knowledge_base.models.schemas import RawIndexNode
from knowledge_base.retrieval.context_stitcher import (
    build_passages,
    enrich_hits_with_adjacent,
)


def _make_node(node_id: str, content: str, source_file: str = "doc.txt",
               prev_id: str | None = None, next_id: str | None = None) -> RawIndexNode:
    return RawIndexNode(
        node_id=node_id,
        scope="global",
        source_tier="global",
        source_file=source_file,
        source_path="/global/doc.txt",
        content=content,
        anchor_text=content[:200],
        anchor_locator="L1",
        source_hash="h",
        parser_version="v1",
        prev_node_id=prev_id,
        next_node_id=next_id,
    )


class TestBuildPassages:

    def test_empty_hits_returns_empty(self):
        assert build_passages([], []) == []

    def test_single_hit_no_neighbours(self):
        node = _make_node("n1", "content 1")
        result = build_passages([node], [node])
        assert len(result) == 1
        assert result[0].content == "content 1"

    def test_expands_neighbours(self):
        n1 = _make_node("n1", "chunk 1", next_id="n2")
        n2 = _make_node("n2", "chunk 2", prev_id="n1", next_id="n3")
        n3 = _make_node("n3", "chunk 3", prev_id="n2")
        all_nodes = [n1, n2, n3]

        # Hit the middle node
        result = build_passages([n2], all_nodes, window=1)
        assert len(result) == 1
        # Should merge n1 + n2 + n3 into one passage
        assert "chunk 1" in result[0].content
        assert "chunk 2" in result[0].content
        assert "chunk 3" in result[0].content

    def test_merges_overlapping_windows(self):
        n1 = _make_node("n1", "a", next_id="n2")
        n2 = _make_node("n2", "b", prev_id="n1", next_id="n3")
        n3 = _make_node("n3", "c", prev_id="n2")
        all_nodes = [n1, n2, n3]

        # Two overlapping hits in same document → one merged passage
        result = build_passages([n1, n2], all_nodes, window=1)
        assert len(result) == 1
        assert "a" in result[0].content
        assert "b" in result[0].content
        assert "c" in result[0].content

    def test_different_documents_stay_separate(self):
        a1 = _make_node("a1", "docA-1", source_file="A.txt")
        b1 = _make_node("b1", "docB-1", source_file="B.txt")
        all_nodes = [a1, b1]

        result = build_passages([a1, b1], all_nodes)
        assert len(result) == 2


class TestEnrichHitsWithAdjacent:

    def test_adds_prev_and_next(self):
        n1 = _make_node("n1", "before", next_id="n2")
        n2 = _make_node("n2", "target", prev_id="n1", next_id="n3")
        n3 = _make_node("n3", "after", prev_id="n2")
        all_nodes = [n1, n2, n3]

        result = enrich_hits_with_adjacent([n2], all_nodes)
        ids = {n.node_id for n in result}
        assert "n1" in ids
        assert "n2" in ids
        assert "n3" in ids

    def test_no_duplicates(self):
        n1 = _make_node("n1", "x", next_id="n2")
        n2 = _make_node("n2", "y", prev_id="n1")
        all_nodes = [n1, n2]

        result = enrich_hits_with_adjacent([n1, n2], all_nodes)
        ids = [n.node_id for n in result]
        assert ids.count("n1") == 1
        assert ids.count("n2") == 1
