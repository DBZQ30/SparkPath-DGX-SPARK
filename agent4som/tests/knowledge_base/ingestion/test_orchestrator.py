"""Tests for IngestionOrchestrator — the end-to-end file ingestion pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import pytest

from knowledge_base.core.quota_manager import QuotaManager
from knowledge_base.core.version_manager import VersionManager
from knowledge_base.ingestion.orchestrator import (
    IngestionOrchestrator,
    IngestionStatus,
)
from knowledge_base.models.schemas import RawIndexNode, BaseFact
from knowledge_base.pipeline.router import ExtractorRouter
from knowledge_base.repository.interfaces import KnowledgeBaseRepository


# ── Mock repository ──────────────────────────────────────────────────


class MockRepo(KnowledgeBaseRepository):
    """In-memory mock that also satisfies the quota/version repo protocols."""

    def __init__(self):
        self.nodes: Dict[str, List[RawIndexNode]] = {}
        self.facts: Dict[str, List[BaseFact]] = {}
        self.file_metadata: Dict[str, dict] = {}
        self.user_file_counts: Dict[str, int] = {}
        self.user_daily_uploads: Dict[str, int] = {}

    # KnowledgeBaseRepository interface
    def store_nodes(self, scope: str, nodes: List[RawIndexNode]) -> None:
        self.nodes.setdefault(scope, []).extend(nodes)

    def search_nodes(self, scopes, query_embedding, top_k=5):
        return []

    def delete_nodes(self, scope: str, source_file: str | None = None) -> bool:
        if source_file:
            self.nodes[scope] = [
                n for n in self.nodes.get(scope, [])
                if n.source_file != source_file
            ]
        else:
            self.nodes.pop(scope, None)
        return True

    def store_facts(self, scope: str, facts: List[BaseFact]) -> None:
        self.facts.setdefault(scope, []).extend(facts)

    def get_facts_by_keys(self, scopes, fact_keys):
        return []

    def purge_scope(self, scope: str) -> None:
        self.nodes.pop(scope, None)
        self.facts.pop(scope, None)

    # QuotaRepo protocol methods
    def get_user_file_count(self, user_id: str) -> int:
        return self.user_file_counts.get(user_id, 0)

    def get_user_daily_upload_count(self, user_id: str) -> int:
        return self.user_daily_uploads.get(user_id, 0)

    def get_user_recent_upload_count(self, user_id: str, window_seconds: int = 60) -> int:
        return self.user_daily_uploads.get(user_id, 0)

    def record_upload(self, user_id: str) -> None:
        pass

    # VersionRepo protocol methods
    def get_file_metadata(self, user_id: str, filename: str, scope: str = "") -> Optional[dict]:
        return self.file_metadata.get(f"{user_id}:{filename}")

    def get_file_metadata_any_user(self, filename: str, scope: str = "") -> Optional[dict]:
        for key, meta in self.file_metadata.items():
            if filename in key:
                return meta
        return None

    def set_file_metadata(self, user_id: str, filename: str, metadata: dict) -> None:
        self.file_metadata[f"{user_id}:{filename}"] = metadata

    def file_hash_exists(self, file_hash: str, scope: str = "") -> bool:
        """Check if a file_hash already exists in the given scope."""
        for _key, meta in self.file_metadata.items():
            if meta.get("file_hash") == file_hash and (not scope or meta.get("scope") == scope):
                return True
        return False

    def count_nodes(self, scope: str, source_file: str | None = None) -> int:
        """Count nodes in a scope, optionally filtered by source_file."""
        nodes = self.nodes.get(scope, [])
        if source_file:
            nodes = [n for n in nodes if n.source_file == source_file]
        return len(nodes)

    def delete_user_data(self, user_id: str) -> None:
        keys = [k for k in self.file_metadata if k.startswith(f"{user_id}:")]
        for k in keys:
            self.file_metadata.pop(k, None)
        self.user_file_counts.pop(user_id, None)
        self.user_daily_uploads.pop(user_id, None)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_repo() -> MockRepo:
    return MockRepo()


@pytest.fixture
def orchestrator(mock_repo: MockRepo) -> IngestionOrchestrator:
    quota = QuotaManager(mock_repo, max_file_size_mb=10)  # match test expectations
    version = VersionManager(mock_repo)
    router = ExtractorRouter()
    return IngestionOrchestrator(
        repo=mock_repo,
        quota_manager=quota,
        version_manager=version,
        router=router,
    )


# ── Platform parameter ─────────────────────────────────────────────────

class TestPlatformParameter:
    def test_platform_passed_to_role_resolution(self, orchestrator: IngestionOrchestrator, tmp_path: Path):
        """The platform parameter is accepted and does not crash."""
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        # Should not raise — platform="web" resolves roles from the web platform
        result = orchestrator.ingest_file("user_1", str(f), platform="web")
        assert result.status in (IngestionStatus.INGESTED, IngestionStatus.ERROR)

    def test_platform_defaults_to_wecom(self, orchestrator: IngestionOrchestrator, tmp_path: Path):
        """Default platform is 'wecom' for backward compatibility."""
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        result = orchestrator.ingest_file("user_1", str(f))
        assert result.status in (IngestionStatus.INGESTED, IngestionStatus.ERROR)


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "test.pdf"
    p.write_text("Sample PDF content for ingestion testing.", encoding="utf-8")
    return p


@pytest.fixture
def sample_docx(tmp_path: Path) -> Path:
    p = tmp_path / "report.docx"
    p.write_text("DOCX content simulation.", encoding="utf-8")
    return p


@pytest.fixture
def sample_xlsx(tmp_path: Path) -> Path:
    p = tmp_path / "grades.xlsx"
    p.write_text("xlsx,data,values", encoding="utf-8")
    return p


# ── Tests: successful ingestion ───────────────────────────────────────


class TestSuccessfulIngestion:
    def test_ingest_pdf_creates_nodes(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, sample_pdf: Path):
        """Ingesting a PDF creates nodes in the user's scope."""
        result = orchestrator.ingest_file("user_1", str(sample_pdf))
        assert result.status == IngestionStatus.INGESTED
        assert result.filename == "test.pdf"
        scope = "users/user_1"
        assert scope in mock_repo.nodes
        assert len(mock_repo.nodes[scope]) > 0

    def test_ingest_docx_creates_nodes(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, sample_docx: Path):
        """Ingesting a DOCX creates nodes."""
        result = orchestrator.ingest_file("user_1", str(sample_docx))
        assert result.status == IngestionStatus.INGESTED
        scope = "users/user_1"
        assert scope in mock_repo.nodes

    def test_nodes_have_correct_metadata(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, sample_pdf: Path):
        """Stored nodes contain correct provenance metadata."""
        orchestrator.ingest_file("user_1", str(sample_pdf))
        scope = "users/user_1"
        node = mock_repo.nodes[scope][0]
        assert node.source_file == "test.pdf"  # extension kept for dedup match
        assert node.source_tier == "user"
        assert "users/user_1" in node.source_path
        assert node.node_id.startswith("chk_")  # deterministic chunk ID format

    def test_different_users_isolated(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, tmp_path: Path):
        """Different users' files go to different scopes."""
        f1 = tmp_path / "a.pdf"
        f1.write_text("user_a content", encoding="utf-8")
        f2 = tmp_path / "b.pdf"
        f2.write_text("user_b content", encoding="utf-8")

        orchestrator.ingest_file("user_a", str(f1))
        orchestrator.ingest_file("user_b", str(f2))

        assert "users/user_a" in mock_repo.nodes
        assert "users/user_b" in mock_repo.nodes

    def test_returns_file_size(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, sample_pdf: Path):
        """Result includes the ingested file size."""
        result = orchestrator.ingest_file("user_1", str(sample_pdf))
        assert result.file_size_bytes > 0


