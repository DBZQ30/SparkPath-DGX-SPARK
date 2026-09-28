"""Tests for ChromaRepository where-clause hardening against prompt injection.

C-10: search_nodes() must validate scope tokens before building the where clause.
"""

from unittest.mock import MagicMock
import pytest
from knowledge_base.repository.chroma_repository import ChromaRepository


@pytest.fixture
def repo():
    client = MagicMock()
    mock_collection = MagicMock()
    client.get_or_create_collection.return_value = mock_collection
    return ChromaRepository(client), mock_collection


def test_valid_scopes_pass_validation(repo):
    r, _ = repo
    r._validate_scopes(["global"])
    r._validate_scopes(["users/mba-admission"])
    r._validate_scopes(["users/zhangsan"])
    r._validate_scopes(["users/test_user_123"])
    r._validate_scopes(["global", "users/mba", "users/test"])


def test_invalid_scope_raises(repo):
    r, _ = repo
    with pytest.raises(ValueError, match="Invalid scope token"):
        r._validate_scopes(["global; drop table"])

    with pytest.raises(ValueError, match="Invalid scope token"):
        r._validate_scopes(["../../etc/passwd"])

    with pytest.raises(ValueError, match="Invalid scope token"):
        r._validate_scopes(['{"$ne": "global"}'])

    with pytest.raises(ValueError, match="Invalid scope token"):
        r._validate_scopes(["users/../global"])

    with pytest.raises(ValueError, match="Invalid scope token"):
        r._validate_scopes(["global/extra"])

    with pytest.raises(ValueError, match="Invalid scope token"):
        r._validate_scopes(["assistants//"])


def test_search_nodes_rejects_invalid_scopes(repo):
    r, mock_collection = repo
    with pytest.raises(ValueError, match="Invalid scope token"):
        r.search_nodes(scopes=["'; drop collection --"], query_embedding=[0.0], top_k=5)
    mock_collection.query.assert_not_called()


def test_store_nodes_rejects_invalid_scope(repo):
    r, mock_collection = repo
    from knowledge_base.models.schemas import RawIndexNode
    node = RawIndexNode(
        node_id="x", source_tier="u", source_file="f.txt", source_path="/f.txt",
        content="x", anchor_text="x", anchor_locator="L1", source_hash="h",
        parser_version="v1",
    )
    with pytest.raises(ValueError, match="Invalid scope token"):
        r.store_nodes("'; drop table --", [node])
    mock_collection.add.assert_not_called()


def test_purge_scope_rejects_invalid_scope(repo):
    r, mock_collection = repo
    with pytest.raises(ValueError, match="Invalid scope token"):
        r.purge_scope("'; delete all --")
    mock_collection.delete.assert_not_called()


def test_get_facts_by_keys_rejects_invalid_scope(repo):
    r, mock_collection = repo
    with pytest.raises(ValueError, match="Invalid scope token"):
        r.get_facts_by_keys(scopes=["evil]; --"], fact_keys=["k1"])
    mock_collection.get.assert_not_called()
