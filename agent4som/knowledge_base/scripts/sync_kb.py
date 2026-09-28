"""``/sync_kb`` backend — administrator-driven KB visibility refresh.

Scans configured directories (``assistants/``, ``global/``, etc.) and
ingests any new or updated files via ``IngestionOrchestrator``.

Compat contract (v1) return fields::

    job_id             — unique run identifier
    files_scanned      — total files examined
    parsed_nodes       — nodes created
    facts_upserted     — facts upserted (future)
    conflicts_detected — schema or data conflicts
    failed_files       — list of (path, error) tuples
    partial_failure    — True if any file failed
    visibility_refresh — echo of the scope paths refreshed
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from uuid import uuid4

from knowledge_base.ingestion.orchestrator import IngestionOrchestrator

logger = logging.getLogger(__name__)

# Default directories scanned by /sync_kb
_DEFAULT_SYNC_ROOTS = [
    "teachers/",
    "global/",
]
# NOTE: users/ is deliberately excluded from batch sync because personal KBs
# are populated through individual file uploads (auto_ingest hook), not admin
# batch operations.  The admin should not be able to bulk-ingest files into
# other people's personal knowledge bases.

# File extensions the pipeline can handle (must match ExtractorRouter)
_INDEXABLE_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md", ".xlsx", ".xls", ".csv", ".pptx", ".jpg", ".jpeg", ".png"}


@dataclass
class SyncKbResult:
    """Result of a single ``/sync_kb`` run."""

    job_id: str
    files_scanned: int = 0
    parsed_nodes: int = 0
    facts_upserted: int = 0
    conflicts_detected: int = 0
    failed_files: List[tuple[str, str]] = field(default_factory=list)
    visibility_refresh: List[str] = field(default_factory=list)

    @property
    def partial_failure(self) -> bool:
        return len(self.failed_files) > 0


class SyncKbRunner:
    """Scans KB source directories and syncs them into the vector store.

    Usage::

        runner = SyncKbRunner(orchestrator)
        result = runner.run(["assistants/", "global/"])
    """

    def __init__(
        self,
        orchestrator: IngestionOrchestrator,
        kb_root: str | None = None,
        file_filter: Callable[[str], bool] | None = None,
    ):
        self._orchestrator = orchestrator
        self._kb_root = (kb_root or ".").rstrip("/")
        self._file_filter = file_filter or self._default_file_filter

    @staticmethod
    def _default_file_filter(path: str) -> bool:
        _, ext = os.path.splitext(path)
        return ext.lower() in _INDEXABLE_EXTS

    def _derive_scope(self, fpath: str, sync_root: str) -> str | None:
        abs_root = os.path.join(self._kb_root, sync_root)
        rel = os.path.relpath(fpath, abs_root)
        parts = rel.split(os.sep)
        root_norm = sync_root.rstrip("/")
        if root_norm == "global":
            return "global"
        if root_norm == "teachers":
            return "teachers"
        return None

    def run(
        self,
        roots: Optional[List[str]] = None,
        admin_user_id: str = "admin",
    ) -> SyncKbResult:
        """Execute a full sync cycle.

        Args:
            roots: Directories relative to *kb_root* to scan. Defaults to
                   ``["assistants/", "global/"]``. Pass an explicit empty list
                   to scan nothing.
            admin_user_id: User ID under which ingestion runs (for audit).
        """
        result = SyncKbResult(job_id=f"sync_{uuid4().hex[:12]}")

        sync_roots = roots if roots is not None else list(_DEFAULT_SYNC_ROOTS)
        result.visibility_refresh = list(sync_roots)

        for root in sync_roots:
            abs_root = os.path.join(self._kb_root, root)
            if not os.path.isdir(abs_root):
                logger.info("sync_kb: root %s does not exist, skipping", abs_root)
                continue

            for dirpath, _dirnames, filenames in os.walk(abs_root):
                for fname in sorted(filenames):
                    fpath = os.path.join(dirpath, fname)
                    if not self._file_filter(fpath):
                        continue

                    result.files_scanned += 1

                    try:
                        scope = self._derive_scope(fpath, root)
                        ingest_result = self._orchestrator.ingest_file(
                            admin_user_id, fpath, scope=scope, source="file"
                        )
                        result.parsed_nodes += ingest_result.node_count

                        if ingest_result.status.value in (
                            "quota_exceeded",
                            "error",
                            "unsupported_type",
                            "file_not_found",
                        ):
                            result.failed_files.append((fpath, ingest_result.status.value))
                    except Exception as exc:
                        logger.exception("sync_kb: failed to ingest %s", fpath)
                        result.failed_files.append((fpath, str(exc)))

        return result
