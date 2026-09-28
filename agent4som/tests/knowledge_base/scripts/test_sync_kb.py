"""Tests for the ``/sync_kb`` backend.

Post-2026-07-07: _derive_scope only supports "global" and "teachers" roots.
The "assistants/" scope derivation was removed (now handled via explicit
scope parameters). Tests use "global" and "teachers" roots.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from knowledge_base.ingestion.orchestrator import (
    IngestResult,
    IngestionOrchestrator,
    IngestionStatus,
)
from knowledge_base.scripts.sync_kb import SyncKbRunner


@pytest.fixture
def mock_orchestrator() -> MagicMock:
    orch = MagicMock(spec=IngestionOrchestrator)
    orch.ingest_file.return_value = IngestResult(
        status=IngestionStatus.INGESTED,
        filename="test.pdf",
        file_size_bytes=100,
        node_count=3,
        notification="[test.pdf indexed (3 chunks).]",
    )
    return orch


# ── Basic sync ────────────────────────────────────────────────────────


def test_sync_scans_and_ingests(mock_orchestrator: MagicMock, tmp_path: Path):
    """Files under sync roots are ingested via the orchestrator."""
    pdf = tmp_path / "global" / "培养计划.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_text("content", encoding="utf-8")

    runner = SyncKbRunner(mock_orchestrator, kb_root=str(tmp_path))
    result = runner.run(roots=["global/"])

    assert result.files_scanned == 1
    assert result.parsed_nodes == 3
    mock_orchestrator.ingest_file.assert_called_once_with("admin", str(pdf), scope="global", source="file")


def test_sync_multiple_roots(mock_orchestrator: MagicMock, tmp_path: Path):
    """Multiple sync roots are scanned independently with correct scope."""
    (tmp_path / "global").mkdir(parents=True)
    (tmp_path / "teachers").mkdir(parents=True)
    (tmp_path / "global" / "a.pdf").write_text("a", encoding="utf-8")
    (tmp_path / "teachers" / "b.pdf").write_text("b", encoding="utf-8")

    runner = SyncKbRunner(mock_orchestrator, kb_root=str(tmp_path))
    result = runner.run(roots=["global/", "teachers/"])

    assert result.files_scanned == 2
    assert result.parsed_nodes == 6  # 3 nodes per file * 2 files
    mock_orchestrator.ingest_file.assert_any_call("admin", str(tmp_path / "global" / "a.pdf"), scope="global", source="file")
    mock_orchestrator.ingest_file.assert_any_call("admin", str(tmp_path / "teachers" / "b.pdf"), scope="teachers", source="file")


def test_sync_skips_non_indexable_extensions(mock_orchestrator: MagicMock, tmp_path: Path):
    """Files with unsupported extensions are skipped."""
    (tmp_path / "global").mkdir()
    (tmp_path / "global" / "image.exe").write_text("bad", encoding="utf-8")

    runner = SyncKbRunner(mock_orchestrator, kb_root=str(tmp_path))
    result = runner.run(roots=["global/"])

    assert result.files_scanned == 0
    mock_orchestrator.ingest_file.assert_not_called()


def test_sync_missing_root_does_not_error(mock_orchestrator: MagicMock, tmp_path: Path):
    """A non-existent sync root is silently skipped."""
    runner = SyncKbRunner(mock_orchestrator, kb_root=str(tmp_path))
    result = runner.run(roots=["nonexistent/"])

    assert result.files_scanned == 0
    mock_orchestrator.ingest_file.assert_not_called()


# ── Failure handling ──────────────────────────────────────────────────


def test_sync_tracks_failures(mock_orchestrator: MagicMock, tmp_path: Path):
    """Files that fail ingestion are recorded in failed_files."""
    (tmp_path / "global").mkdir()
    (tmp_path / "global" / "a_good.pdf").write_text("good", encoding="utf-8")
    (tmp_path / "global" / "b_bad.pdf").write_text("bad", encoding="utf-8")

    # alphabetical order: a_good.pdf first, b_bad.pdf second
    mock_orchestrator.ingest_file.side_effect = [
        IngestResult(
            status=IngestionStatus.INGESTED,
            filename="a_good.pdf",
            node_count=2,
            notification="ok",
        ),
        IngestResult(
            status=IngestionStatus.ERROR,
            filename="b_bad.pdf",
            notification="fail",
            error_detail="processing error",
        ),
    ]

    runner = SyncKbRunner(mock_orchestrator, kb_root=str(tmp_path))
    result = runner.run(roots=["global/"])

    assert result.files_scanned == 2
    assert result.parsed_nodes == 2  # only good file counted
    assert len(result.failed_files) == 1
    assert "b_bad.pdf" in result.failed_files[0][0]
    assert result.partial_failure is True


def test_sync_exception_does_not_abort_scan(mock_orchestrator: MagicMock, tmp_path: Path):
    """An exception during ingest_file does not crash the full scan."""
    (tmp_path / "global").mkdir()
    (tmp_path / "global" / "f1.pdf").write_text("f1", encoding="utf-8")
    (tmp_path / "global" / "f2.pdf").write_text("f2", encoding="utf-8")

    mock_orchestrator.ingest_file.side_effect = [
        IngestResult(status=IngestionStatus.INGESTED, filename="f1.pdf", node_count=1, notification="ok"),
        Exception("unexpected crash"),
    ]

    runner = SyncKbRunner(mock_orchestrator, kb_root=str(tmp_path))
    result = runner.run(roots=["global/"])

    assert result.files_scanned == 2
    assert result.parsed_nodes == 1
    assert len(result.failed_files) == 1
    assert "unexpected crash" in result.failed_files[0][1]


# ── Result shape ──────────────────────────────────────────────────────


def test_sync_result_has_required_fields(mock_orchestrator: MagicMock):
    """SyncKbResult exposes all compat-contract fields."""
    runner = SyncKbRunner(mock_orchestrator)
    result = runner.run(roots=[])

    assert result.job_id.startswith("sync_")
    assert result.files_scanned == 0
    assert result.parsed_nodes == 0
    assert result.facts_upserted == 0
    assert result.conflicts_detected == 0
    assert result.failed_files == []
    assert result.partial_failure is False
    assert result.visibility_refresh == []


def test_sync_default_roots(mock_orchestrator: MagicMock):
    """Default roots are teachers/ and global/."""
    runner = SyncKbRunner(mock_orchestrator)
    result = runner.run()

    assert "teachers/" in result.visibility_refresh
    assert "global/" in result.visibility_refresh


# ── Scope derivation ──────────────────────────────────────────────────


def test_derive_scope_global(tmp_path: Path):
    """Files under global/ get scope 'global'."""
    runner = SyncKbRunner(MagicMock(), kb_root=str(tmp_path))
    fpath = tmp_path / "global" / "policies" / "handbook.pdf"
    assert runner._derive_scope(str(fpath), "global/") == "global"


def test_derive_scope_teachers(tmp_path: Path):
    """Files under teachers/ get scope 'teachers'."""
    runner = SyncKbRunner(MagicMock(), kb_root=str(tmp_path))
    fpath = tmp_path / "teachers" / "docs" / "syllabus.pdf"
    assert runner._derive_scope(str(fpath), "teachers/") == "teachers"


def test_derive_scope_unknown_root(tmp_path: Path):
    """Unknown sync roots return None (no default fallback)."""
    runner = SyncKbRunner(MagicMock(), kb_root=str(tmp_path))
    fpath = tmp_path / "custom" / "file.pdf"
    assert runner._derive_scope(str(fpath), "custom/") is None


def test_derive_scope_file_at_root(tmp_path: Path):
    """File directly in root gets correct scope."""
    runner = SyncKbRunner(MagicMock(), kb_root=str(tmp_path))
    (tmp_path / "global").mkdir()
    fpath = tmp_path / "global" / "readme.pdf"
    assert runner._derive_scope(str(fpath), "global/") == "global"
