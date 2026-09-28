from enum import Enum
from typing import Any

class IngestAction(Enum):
    NEW = "new"
    SKIP = "skip"
    REPLACE = "replace"

# Scopes that are shared across users — dedup must ignore user_id
_SHARED_SCOPES = frozenset({"global", "teachers"})

class VersionManager:
    def __init__(self, repo: Any):
        self.repo = repo

    def determine_ingest_action(self, user_id: str, filename: str, content_hash: str, scope: str = "") -> IngestAction:
        existing_meta = self.repo.get_file_metadata(user_id, filename, scope)

        # For shared scopes, also check across all users (different users may
        # ingest the same file to global/teachers, and the per-user metadata
        # lookup would miss the existing entry).
        if not existing_meta and scope in _SHARED_SCOPES:
            existing_meta = self.repo.get_file_metadata_any_user(filename, scope)

        if not existing_meta:
            return IngestAction.NEW

        if existing_meta.get("content_hash") == content_hash:
            return IngestAction.SKIP

        return IngestAction.REPLACE
