"""Tests for PurgeUserKBPipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from knowledge_base.repository.interfaces import KnowledgeBaseRepository
from knowledge_base.scripts.purge_pipeline import PurgeUserKBPipeline


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def mock_repo() -> MagicMock:
    return MagicMock(spec=KnowledgeBaseRepository)

# ── Tests ─────────────────────────────────────────────────────────────


def test_purge_cleans_vectors(mock_repo: MagicMock):
    """run() calls purge_scope on the repo."""
    pipeline = PurgeUserKBPipeline(repo=mock_repo)
    result = pipeline.run("student_A", dry_run=False)

    mock_repo.purge_scope.assert_called_once_with("users/student_A")
    assert result["vectors_purged"] is True


def test_purge_dry_run_skips_vector_deletion(mock_repo: MagicMock):
    """Dry-run should NOT call purge_scope."""
    pipeline = PurgeUserKBPipeline(repo=mock_repo)
    result = pipeline.run("student_B", dry_run=True)

    mock_repo.purge_scope.assert_not_called()
    assert result["vectors_purged"] is False


def test_purge_dry_run_still_audits_log(mock_repo: MagicMock):
    """Even in dry-run, the audit callback fires."""
    audit_entries: list[str] = []

    def audit(msg: str) -> None:
        audit_entries.append(msg)

    pipeline = PurgeUserKBPipeline(repo=mock_repo, audit_log=audit)
    pipeline.run("student_C", dry_run=True)

    assert any("dry_run=True" in msg for msg in audit_entries)
    assert any("student_C" in msg for msg in audit_entries)


def test_purge_removes_file_directory(mock_repo: MagicMock, tmp_path: Path):
    """When user_files_root is set, run() removes the user's file dir."""
    user_dir = tmp_path / "student_A"
    user_dir.mkdir(parents=True)
    (user_dir / "test.pdf").write_text("content")

    pipeline = PurgeUserKBPipeline(
        repo=mock_repo,
        user_files_root=tmp_path,
    )
    result = pipeline.run("student_A", dry_run=False)

    assert result["files_purged"] is True
    assert not user_dir.exists()


def test_purge_dry_run_skips_file_deletion(mock_repo: MagicMock, tmp_path: Path):
    """Dry-run should NOT delete physical files."""
    user_dir = tmp_path / "student_D"
    user_dir.mkdir(parents=True)
    (user_dir / "doc.pdf").write_text("data")

    pipeline = PurgeUserKBPipeline(
        repo=mock_repo,
        user_files_root=tmp_path,
    )
    result = pipeline.run("student_D", dry_run=True)

    assert result["files_purged"] is False
    assert user_dir.exists()


def test_purge_missing_file_dir_does_not_error(mock_repo: MagicMock, tmp_path: Path):
    """run() should not crash when the user's file dir does not exist."""
    pipeline = PurgeUserKBPipeline(
        repo=mock_repo,
        user_files_root=tmp_path,
    )
    # user "ghost" has no directory
    result = pipeline.run("ghost", dry_run=False)

    mock_repo.purge_scope.assert_called_once_with("users/ghost")
    assert result["files_purged"] is False


def test_purge_summary_shape(mock_repo: MagicMock):
    """Returned summary dict has the expected keys."""
    pipeline = PurgeUserKBPipeline(repo=mock_repo)
    result = pipeline.run("anyone", dry_run=False)

    assert set(result.keys()) == {"user_id", "dry_run", "vectors_purged", "files_purged", "metadata_purged"}
    assert result["metadata_purged"] is True


def test_purge_without_user_files_root_skips_file_cleanup(mock_repo: MagicMock):
    """When user_files_root is None, file cleanup is skipped entirely."""
    pipeline = PurgeUserKBPipeline(repo=mock_repo)
    result = pipeline.run("student_E", dry_run=False)

    assert result["files_purged"] is False
