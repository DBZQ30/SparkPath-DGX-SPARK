"""Tests for ConflictResolver adapter (normalize_for_conflict_resolver).

GI-1: Bridges RawIndexNode / BaseFact → the dict format ConflictResolver expects.
"""

from knowledge_base.models.schemas import BaseFact, RawIndexNode, FactStatus
from knowledge_base.retrieval.normalize_for_conflict_resolver import normalize_for_conflict_resolver
from knowledge_base.retrieval.conflict_resolver import ConflictResolver


def sample_node(node_id: str = "n1", content: str = "test", source_tier: str = "user") -> RawIndexNode:
    return RawIndexNode(
        node_id=node_id,
        source_tier=source_tier,
        source_file="f.txt",
        source_path="/f.txt",
        content=content,
        anchor_text=content[:50],
        anchor_locator="L1",
        source_hash="h",
        parser_version="v1",
    )


def sample_fact(fact_id: str = "f1", fact_key: str = "key1", payload: dict | None = None) -> BaseFact:
    return BaseFact(
        fact_id=fact_id,
        fact_type="test",
        assistant_id="mba",
        program="mba",
        cohort_year="2024",
        payload=payload or {"effective_from": 1},
        payload_schema_version="v1",
        fact_key=fact_key,
        source_node_ids=["n1"],
        status=FactStatus.APPROVED,
    )


def test_normalize_raw_index_node():
    """RawIndexNode is normalized with tier from source_tier."""
    node = sample_node(source_tier="user")
    result = normalize_for_conflict_resolver([node])
    assert len(result) == 1
    assert result[0]["tier"] == "user"
    assert result[0]["fact"].fact_id == "n1"


def test_normalize_base_fact():
    """BaseFact is normalized with tier='user'."""
    fact = sample_fact()
    result = normalize_for_conflict_resolver([fact])
    assert len(result) == 1
    assert result[0]["tier"] == "user"
    assert result[0]["fact"].fact_id == "f1"


def test_normalize_mixed_list():
    """Mixed RawIndexNode and BaseFact lists are all normalized."""
    node = sample_node(source_tier="global")
    fact = sample_fact()
    result = normalize_for_conflict_resolver([node, fact])
    assert len(result) == 2
    assert result[0]["tier"] == "global"
    assert result[1]["tier"] == "user"


def test_conflict_resolver_accepts_normalized_input():
    """Normalized output can be passed to ConflictResolver.resolve()."""
    node = sample_node(source_tier="system", content="system data")
    fact = sample_fact(payload={"effective_from": 2})
    normalized = normalize_for_conflict_resolver([node, fact])
    resolver = ConflictResolver()
    result = resolver.resolve(normalized)
    assert len(result) > 0


def test_resolver_system_preferred_over_user():
    """System-tier items override user-tier items for the same fact_key."""
    node = sample_node(node_id="sys", source_tier="system", content="system answer")
    fact = sample_fact(fact_id="usr", fact_key="f.txt", payload={"effective_from": 1})
    normalized = normalize_for_conflict_resolver([node, fact])
    resolver = ConflictResolver()
    result = resolver.resolve(normalized)
    assert len(result) == 1
    assert result[0]["fact"].fact_id == "sys"
