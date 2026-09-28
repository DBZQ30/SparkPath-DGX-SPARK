"""
Tests for the ChromaDB repository implementation.
Uses mocked chromadb.Client to verify logic in isolation.
"""
import pytest
from unittest.mock import MagicMock
from knowledge_base.repository.chroma_repository import ChromaRepository
from knowledge_base.repository.interfaces import KnowledgeBaseRepository
from knowledge_base.models.schemas import RawIndexNode, BaseFact, FactStatus


@pytest.fixture
def mock_chroma_client():
    """Create a mock chromadb client with a mock collection."""
    client = MagicMock()
    mock_collection = MagicMock()
    # get_or_create_collection returns the mock collection
    client.get_or_create_collection.return_value = mock_collection
    return client, mock_collection


@pytest.fixture
def sample_node() -> RawIndexNode:
    return RawIndexNode(
        node_id="node-001",
        source_tier="users/mba",
        source_file="培养计划.xlsx",
        source_path="/data/knowledge/培养计划.xlsx",
        doc_version="v1",
        content="必修课程包括管理经济学、组织行为学等",
        section_title="必修课程",
        section_path="第3章>3.1 必修课程",
        sheet_name="课程结构",
        row_start=10,
        row_end=25,
        col_start=1,
        col_end=8,
        anchor_text="管理经济学",
        anchor_locator="Sheet=课程结构|行10-25|列A-H",
        source_hash="abc123",
        parser_version="1.0",
        parse_confidence=0.95,
    )


@pytest.fixture
def sample_fact() -> BaseFact:
    return BaseFact(
        fact_id="fact-001",
        fact_type="course_requirement",
        assistant_id="mba",
        program="MBA",
        cohort_year="2026",
        payload={"course_code": "MGMT501", "requirement_group": "core", "term_scope": "semester1"},
        payload_schema_version="v1.0",
        fact_key="mba:MBA:2026:course_requirement:MGMT501",
        source_node_ids=["node-001"],
        status=FactStatus.APPROVED,
    )


# ── Interface conformance ──────────────────────────────────────────────

def test_repository_is_abstract():
    """KnowledgeBaseRepository cannot be instantiated directly."""
    with pytest.raises(TypeError):
        KnowledgeBaseRepository()  # type: ignore


def test_chroma_repository_conforms_to_interface():
    """ChromaRepository should be a concrete subclass of KnowledgeBaseRepository."""
    repo = ChromaRepository(client=MagicMock())
    assert isinstance(repo, KnowledgeBaseRepository)


# ── store_nodes ────────────────────────────────────────────────────────

def test_store_nodes_creates_collection_and_adds(mock_chroma_client, sample_node):
    mock_client, mock_collection = mock_chroma_client
    repo = ChromaRepository(client=mock_client)

    repo.store_nodes("users/mba", [sample_node])

    mock_client.get_or_create_collection.assert_called_once_with("raw_nodes")
    mock_collection.upsert.assert_called_once()
    call_kwargs = mock_collection.upsert.call_args[1]
    assert sample_node.node_id in call_kwargs["ids"]
    assert sample_node.content in call_kwargs["documents"]
    # Metadata must have scope for ACL filtering
    assert call_kwargs["metadatas"][0]["scope"] == "users/mba"
    assert call_kwargs["metadatas"][0]["source_file"] == "培养计划.xlsx"


def test_store_nodes_multiple_nodes(mock_chroma_client):
    mock_client, mock_collection = mock_chroma_client
    repo = ChromaRepository(client=mock_client)
    nodes = [
        RawIndexNode(
            node_id=f"node-{i}",
            source_tier="global",
            source_file="doc.pdf",
            source_path="/docs/doc.pdf",
            content=f"content-{i}",
            anchor_text=f"anchor-{i}",
            anchor_locator=f"locator-{i}",
            source_hash="abc",
            parser_version="1.0",
        )
        for i in range(3)
    ]

    repo.store_nodes("global", nodes)

    call_kwargs = mock_collection.upsert.call_args[1]
    assert len(call_kwargs["ids"]) == 3
    assert all(m["scope"] == "global" for m in call_kwargs["metadatas"])


def test_store_nodes_handles_empty_list(mock_chroma_client):
    mock_client, mock_collection = mock_chroma_client
    repo = ChromaRepository(client=mock_client)

    repo.store_nodes("global", [])

    mock_collection.upsert.assert_not_called()


# ── search_nodes ───────────────────────────────────────────────────────

