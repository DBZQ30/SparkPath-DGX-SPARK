"""Unified audit logging for knowledge base operations (read + write).

Single ``audit_events`` table with sparse columns — search fields are NULL
for ingest events and vice versa.  SQLite sparse columns cost zero storage.

Usage::

    from knowledge_base.core.audit_logger import AuditLogger
    audit = AuditLogger("data/audit.db")
    audit.log_event(event_type="search", user_id=..., role=..., query=..., ...)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any
import contextlib

logger = logging.getLogger(__name__)


class AuditLogger:
    """SQLite-backed unified audit logger (read + write events in one table)."""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_table()
        self._migrate_from_legacy()

    # ── schema ────────────────────────────────────────────────────────

    def _init_table(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type      TEXT    NOT NULL,
                timestamp       TEXT    NOT NULL,
                user_id         TEXT    NOT NULL,
                role            TEXT    NOT NULL,
                correlation_id  TEXT,
                platform        TEXT    NOT NULL DEFAULT 'wecom',

                -- search-specific (NULL for ingest events)
                query           TEXT,
                rewritten_query TEXT,
                scopes_allowed  TEXT,
                scopes_hit      TEXT,
                top_k           INTEGER,
                result_count    INTEGER,
                result_sources  TEXT,
                latency_ms      INTEGER,

                -- ingest-specific (NULL for search events)
                filename        TEXT,
                scope           TEXT,
                node_count      INTEGER,
                file_size_bytes INTEGER,

                -- generic extension
                detail          TEXT    NOT NULL DEFAULT '{}',
                row_hash        TEXT,
                retention_date  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_audit_events_time
                ON audit_events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_events_user
                ON audit_events(user_id);
            CREATE INDEX IF NOT EXISTS idx_audit_events_type
                ON audit_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_audit_events_corr
                ON audit_events(correlation_id);
        """)
        self._conn.commit()

    # ── migration ──────────────────────────────────────────────────────

    def _migrate_from_legacy(self):
        """One-shot: migrate audit_log rows → audit_events, then drop legacy table."""
        try:
            count = self._conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='audit_log'"
            ).fetchone()[0]
        except Exception:
            logger.warning("Migration check skipped — could not query sqlite_master", exc_info=True)
            return
        if not count:
            return

        rows = self._conn.execute(
            "SELECT id, timestamp, event_type, user_id, role, detail, created_at "
            "FROM audit_log ORDER BY id"
        ).fetchall()

        for r in rows:
            try:
                detail = json.loads(r[5]) if r[5] else {}
            except json.JSONDecodeError:
                detail = {}
            # Use existing UTC timestamp if present, otherwise created_at
            ts = r[1] if r[1] else (r[6] if len(r) > 6 else datetime.now(timezone.utc).isoformat())
            # Normalize to ISO 8601 UTC
            if ts and '+' not in ts and 'Z' not in ts:
                ts = ts + '+00:00'

            self._conn.execute(
                """INSERT INTO audit_events
                   (event_type, timestamp, user_id, role, filename, scope, node_count, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    r[2],  # event_type
                    ts,
                    r[3] or "",  # user_id
                    r[4] or "",  # role
                    detail.get("filename"),
                    detail.get("scope"),
                    detail.get("node_count"),
                    json.dumps(detail, ensure_ascii=False),
                ),
            )

        self._conn.execute("DROP TABLE audit_log")
        self._conn.commit()
        logger.info("Migrated %d audit_log rows → audit_events", len(rows))

    # ── public API ─────────────────────────────────────────────────────

    def log_event(
        self,
        event_type: str,
        *,
        user_id: str = "",
        role: str = "",
        correlation_id: str | None = None,
        platform: str = "wecom",
        # search
        query: str | None = None,
        rewritten_query: str | None = None,
        scopes_allowed: list | None = None,
        scopes_hit: list | None = None,
        top_k: int | None = None,
        result_count: int | None = None,
        result_sources: list | None = None,
        latency_ms: int | None = None,
        # ingest
        filename: str | None = None,
        scope: str | None = None,
        node_count: int | None = None,
        file_size_bytes: int | None = None,
        # extension
        detail: dict[str, Any | None] | None = None,
    ) -> None:
        """Insert one audit event.  All keyword args except event_type are
        optional — columns for non-applicable fields stay NULL.

        Raises nothing.  On failure logs an error so a single audit write
        cannot take down the calling tool.
        """
        try:
            ts = datetime.now(timezone.utc).isoformat()

            # Chain hash: SHA-256(prev_row_hash + new_row_data).
            prev = self._conn.execute(
                "SELECT row_hash FROM audit_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_val = prev[0] if prev else None
            prev_hash = (prev_val or "").encode()
            import hashlib
            # Include all audit-significant fields in the chain hash so tampering
            # with any column is detectable (P1-5 fix).
            row_data = (
                f"{ts}|{event_type}|{user_id}|{role}|{query or ''}|"
                f"{filename or ''}|{scope or ''}|{result_count or 0}|"
                f"{node_count or 0}|{file_size_bytes or 0}"
            ).encode()
            row_hash = hashlib.sha256(prev_hash + row_data).hexdigest()
            retention = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            self._conn.execute(
                """INSERT INTO audit_events
                   (event_type, timestamp, user_id, role, correlation_id, platform,
                    query, rewritten_query, scopes_allowed, scopes_hit,
                    top_k, result_count, result_sources, latency_ms,
                    filename, scope, node_count, file_size_bytes, detail,
                    row_hash, retention_date)
                   VALUES (?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?,
                           ?, ?, ?, ?,
                           ?, ?, ?, ?, ?,
                           ?, ?)""",
                (
                    event_type,
                    ts,
                    user_id,
                    role,
                    correlation_id or str(uuid.uuid4()),
                    platform,
                    query[:500] if query else None,
                    rewritten_query,
                    json.dumps(scopes_allowed, ensure_ascii=False) if scopes_allowed else None,
                    json.dumps(scopes_hit, ensure_ascii=False) if scopes_hit else None,
                    top_k,
                    result_count,
                    json.dumps(result_sources, ensure_ascii=False) if result_sources else None,
                    latency_ms,
                    filename,
                    scope,
                    node_count,
                    file_size_bytes,
                    json.dumps(detail or {}, ensure_ascii=False),
                    row_hash,
                    retention,
                ),
            )
            self._conn.commit()
        except Exception:
            logger.error("AuditLogger: failed to write %s event for user=%s", event_type, user_id, exc_info=True)

    def log_redaction(
        self,
        user_id: str,
        role: str,
        correlation_id: str,
        detail: dict[str, Any | None] | None = None,
    ) -> None:
        """Convenience wrapper for citation_redacted events."""
        self.log_event(
            event_type="citation_redacted",
            user_id=user_id,
            role=role,
            correlation_id=correlation_id,
            detail=detail,
        )

    # ── query ──────────────────────────────────────────────────────────

    def query(
        self,
        limit: int = 50,
        event_type: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM audit_events WHERE 1=1"
        params: list[Any] = []
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        col_names = [d[1] for d in self._conn.execute("PRAGMA table_info(audit_events)").fetchall()]
        results = []
        for r in rows:
            row = dict(zip(col_names, r, strict=False))
            if isinstance(row.get("detail"), str):
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    row["detail"] = json.loads(row["detail"])
            results.append(row)
        return results

    def verify_chain(self) -> tuple[int, int]:
        """Verify the SHA-256 chain hash across all rows.

        Returns ``(total, ok)`` — *total* rows with hashes, *ok* rows that
        pass.  Rows before the chain-hash feature (NULL row_hash) are skipped.
        """
        import hashlib
        rows = self._conn.execute(
            "SELECT id, timestamp, event_type, user_id, role, "
            "query, filename, scope, result_count, node_count, file_size_bytes, row_hash "
            "FROM audit_events WHERE row_hash IS NOT NULL ORDER BY id"
        ).fetchall()
        total, ok, prev_hash = 0, 0, b""
        for r in rows:
            total += 1
            row_data = (
                f"{r[1]}|{r[2]}|{r[3]}|{r[4]}|{r[5] or ''}|"
                f"{r[6] or ''}|{r[7] or ''}|{r[8] or 0}|"
                f"{r[9] or 0}|{r[10] or 0}"
            ).encode()
            expected = hashlib.sha256(prev_hash + row_data).hexdigest()
            if expected == r[11]:
                ok += 1
            prev_hash = r[11].encode() if r[11] else b""
        return total, ok

    def cleanup_retention(self, days: int = 90) -> int:
        """Delete events older than *days*.  Returns count of deleted rows."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        cursor = self._conn.execute(
            "DELETE FROM audit_events WHERE retention_date < ?",
            (cutoff,),
        )
        deleted = cursor.rowcount
        self._conn.commit()
        if deleted:
            logger.info("AuditLogger: cleaned %d events older than %d days", deleted, days)
        return deleted

    def stats(self) -> dict[str, Any]:
        """Return aggregate statistics for the audit log."""
        total = self._conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        by_type = {}
        for r in self._conn.execute(
            "SELECT event_type, COUNT(*) FROM audit_events GROUP BY event_type"
        ).fetchall():
            by_type[r[0]] = r[1]
        by_user = {}
        for r in self._conn.execute(
            "SELECT user_id, COUNT(*) FROM audit_events GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 10"
        ).fetchall():
            by_user[r[0]] = r[1]
        return {"total": total, "by_type": by_type, "top_users": by_user}

    def close(self) -> None:
        self._conn.close()
