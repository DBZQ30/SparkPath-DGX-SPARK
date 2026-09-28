"""Purge pipeline — remove all knowledge base data for a given user.

Handles:
- Vector cleanup via ``KnowledgeBaseRepository.purge_scope()``
- Physical file cleanup (directory removal)
- Audit logging
- Dry-run mode for safety verification
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from knowledge_base.repository.interfaces import KnowledgeBaseRepository

logger = logging.getLogger(__name__)


class PurgeUserKBPipeline:
    """Scoped cleanup of a single user's knowledge base data.

    Usage::

        pipeline = PurgeUserKBPipeline(
            repo=chroma_repo,
            audit_log=print,  # or a proper audit service
            user_files_root=Path("/data/user_uploads"),
        )
        pipeline.run("student_A")
    """

    def __init__(
        self,
        repo: KnowledgeBaseRepository,
        audit_log: Optional[Callable[[str], None]] = None,
        user_files_root: Optional[Path] = None,
    ):
        self._repo = repo
        self._audit_log = audit_log or logger.warning
        self._user_files_root = user_files_root

    def run(self, user_id: str, dry_run: bool = False) -> dict:
        """Delete all KB data for *user_id*.

        Returns a summary dict with counts / paths of deleted items.
        """
        scope = f"users/{user_id}"
        summary: dict[str, Any] = {
            "user_id": user_id,
            "dry_run": dry_run,
            "vectors_purged": False,
            "files_purged": False,
        }

        # 1. Purge vectors (ChromaDB) + quota/version metadata (SQLite)
        if not dry_run:
            self._repo.purge_scope(scope)
            summary["vectors_purged"] = True
            self._audit_log(f"PURGE: purged vectors for scope={scope}")
            self._repo.delete_user_data(user_id)
            summary["metadata_purged"] = True
            self._audit_log(f"PURGE: purged metadata for user={user_id}")

        # 2. Purge physical files
        file_dir = None
        if self._user_files_root:
            # Prevent path traversal (user_id must be a simple identifier)
            import re as _re
            if not _re.match(r"^[a-zA-Z0-9_.@-]+$", user_id):
                self._audit_log(f"PURGE SKIP: invalid user_id {user_id!r}")
            else:
                file_dir = self._user_files_root / user_id
                if file_dir.is_dir():
                    summary["file_dir"] = str(file_dir)
                    if not dry_run:
                        import shutil
                        shutil.rmtree(file_dir)
                        summary["files_purged"] = True
                        self._audit_log(f"PURGE: removed file dir {file_dir}")

        # 3. Audit (always, even in dry-run)
        self._audit_log(
            f"PURGE user={user_id} dry_run={dry_run} "
            f"vectors={summary['vectors_purged']} files={summary['files_purged']}"
        )

        return summary