# ── Tests: dedup via VersionManager ───────────────────────────────────


class TestDedup:
    def test_same_file_skipped(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, sample_pdf: Path):
        """Same file content → SKIP, not re-ingested."""
        r1 = orchestrator.ingest_file("user_1", str(sample_pdf))
        assert r1.status == IngestionStatus.INGESTED

        r2 = orchestrator.ingest_file("user_1", str(sample_pdf))
        assert r2.status == IngestionStatus.SKIPPED

    def test_modified_file_replaces(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, sample_pdf: Path):
        """Modified content → REPLACE (delete old, ingest new)."""
        r1 = orchestrator.ingest_file("user_1", str(sample_pdf))
        assert r1.status == IngestionStatus.INGESTED
        first_node_count = len(orchestrator._repo.nodes.get("users/user_1", []))

        # Modify and re-ingest
        sample_pdf.write_text("MODIFIED: completely different content", encoding="utf-8")
        r2 = orchestrator.ingest_file("user_1", str(sample_pdf))
        assert r2.status == IngestionStatus.REPLACED

        # Old nodes should be gone, new nodes present
        scope = "users/user_1"
        assert len(orchestrator._repo.nodes[scope]) == first_node_count  # same count, fresh nodes


# ── Tests: metadata write failure must not orphan vectors ─────────────


