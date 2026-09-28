from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS file_metadata (
    user_id   TEXT NOT NULL,
    filename  TEXT NOT NULL,
    metadata  TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, filename)
);
"""


class ChromaMetadataDAO:
    """SQLite-backed metadata store for ingested file information.

    Used by ``ChromaRepository`` to persist per-file metadata such as
    content hashes, versions, and ingestion timestamps.  Each row is
    keyed by ``(user_id, filename)``.
    """

    def __init__(self, db_path: str):
        self._db_path = str(Path(db_path).resolve())
        self._conn: Optional[sqlite3.Connection] = None

    # ── lifecycle ──────────────────────────────────────────────────────

    def initialize(self) -> None:
        """Create the database file and table if they do not exist."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute(CREATE_TABLE_SQL)
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    # ── CRUD ───────────────────────────────────────────────────────────

    def get(self, user_id: str, filename: str) -> Optional[dict]:
        """Return the metadata dict for *filename*, or ``None``."""
        conn = self._connect()
        row = conn.execute(
            "SELECT metadata FROM file_metadata WHERE user_id=? AND filename=?",
            (user_id, filename),
        ).fetchone()
        if row is None:
            return None
        raw = row["metadata"]
        if isinstance(raw, str):
            return json.loads(raw)
        return raw

    def upsert(self, user_id: str, filename: str, metadata: dict) -> None:
        """Insert or replace metadata for *filename*."""
        conn = self._connect()
        raw = json.dumps(metadata, ensure_ascii=False)
        conn.execute(
            """INSERT INTO file_metadata (user_id, filename, metadata, created_at, updated_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))
               ON CONFLICT(user_id, filename) DO UPDATE SET
                   metadata=excluded.metadata,
                   updated_at=datetime('now')""",
            (user_id, filename, raw),
        )
        conn.commit()

    def delete(self, user_id: str, filename: str) -> bool:
        """Remove the metadata entry for *filename*.

        Returns ``True`` if a row was deleted, ``False`` otherwise.
        """
        conn = self._connect()
        cursor = conn.execute(
            "DELETE FROM file_metadata WHERE user_id=? AND filename=?",
            (user_id, filename),
        )
        conn.commit()
        return cursor.rowcount > 0
