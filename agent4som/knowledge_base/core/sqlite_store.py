from __future__ import annotations

import sqlite3
from datetime import date
import contextlib


class SqliteStore:
    """SQLite-backed persistence for quota counters and file versioning metadata.

    Replaces the in-memory dicts originally used in ChromaRepository.
    Data persists across restarts.
    """

    def __init__(self, db_path: str):
        # timeout=30：网关与同步脚本会并发写 quota.db，默认 5s 易触发
        # "database is locked" → 元数据静默丢失（有向量无元数据）。WAL + 30s 让步。
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._migrate()
        self._init_tables()

    def _migrate(self):
        """Add columns/tables missing from older schema versions."""
        with contextlib.suppress(sqlite3.OperationalError):  # column already exists
            self._conn.execute(
                "ALTER TABLE file_metadata ADD COLUMN file_hash TEXT NOT NULL DEFAULT ''"
            )
        with contextlib.suppress(sqlite3.OperationalError):
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_metadata_file_hash ON file_metadata(file_hash)"
            )

    def _init_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS file_metadata (
                user_id     TEXT NOT NULL,
                filename    TEXT NOT NULL,
                scope       TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                file_hash   TEXT NOT NULL DEFAULT '',
                ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, filename, scope)
            );
            CREATE TABLE IF NOT EXISTS daily_uploads (
                user_id     TEXT NOT NULL,
                upload_date TEXT NOT NULL,
                count       INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (user_id, upload_date)
            );
            CREATE TABLE IF NOT EXISTS upload_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_upload_log_user_time
                ON upload_log(user_id, uploaded_at);
        """)
        self._conn.commit()

    # ── file count ──────────────────────────────────────────────────

    def get_user_file_count(self, user_id: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM file_metadata WHERE user_id = ?", (user_id,)
        )
        return cur.fetchone()[0]

    # ── daily uploads ───────────────────────────────────────────────

    def get_user_daily_upload_count(self, user_id: str) -> int:
        today = date.today().isoformat()
        cur = self._conn.execute(
            "SELECT count FROM daily_uploads WHERE user_id = ? AND upload_date = ?",
            (user_id, today),
        )
        row = cur.fetchone()
        return row[0] if row else 0

    def _increment_daily_upload(self, user_id: str) -> None:
        today = date.today().isoformat()
        self._conn.execute(
            "INSERT INTO daily_uploads (user_id, upload_date, count) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, upload_date) DO UPDATE SET count = count + 1",
            (user_id, today),
        )
        self._conn.commit()

    # ── file versioning ─────────────────────────────────────────────

    def get_file_metadata(self, user_id: str, filename: str, scope: str = "") -> dict[str, str | None]:
        if scope:
            cur = self._conn.execute(
                "SELECT content_hash FROM file_metadata WHERE user_id = ? AND filename = ? AND scope = ?",
                (user_id, filename, scope),
            )
        else:
            cur = self._conn.execute(
                "SELECT content_hash FROM file_metadata WHERE user_id = ? AND filename = ?",
                (user_id, filename),
            )
        row = cur.fetchone()
        return {"content_hash": row[0]} if row else None

    def get_file_metadata_any_user(self, filename: str, scope: str) -> dict[str, str | None]:
        """Check if *filename* exists in *scope* for ANY user.

        Used for shared scopes (global, teachers) where cross-user dedup matters.
        """
        row = self._conn.execute(
            "SELECT content_hash FROM file_metadata WHERE filename = ? AND scope = ? LIMIT 1",
            (filename, scope),
        ).fetchone()
        return {"content_hash": row[0]} if row else None

    def list_file_metadata_by_scope(self, scope: str, order: str = "asc") -> list[dict[str, str]]:
        """Return every metadata row in *scope*, ordered by filename.

        ``order``: ``"asc"``（默认）或 ``"desc"``——白名单取值，不接受任意 SQL。

        Read-only.  Used by the list endpoint and — filtered by ``filename`` —
        to enumerate all uploader rows of one file (delete preview, and the
        per-row delete in the delete path).
        """
        direction = "DESC" if order == "desc" else "ASC"
        rows = self._conn.execute(
            "SELECT user_id, filename, scope, content_hash, file_hash, ingested_at "
            f"FROM file_metadata WHERE scope = ? ORDER BY filename {direction}",
            (scope,),
        ).fetchall()
        return [
            {"user_id": r[0], "filename": r[1], "scope": r[2],
             "content_hash": r[3], "file_hash": r[4], "ingested_at": r[5]}
            for r in rows
        ]

    def get_file_metadata_by_hash(self, file_hash: str, scope: str = "") -> dict[str, str | None]:
        """Check if *file_hash* exists in file_metadata, optionally scoped."""
        if scope:
            row = self._conn.execute(
                "SELECT content_hash, file_hash FROM file_metadata "
                "WHERE file_hash = ? AND file_hash != '' AND scope = ? LIMIT 1",
                (file_hash, scope),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT content_hash, file_hash FROM file_metadata "
                "WHERE file_hash = ? AND file_hash != '' LIMIT 1",
                (file_hash,),
            ).fetchone()
        return {"content_hash": row[0], "file_hash": row[1]} if row else None

    def set_file_metadata(self, user_id: str, filename: str, metadata: dict[str, str]) -> None:
        scope = metadata.get("scope", "")
        # Check if this is a NEW record or REPLACE to avoid counting
        # re-ingests against the daily upload quota.
        existing = self._conn.execute(
            "SELECT 1 FROM file_metadata WHERE user_id = ? AND filename = ? AND scope = ?",
            (user_id, filename, scope),
        ).fetchone()
        is_new = existing is None

        self._conn.execute(
            "INSERT OR REPLACE INTO file_metadata "
            "(user_id, filename, scope, content_hash, file_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, filename, scope,
             metadata.get("content_hash", ""),
             metadata.get("file_hash", "")),
        )
        self._conn.commit()
        if is_new:
            self._increment_daily_upload(user_id)

    # ── per-minute rate limiting ─────────────────────────────────────

    def get_user_recent_upload_count(self, user_id: str, window_seconds: int = 60) -> int:
        """Return how many uploads *user_id* has made in the last *window_seconds*."""
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM upload_log "
            "WHERE user_id = ? AND uploaded_at > datetime('now', ?)",
            (user_id, f"-{window_seconds} seconds"),
        )
        return cur.fetchone()[0]

    def record_upload(self, user_id: str) -> None:
        """Record an upload event for per-minute rate limiting."""
        self._conn.execute(
            "INSERT INTO upload_log (user_id) VALUES (?)",
            (user_id,),
        )
        # Prune entries older than 1 hour to keep table small
        self._conn.execute(
            "DELETE FROM upload_log WHERE uploaded_at < datetime('now', '-1 hour')"
        )
        self._conn.commit()

    # ── single-file metadata cleanup ────────────────────────────────

    def delete_file_metadata(self, user_id: str, filename: str, scope: str) -> None:
        """Delete metadata for a single file (used by orchestrator orphan cleanup).

        Replaces direct ``_conn.execute()`` calls from the orchestrator,
        keeping SQLite access encapsulated inside SqliteStore.
        """
        self._conn.execute(
            "DELETE FROM file_metadata WHERE user_id=? AND filename=? AND scope=?",
            (user_id, filename, scope),
        )
        self._conn.commit()

    # ── user data cleanup (cascade delete) ──────────────────────────

    def delete_user_data(self, user_id: str) -> None:
        self._conn.execute("DELETE FROM file_metadata WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM daily_uploads WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM upload_log WHERE user_id = ?", (user_id,))
        self._conn.commit()

    def close(self):
        self._conn.close()