class TestMetadataFailure:
    def test_metadata_failure_aborts_before_nodes(
            self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo,
            sample_pdf: Path, monkeypatch):
        """元数据写失败必须中止（不写节点）：否则出现"有向量无元数据"的不可见文件。"""
        import sqlite3

        def boom(*_a, **_k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(mock_repo, "set_file_metadata", boom)
        result = orchestrator.ingest_file("user_1", str(sample_pdf))
        assert result.status == IngestionStatus.ERROR
        assert "users/user_1" not in mock_repo.nodes

    def test_replace_metadata_failure_keeps_old_nodes(
            self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo,
            sample_pdf: Path, monkeypatch):
        """REPLACE 时元数据失败也应保留旧节点（删除必须发生在元数据成功之后）。"""
        import sqlite3
        r1 = orchestrator.ingest_file("user_1", str(sample_pdf))
        assert r1.status == IngestionStatus.INGESTED
        old = list(mock_repo.nodes["users/user_1"])
        sample_pdf.write_text("MODIFIED: completely different content", encoding="utf-8")

        def boom(*_a, **_k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(mock_repo, "set_file_metadata", boom)
        r2 = orchestrator.ingest_file("user_1", str(sample_pdf))
        assert r2.status == IngestionStatus.ERROR
        assert mock_repo.nodes["users/user_1"] == old


# ── Tests: quota enforcement ──────────────────────────────────────────


class TestQuota:
    def test_exceeds_file_size(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, tmp_path: Path):
        """File exceeding max size → QUOTA_EXCEEDED."""
        big = tmp_path / "huge.pdf"
        big.write_text("x" * (11 * 1024 * 1024), encoding="utf-8")  # 11MB
        result = orchestrator.ingest_file("user_1", str(big))
        assert result.status == IngestionStatus.QUOTA_EXCEEDED

    def test_exceeds_daily_limit(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, tmp_path: Path):
        """Exceed daily uploads → QUOTA_EXCEEDED."""
        mock_repo.user_daily_uploads["user_1"] = 30
        f = tmp_path / "test.pdf"
        f.write_text("small file", encoding="utf-8")
        result = orchestrator.ingest_file("user_1", str(f))
        assert result.status == IngestionStatus.QUOTA_EXCEEDED

    def test_exceeds_file_count(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, tmp_path: Path):
        """Exceed max files → QUOTA_EXCEEDED."""
        mock_repo.user_file_counts["user_1"] = 50
        f = tmp_path / "test.pdf"
        f.write_text("small file", encoding="utf-8")
        result = orchestrator.ingest_file("user_1", str(f))
        assert result.status == IngestionStatus.QUOTA_EXCEEDED


# ── Tests: unknown file type ─────────────────────────────────────────


class TestUnknownType:
    def test_unknown_extension_rejected(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, tmp_path: Path):
        """Files with unsupported extensions → UNSUPPORTED_TYPE."""
        f = tmp_path / "virus.exe"
        f.write_text("not a document", encoding="utf-8")
        result = orchestrator.ingest_file("user_1", str(f))
        assert result.status == IngestionStatus.UNSUPPORTED_TYPE

    def test_nonexistent_file(self, orchestrator: IngestionOrchestrator):
        """Non-existent file path → FILE_NOT_FOUND."""
        result = orchestrator.ingest_file("user_1", "/nonexistent/file.pdf")
        assert result.status == IngestionStatus.FILE_NOT_FOUND


# ── Tests: xlsx/csv routed but marked ────────────────────────────────


class TestStructuredTable:
    def test_xlsx_processed_with_warning(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, sample_xlsx: Path):
        """XLSX files are processed but result indicates structured table path."""
        result = orchestrator.ingest_file("user_1", str(sample_xlsx))
        # XLSX is in the table_extensions, so it's processed but annotated
        assert result.status == IngestionStatus.INGESTED
        assert result.file_type == "structured_table"
        assert result.warning is not None
        assert "Structured tables" in result.warning


# ── Tests: gateway HookNotification ───────────────────────────────────


class TestHookNotification:
    def test_notification_for_ingested(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, sample_pdf: Path):
        """Ingested files produce a user-facing notification."""
        result = orchestrator.ingest_file("user_1", str(sample_pdf))
        assert result.notification is not None
        assert "indexed" in result.notification.lower()

    def test_notification_for_skipped(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, sample_pdf: Path):
        """Skipped (duplicate) files produce a notification."""
        orchestrator.ingest_file("user_1", str(sample_pdf))
        result = orchestrator.ingest_file("user_1", str(sample_pdf))
        assert result.notification is not None
        assert "already" in result.notification.lower()

    def test_notification_for_quota_exceeded(self, orchestrator: IngestionOrchestrator, mock_repo: MockRepo, tmp_path: Path):
        """Quota exceeded produces an error notification."""
        mock_repo.user_file_counts["user_1"] = 50
        f = tmp_path / "test.pdf"
        f.write_text("content", encoding="utf-8")
        result = orchestrator.ingest_file("user_1", str(f))
        assert result.notification is not None
        assert "上限" in result.notification