def test_search_nodes_queries_with_scopes(mock_chroma_client):
    mock_client, mock_collection = mock_chroma_client
    # Mock query result
    mock_collection.query.return_value = {
        "ids": [["node-001"]],
        "documents": [["必修课程包括管理经济学"]],
        "metadatas": [[{"scope": "users/mba", "source_file": "培养计划.xlsx"}]],
        "distances": [[0.15]],
    }
    repo = ChromaRepository(client=mock_client)

    results = repo.search_nodes(
        scopes=["global", "users/mba"],
        query_embedding=[0.1, 0.2, 0.3],
        top_k=5,
    )

    mock_collection.query.assert_called_once()
    call_kwargs = mock_collection.query.call_args[1]
    # Should filter by the given scopes
    where = call_kwargs.get("where", {})
    has_scope_filter = "scope" in where or "$or" in str(call_kwargs)
    assert has_scope_filter
    assert len(results) == 1
    assert results[0].node_id == "node-001"


def test_search_nodes_returns_empty_when_no_results(mock_chroma_client):
    mock_client, mock_collection = mock_chroma_client
    mock_collection.query.return_value = {
        "ids": [],
        "documents": [],
        "metadatas": [],
        "distances": [],
    }
    repo = ChromaRepository(client=mock_client)

    results = repo.search_nodes(scopes=["global"], query_embedding=[0.1], top_k=5)

    assert results == []


# ── store_facts ────────────────────────────────────────────────────────

def test_store_facts_creates_facts_collection(mock_chroma_client, sample_fact):
    mock_client, _mock_collection = mock_chroma_client
    repo = ChromaRepository(client=mock_client)

    repo.store_facts("global", [sample_fact])

    assert mock_client.get_or_create_collection.call_count == 1
    collection_name = mock_client.get_or_create_collection.call_args[0][0]
    assert "facts" in collection_name


# The facts collection should be separate from nodes collection
def test_facts_and_nodes_use_separate_collections(mock_chroma_client, sample_node, sample_fact):
    mock_client, _mock_collection = mock_chroma_client
    # Make get_or_create_collection return different mocks for different names
    collections = {}

    def get_or_create(name):
        if name not in collections:
            collections[name] = MagicMock()
        return collections[name]

    mock_client.get_or_create_collection.side_effect = get_or_create

    repo = ChromaRepository(client=mock_client)
    repo.store_nodes("global", [sample_node])
    repo.store_facts("global", [sample_fact])

    assert collections.keys() == {"raw_nodes", "normalized_facts"}


# ── delete_nodes ───────────────────────────────────────────────────────

def test_delete_nodes_by_scope(mock_chroma_client):
    mock_client, mock_collection = mock_chroma_client
    repo = ChromaRepository(client=mock_client)

    deleted = repo.delete_nodes("users/mba")

    assert deleted is True
    mock_collection.delete.assert_called_once()
    call_kwargs = mock_collection.delete.call_args[1]
    assert call_kwargs["where"]["scope"] == "users/mba"


def test_delete_nodes_with_source_filter(mock_chroma_client):
    mock_client, mock_collection = mock_chroma_client
    repo = ChromaRepository(client=mock_client)

    result = repo.delete_nodes("users/mba", source_file="培养计划.xlsx")
    assert result is True

    call_kwargs = mock_collection.delete.call_args[1]
    where = call_kwargs["where"]
    assert where["$and"][0]["scope"] == "users/mba"
    assert where["$and"][1]["source_file"] == "培养计划.xlsx"


# ── purge_scope ────────────────────────────────────────────────────────

def test_purge_scope_clears_both_collections(mock_chroma_client):
    mock_client = mock_chroma_client[0]
    # Set up two collection mocks
    nodes_col = MagicMock()
    facts_col = MagicMock()

    def get_or_create(name):
        return {"raw_nodes": nodes_col, "normalized_facts": facts_col}[name]

    mock_client.get_or_create_collection.side_effect = get_or_create

    repo = ChromaRepository(client=mock_client)
    repo.purge_scope("users/student_A")

    nodes_col.delete.assert_called_once_with(where={"scope": "users/student_A"})
    facts_col.delete.assert_called_once_with(where={"scope": "users/student_A"})


# ── Edge cases ─────────────────────────────────────────────────────────

def test_store_facts_handles_empty_list(mock_chroma_client):
    mock_client, mock_collection = mock_chroma_client
    repo = ChromaRepository(client=mock_client)

    repo.store_facts("global", [])

    mock_collection.upsert.assert_not_called()


def test_delete_nodes_nonexistent_scope_does_not_raise(mock_chroma_client):
    mock_client, _mock_collection = mock_chroma_client
    repo = ChromaRepository(client=mock_client)

    result = repo.delete_nodes("users/nobody")
    assert result is True


# ── embedding_function passthrough ─────────────────────────────────────

