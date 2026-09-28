"""Tests for the delete / orphan-scan helpers (knowledge-base delete design §4).

Uses a real ``PersistentClient`` under ``tmp_path`` — never the production
HTTP client (design §8: the harness must not touch production vectors).
"""

from unittest.mock import MagicMock

import chromadb
import pytest

from knowledge_base.core.sqlite_store import SqliteStore
from knowledge_base.models.schemas import RawIndexNode
from knowledge_base.repository.chroma_repository import ChromaRepository


@pytest.fixture
def repo(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    store = SqliteStore(str(tmp_path / "quota.db"))
    return ChromaRepository(client, sqlite_store=store)


def _node(node_id: str, source_file: str, source: str = "file",
          scope: str = "global") -> RawIndexNode:
    return RawIndexNode(
        node_id=node_id,
        scope=scope,
        source_tier="global",
        source_file=source_file,
        source=source,
        source_path=f"/{scope}/x",
        content=f"内容 {node_id}",
        anchor_text="内容",
        anchor_locator="L1",
        source_hash="h",
        parser_version="v1",
    )


def test_list_source_files_counts(repo):
    repo.store_nodes("global", [
        _node("n1", "a.txt"), _node("n2", "a.txt"), _node("n3", "b.txt"),
    ])
    assert repo.list_source_files("global") == {"a.txt": 2, "b.txt": 1}
    assert repo.list_source_files("teachers") == {}


def test_count_collection_nodes(repo):
    assert repo.count_collection_nodes() == 0
    repo.store_nodes("global", [_node("n1", "a.txt"), _node("n2", "b.txt")])
    repo.store_nodes("teachers", [_node("n3", "c.txt", scope="teachers")])
    assert repo.count_collection_nodes() == 3


def test_count_collection_nodes_raises_on_error(repo, monkeypatch):
    monkeypatch.setattr(repo, "_collection",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("chroma down")))
    with pytest.raises(RuntimeError):
        repo.count_collection_nodes()


def test_list_source_files_raises_on_error(repo, monkeypatch):
    """Silent empty would make every metadata row look like an orphan (§4.5)."""
    monkeypatch.setattr(repo, "_collection",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("chroma down")))
    with pytest.raises(RuntimeError):
        repo.list_source_files("global")


def test_get_file_source(repo):
    repo.store_nodes("global", [_node("n1", "jxtz_1_x.txt", source="jxtz")])
    assert repo.get_file_source("global", "jxtz_1_x.txt") == "jxtz"
    assert repo.get_file_source("global", "missing.txt") == ""


def test_count_nodes_raise_on_error(repo, monkeypatch):
    repo.store_nodes("global", [_node("n1", "a.txt")])
    assert repo.count_nodes("global", "a.txt") == 1

    broken = MagicMock()
    broken.get.side_effect = RuntimeError("chroma down")
    monkeypatch.setattr(repo, "_collection", lambda *a, **k: broken)
    assert repo.count_nodes("global", "a.txt") == 0          # 默认静默语义不变
    with pytest.raises(RuntimeError):
        repo.count_nodes("global", "a.txt", raise_on_error=True)


def test_get_file_documents_ordered_by_node_id(repo):
    """§4.7.2：按 node_id 顺序拼接，用于原文预览。"""
    repo.store_nodes("global", [_node("n2", "a.txt"), _node("n1", "a.txt"), _node("n3", "b.txt")])
    assert repo.get_file_documents("global", "a.txt") == ["内容 n1", "内容 n2"]
    assert repo.get_file_documents("global", "missing.txt") == []


def test_get_file_documents_raises_on_error(repo, monkeypatch):
    """静默空列表会被当成「文件不存在」→ 404，必须抛（§4.7.2）。"""
    monkeypatch.setattr(repo, "_collection",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("chroma down")))
    with pytest.raises(RuntimeError):
        repo.get_file_documents("global", "a.txt")


def test_delete_nodes_isolated_by_scope_and_file(repo):
    """删除必须同时按 scope + source_file 限定：不误删同 scope 其它文件，也不跨 scope。"""
    repo.store_nodes("global", [_node("g1", "keep.txt"), _node("g2", "drop.txt")])
    repo.store_nodes("teachers", [_node("t1", "drop.txt", scope="teachers")])
    assert repo.count_nodes("global", "drop.txt") == 1

    assert repo.delete_nodes("global", "drop.txt") is True

    assert repo.count_nodes("global", "drop.txt") == 0
    assert repo.count_nodes("global", "keep.txt") == 1      # 同 scope 其它文件不受影响
    assert repo.count_nodes("teachers", "drop.txt") == 1    # 其它 scope 同名文件不受影响


def test_delete_file_metadata_targets_one_scope(repo):
    """元数据删除按 (user_id, filename, scope) 精确命中，不跨 scope。"""
    repo.set_file_metadata("admin", "a.txt", {"scope": "global", "content_hash": "h"})
    repo.set_file_metadata("admin", "a.txt", {"scope": "teachers", "content_hash": "h2"})
    repo.delete_file_metadata("admin", "a.txt", "global")
    assert [r["filename"] for r in repo.list_file_metadata_by_scope("global")] == []
    assert [r["filename"] for r in repo.list_file_metadata_by_scope("teachers")] == ["a.txt"]


def test_backfill_missing_metadata(repo):
    """「有向量、无元数据」的文件应能被补登记（修复历史静默失败），且幂等。"""
    repo.store_nodes("global", [_node("n1", "orphan.txt")])
    assert repo.list_file_metadata_by_scope("global") == []
    assert repo.backfill_missing_metadata("global", "admin") == 1
    rows = repo.list_file_metadata_by_scope("global")
    assert [r["filename"] for r in rows] == ["orphan.txt"]
    assert rows[0]["content_hash"] == "h"          # 取自节点 source_hash
    assert repo.backfill_missing_metadata("global", "admin") == 0


def test_list_file_metadata_by_scope_passthrough(repo):
    repo.set_file_metadata("admin", "a.txt", {"scope": "global", "content_hash": "h"})
    repo.set_file_metadata("admin", "b.txt", {"scope": "teachers", "content_hash": "h2"})
    rows = repo.list_file_metadata_by_scope("global")
    assert [r["filename"] for r in rows] == ["a.txt"]
    assert rows[0]["user_id"] == "admin" and rows[0]["scope"] == "global"


def test_list_file_metadata_by_scope_order_passthrough(repo):
    repo.set_file_metadata("admin", "a.txt", {"scope": "global", "content_hash": "h"})
    repo.set_file_metadata("admin", "b.txt", {"scope": "global", "content_hash": "h2"})
    assert [r["filename"] for r in repo.list_file_metadata_by_scope("global", "desc")] \
        == ["b.txt", "a.txt"]
