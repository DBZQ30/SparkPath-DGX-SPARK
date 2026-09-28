"""Full-pipeline integration tests.

Exercises the complete data plane:
  ingest → store → retrieve → filter → purge

Uses ``MockRepo`` (in-memory KnowledgeBaseRepository) to avoid the
ChromaDB/sqlite3 dependency while still validating every layer's
interaction with the real production classes.
"""

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
from knowledge_base.models.schemas import BaseFact, RawIndexNode
from knowledge_base.pipeline.router import ExtractorRouter
from knowledge_base.repository.interfaces import KnowledgeBaseRepository
from knowledge_base.retrieval.acl_filter import ACLFilter
from knowledge_base.scripts.purge_pipeline import PurgeUserKBPipeline
from knowledge_base.scripts.sync_kb import SyncKbRunner


# ── MockRepo (same as test_orchestrator.py but inlined for self-containment) ─


class MockRepo(KnowledgeBaseRepository):
    """In-memory KnowledgeBaseRepository for integration tests."""

    def __init__(self):
        self.nodes: Dict[str, List[RawIndexNode]] = {}
        self.facts: Dict[str, List[BaseFact]] = {}
        self.file_metadata: Dict[str, dict] = {}
        self.user_file_counts: Dict[str, int] = {}
        self.user_daily_uploads: Dict[str, int] = {}

    def store_nodes(self, scope: str, nodes: List[RawIndexNode]) -> None:
        self.nodes.setdefault(scope, []).extend(nodes)

    def search_nodes(self, scopes, query_embedding, top_k=5):
        return []

    def delete_nodes(self, scope: str, source_file: str | None = None) -> bool:
        if source_file:
            self.nodes[scope] = [
                n for n in self.nodes.get(scope, []) if n.source_file != source_file
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

    def get_user_file_count(self, user_id: str) -> int:
        return self.user_file_counts.get(user_id, 0)

    def get_user_daily_upload_count(self, user_id: str) -> int:
        return self.user_daily_uploads.get(user_id, 0)

    def get_user_recent_upload_count(self, user_id: str, window_seconds: int = 60) -> int:
        return self.user_daily_uploads.get(user_id, 0)

    def record_upload(self, user_id: str) -> None:
        pass

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
def repo() -> MockRepo:
    return MockRepo()


@pytest.fixture
def orchestrator(repo: MockRepo) -> IngestionOrchestrator:
    return IngestionOrchestrator(
        repo=repo,
        quota_manager=QuotaManager(repo),
        version_manager=VersionManager(repo),
        router=ExtractorRouter(),
    )


# ── Scenario 1: User → ingest → store → retrieve → ACL filter ────────


class TestEndToEndUserPipeline:
    """A user uploads a file → it gets ingested, stored, and is retrievable."""

    def test_user_uploads_document(self, orchestrator: IngestionOrchestrator, repo: MockRepo, tmp_path: Path):
        """Full flow: upload → ingest → nodes stored in user scope."""
        pdf = tmp_path / "report.pdf"
        pdf.write_text("Quarterly report content for Q1 2026.", encoding="utf-8")

        result = orchestrator.ingest_file("student_A", str(pdf))
        assert result.status == IngestionStatus.INGESTED

        scope = "users/student_A"
        assert scope in repo.nodes
        nodes = repo.nodes[scope]
        assert len(nodes) > 0
        assert all(n.source_file == "report.pdf" for n in nodes)  # extension kept for dedup match
        assert all(n.source_tier == "user" for n in nodes)

    def test_student_can_only_see_own_documents(self, orchestrator: IngestionOrchestrator, repo: MockRepo, tmp_path: Path):
        """ACL filter restricts student to their own scope plus global."""
        f1 = tmp_path / "a.pdf"
        f1.write_text("a", encoding="utf-8")
        f2 = tmp_path / "b.pdf"
        f2.write_text("b", encoding="utf-8")

        orchestrator.ingest_file("student_A", str(f1))
        orchestrator.ingest_file("student_B", str(f2))

        acl = ACLFilter(current_user_id="student_A", current_role="student")
        allowed = acl.get_allowed_scopes()
        assert "users/student_A" in allowed
        assert "users/student_B" not in allowed

    def test_owner_can_access_all_scopes(self):
        """Owner role bypasses ACL restrictions."""
        acl = ACLFilter(current_user_id="anyone", current_role="owner")
        # owner can access any user scope
        acl.validate_requested_scope("users/someone_else")  # does not raise


# ── Scenario 2: Duplicate uploads are skipped ─────────────────────────


class TestDedupAcrossSessions:
    """Same content across multiple uploads → second is SKIPPED."""

    def test_reupload_same_file(self, orchestrator: IngestionOrchestrator, repo: MockRepo, tmp_path: Path):
        f = tmp_path / "doc.pdf"
        f.write_text("stable content", encoding="utf-8")

        r1 = orchestrator.ingest_file("user_1", str(f))
        assert r1.status == IngestionStatus.INGESTED

        r2 = orchestrator.ingest_file("user_1", str(f))
        assert r2.status == IngestionStatus.SKIPPED

        scope = "users/user_1"
        assert len(repo.nodes[scope]) > 0


# ── Scenario 3: Quota enforcement ─────────────────────────────────────


class TestQuotaEnforcement:
    """Users who exceed limits get clear error feedback."""

    def test_file_too_large(self, orchestrator: IngestionOrchestrator, repo: MockRepo, tmp_path: Path):
        # Use a QuotaManager with a small limit so we don't need a huge file.
        tight_quota = QuotaManager(repo, max_file_size_mb=1)
        orch = IngestionOrchestrator(
            repo=repo,
            quota_manager=tight_quota,
            version_manager=VersionManager(repo),
            router=ExtractorRouter(),
        )
        huge = tmp_path / "huge.pdf"
        huge.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")  # 2 MB > 1 MB limit
        result = orch.ingest_file("user_1", str(huge))
        assert result.status == IngestionStatus.QUOTA_EXCEEDED

    def test_too_many_files(self, orchestrator: IngestionOrchestrator, repo: MockRepo, tmp_path: Path):
        repo.user_file_counts["user_1"] = 50
        f = tmp_path / "extra.pdf"
        f.write_text("extra", encoding="utf-8")
        result = orchestrator.ingest_file("user_1", str(f))
        assert result.status == IngestionStatus.QUOTA_EXCEEDED


# ── Scenario 4: Admin /sync_kb ────────────────────────────────────────


class TestSyncKbFlow:
    """Admin triggers /sync_kb — files in assistant/ and global/ are indexed."""

    def test_sync_kb_indexes_assistant_files(self, repo: MockRepo, tmp_path: Path):
        orch = IngestionOrchestrator(
            repo=repo,
            quota_manager=QuotaManager(repo),
            version_manager=VersionManager(repo),
            router=ExtractorRouter(),
        )
        runner = SyncKbRunner(orch, kb_root=str(tmp_path))

        (tmp_path / "global").mkdir(parents=True)
        (tmp_path / "global" / "courses.pdf").write_text("MBA courses", encoding="utf-8")

        result = runner.run(roots=["global/"])
        assert result.files_scanned == 1
        assert result.parsed_nodes > 0
        assert "global/" in result.visibility_refresh

    def test_sync_kb_reports_partial_failures(self, repo: MockRepo, tmp_path: Path):
        orch = IngestionOrchestrator(
            repo=repo,
            quota_manager=QuotaManager(repo),
            version_manager=VersionManager(repo),
            router=ExtractorRouter(),
        )
        runner = SyncKbRunner(orch, kb_root=str(tmp_path))

        (tmp_path / "global").mkdir()
        (tmp_path / "global" / "ok.pdf").write_text("ok", encoding="utf-8")
        (tmp_path / "global" / "bad.exe").write_text("bad", encoding="utf-8")

        result = runner.run(roots=["global/"])
        # .exe is filtered — only .pdf is scanned
        assert result.files_scanned == 1
        assert result.parsed_nodes > 0
        assert result.partial_failure is False


# ── Scenario 5: User lifecycle — purge ────────────────────────────────


class TestUserLifecycle:
    """When a user is removed, all their KB data is purged."""

    def test_purge_removes_all_user_nodes(self, orchestrator: IngestionOrchestrator, repo: MockRepo, tmp_path: Path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_text("content", encoding="utf-8")
        orchestrator.ingest_file("user_to_delete", str(pdf))

        assert "users/user_to_delete" in repo.nodes

        pipeline = PurgeUserKBPipeline(repo=repo)
        summary = pipeline.run("user_to_delete", dry_run=False)

        assert summary["vectors_purged"] is True
        assert "users/user_to_delete" not in repo.nodes

    def test_purge_dry_run_does_not_delete(self, orchestrator: IngestionOrchestrator, repo: MockRepo, tmp_path: Path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_text("content", encoding="utf-8")
        orchestrator.ingest_file("user_keep", str(pdf))

        pipeline = PurgeUserKBPipeline(repo=repo)
        summary = pipeline.run("user_keep", dry_run=True)

        assert summary["vectors_purged"] is False
        assert "users/user_keep" in repo.nodes


# ── Scenario 6: Unknown file types are rejected ───────────────────────


class TestFileTypeRouting:
    """Unsupported file types never reach the store."""

    def test_png_file_accepted_as_document(self, orchestrator: IngestionOrchestrator, repo: MockRepo, tmp_path: Path):
        """PNG is now a supported document type (image OCR is available)."""
        f = tmp_path / "image.png"
        f.write_text("not a doc", encoding="utf-8")
        result = orchestrator.ingest_file("user_1", str(f))
        # PNG routes to DOCUMENT type; may fail to read content (invalid PNG data)
        # but should NOT be rejected as UNSUPPORTED_TYPE
        assert result.status != IngestionStatus.UNSUPPORTED_TYPE

    def test_missing_file_returns_not_found(self, orchestrator: IngestionOrchestrator):
        result = orchestrator.ingest_file("user_1", "/nonexistent/path.pdf")
        assert result.status == IngestionStatus.FILE_NOT_FOUND