def test_chroma_repository_passes_embedding_function_to_collections():
    mock_client = MagicMock()
    mock_col = MagicMock()
    mock_client.get_or_create_collection.return_value = mock_col

    embedding_fn = MagicMock()
    repo = ChromaRepository(client=mock_client, embedding_function=embedding_fn)

    repo.store_nodes("global", [])
    repo.store_facts("global", [])

    # The embedding_function should be passed to every get_or_create_collection call
    calls = mock_client.get_or_create_collection.call_args_list
    for c in calls:
        assert c.kwargs.get("embedding_function") is embedding_fn


def test_chroma_repository_works_without_embedding_function():
    mock_client = MagicMock()
    repo = ChromaRepository(client=mock_client)

    assert repo._embedding_function is None


# ── search_nodes missing coverage ──────────────────────────────────────

def test_search_nodes_single_scope(mock_chroma_client):
    mock_client, mock_collection = mock_chroma_client
    mock_collection.query.return_value = {
        "ids": [["node-1"]],
        "documents": [["content"]],
        "metadatas": [[{"scope": "global"}]],
        "distances": [[0.1]],
    }
    repo = ChromaRepository(client=mock_client)

    results = repo.search_nodes(scopes=["global"], query_embedding=[0.1, 0.2], top_k=5)

    mock_collection.query.assert_called_once()
    where = mock_collection.query.call_args[1]["where"]
    assert where == {"scope": "global"}


def test_search_nodes_no_metadatas_in_response(mock_chroma_client):
    mock_client, mock_collection = mock_chroma_client
    mock_collection.query.return_value = {
        "ids": [["node-1"]],
        "documents": [[]],
        "metadatas": [[]],
        "distances": [[0.1]],
    }
    repo = ChromaRepository(client=mock_client)
    results = repo.search_nodes(scopes=["global"], query_embedding=[0.1])
    # Should not crash, return a node with empty fields
    assert len(results) == 1
    assert results[0].content == ""


# ── get_facts_by_keys ──────────────────────────────────────────────────

def test_get_facts_by_keys_with_matching_keys(mock_chroma_client, sample_fact):
    mock_client, mock_collection = mock_chroma_client
    fact_json = sample_fact.model_dump_json()
    mock_collection.get.return_value = {
        "ids": ["fact-001"],
        "documents": [fact_json],
        "metadatas": [{"scope": "global", "fact_key": sample_fact.fact_key}],
    }
    repo = ChromaRepository(client=mock_client)

    facts = repo.get_facts_by_keys(
        scopes=["global"], fact_keys=[sample_fact.fact_key]
    )

    mock_collection.get.assert_called_once()
    where = mock_collection.get.call_args[1]["where"]
    assert where["$and"][0]["scope"]["$in"] == ["global"]
    assert where["$and"][1]["fact_key"]["$in"] == [sample_fact.fact_key]
    assert len(facts) == 1
    assert facts[0].fact_id == sample_fact.fact_id
    assert facts[0].fact_key == sample_fact.fact_key


def test_get_facts_by_keys_empty_scope_or_keys(mock_chroma_client):
    mock_client, mock_collection = mock_chroma_client
    repo = ChromaRepository(client=mock_client)

    assert repo.get_facts_by_keys(scopes=[], fact_keys=["k1"]) == []
    assert repo.get_facts_by_keys(scopes=["global"], fact_keys=[]) == []
    mock_collection.get.assert_not_called()


def test_get_facts_by_keys_no_match(mock_chroma_client):
    mock_client, mock_collection = mock_chroma_client
    mock_collection.get.return_value = {"ids": [], "documents": [], "metadatas": []}
    repo = ChromaRepository(client=mock_client)

    facts = repo.get_facts_by_keys(scopes=["global"], fact_keys=["nonexistent"])
    assert facts == []


def test_store_facts_metadata_includes_all_fields(mock_chroma_client, sample_fact):
    mock_client, mock_collection = mock_chroma_client
    repo = ChromaRepository(client=mock_client)

    repo.store_facts("users/mba", [sample_fact])

    call_kwargs = mock_collection.upsert.call_args[1]
    meta = call_kwargs["metadatas"][0]
    assert meta["scope"] == "users/mba"
    assert meta["fact_type"] == sample_fact.fact_type
    assert meta["fact_key"] == sample_fact.fact_key
    assert meta["status"] == sample_fact.status.value
    assert meta["assistant_id"] == sample_fact.assistant_id


def test_store_facts_uses_json_serialization(mock_chroma_client, sample_fact):
    mock_client, mock_collection = mock_chroma_client
    repo = ChromaRepository(client=mock_client)

    repo.store_facts("global", [sample_fact])

    doc = mock_collection.upsert.call_args[1]["documents"][0]
    import json
    parsed = json.loads(doc)
    assert parsed["fact_id"] == sample_fact.fact_id
    assert parsed["fact_key"] == sample_fact.fact_key
