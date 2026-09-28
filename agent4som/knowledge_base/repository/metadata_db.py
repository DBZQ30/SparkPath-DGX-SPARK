"""SQLite DatabaseManager：连接管理与幂等 schema 初始化（原 sqlite_metadata.py 拆分）。"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


DEFAULT_DB_NAME = "miniapp.db"


def resolve_sqlite_db_path(raw_path: str | os.PathLike[str] | None) -> Path:
    """Resolve config ``knowledge_base.sqlite_path`` to a concrete DB file."""
    raw_value = str(raw_path or "data/sqlite/").strip()
    if "AGENT4SOM_REPO" in raw_value:
        repo_root = os.getenv("AGENT4SOM_REPO") or str(Path(__file__).resolve().parents[2])
        raw_value = raw_value.replace("${AGENT4SOM_REPO}", repo_root).replace("$AGENT4SOM_REPO", repo_root)
    value = os.path.expandvars(raw_value).strip()
    path = Path(value).expanduser()
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return path
    return path / DEFAULT_DB_NAME


class DatabaseManager:
    """Small SQLite manager with idempotent schema initialization."""

    def __init__(self, db_path: str | os.PathLike[str]):
        self.db_path = Path(db_path).expanduser()

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            _ensure_column(conn, "admission_profiles", "name", "TEXT")
            _ensure_column(conn, "admission_profiles", "gender", "TEXT")
            _ensure_column(conn, "admission_profiles", "student_auth_intent", "TEXT")
            _ensure_column(conn, "conversation_sessions", "pending_confirmation_json", "TEXT")
            _ensure_column(conn, "user_profiles", "review_note", "TEXT")
            _ensure_column(conn, "admission_turn_contexts", "message_id", "TEXT")
            _ensure_column(conn, "admission_turn_contexts", "profile_file_paths_json", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_column(conn, "admission_turn_contexts", "knowledge_file_paths_json", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_column(conn, "pending_knowledge_uploads", "requested_scope", "TEXT")
            _ensure_column(conn, "human_handoff_messages", "locate_left", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "human_handoff_messages", "stu_read", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "human_handoff_messages", "tea_read", "INTEGER NOT NULL DEFAULT 0")
            _ensure_phone_whitelist_batch_schema(conn)
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_admission_turn_contexts_trace
                   ON admission_turn_contexts(trace_id)"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_admission_turn_contexts_message
                   ON admission_turn_contexts(platform, user_id, message_id)"""
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, description)
                VALUES (?, ?)
                """,
                (1, "mba admission p0 schema"),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, description)
                VALUES (?, ?)
                """,
                (2, "teacher authentication review and management audit"),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, description)
                VALUES (?, ?)
                """,
                (3, "student authentication intent and review workflow"),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, description)
                VALUES (?, ?)
                """,
                (4, "persistent management review context"),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, description)
                VALUES (?, ?)
                """,
                (5, "administrator authentication review workflow"),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, description)
                VALUES (?, ?)
                """,
                (6, "human handoff request workflow"),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, description)
                VALUES (?, ?)
                """,
                (7, "native agent turn context binding"),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, description)
                VALUES (?, ?)
                """,
                (8, "stable native turn identity and trusted profile attachments"),
            )


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS welcome_sent_log (
    platform TEXT NOT NULL DEFAULT 'wecom',
    user_id TEXT NOT NULL,
    welcome_type TEXT NOT NULL DEFAULT 'guest',
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, user_id)
);

CREATE TABLE IF NOT EXISTS lead_contact_state (
    platform TEXT NOT NULL DEFAULT 'wecom',
    user_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    contact_count INTEGER NOT NULL DEFAULT 0,
    last_contact_type TEXT,
    last_contact_at TEXT,
    next_followup_at TEXT,
    opted_out INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, user_id)
);

CREATE TABLE IF NOT EXISTS lead_candidates (
    platform TEXT NOT NULL DEFAULT 'wecom',
    user_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'active',
    raw_json TEXT NOT NULL DEFAULT '{}',
    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, user_id)
);

-- 旧表已迁移至 _old_teacher_auth_requests / _old_admin_auth_requests / _old_student_auth_requests
-- CREATE TABLE 语句已移除，仅保留审计参考

-- Mini-program identity and authentication state.  This remains part of the
-- miniapp schema even after the three legacy auth-request tables were
-- retired; fresh installations must be able to initialize it from scratch.
CREATE TABLE IF NOT EXISTS user_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL DEFAULT 'miniapp',
    user_id TEXT NOT NULL,
    username TEXT,
    name TEXT,
    staff_id TEXT,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'guest',
    raw_json TEXT NOT NULL DEFAULT '{}',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform, user_id)
);

-- Student authentication is still a separate admission workflow.  Teacher
-- and administrator requests use user_profiles, but the admission service
-- continues to persist and review student applications through this table.
CREATE TABLE IF NOT EXISTS student_auth_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL DEFAULT 'wecom',
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    profile_snapshot_json TEXT NOT NULL DEFAULT '{}',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS management_review_context (
    platform TEXT NOT NULL DEFAULT 'wecom',
    reviewer_user_id TEXT NOT NULL,
    request_type TEXT NOT NULL,
    request_id INTEGER NOT NULL,
    pending_action TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, reviewer_user_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL DEFAULT 'wecom',
    operator_id TEXT NOT NULL,
    operator_role TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    result TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS proactive_contact_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL DEFAULT 'wecom',
    user_id TEXT NOT NULL,
    contact_type TEXT NOT NULL,
    message TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    delivery_status TEXT NOT NULL DEFAULT 'planned',
    error TEXT
);

CREATE TABLE IF NOT EXISTS human_handoff_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL DEFAULT 'wecom',
    user_id TEXT NOT NULL,
    question TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    assigned_to TEXT,
    answered_by TEXT,
    answer_text TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS human_handoff_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    message_type TEXT NOT NULL,
    message_text TEXT NOT NULL,
    locate_left INTEGER NOT NULL DEFAULT 0,
    stu_read INTEGER NOT NULL DEFAULT 0,
    tea_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(request_id) REFERENCES human_handoff_requests(id)
);

CREATE TABLE IF NOT EXISTS conversation_sessions (
    platform TEXT NOT NULL DEFAULT 'wecom',
    user_id TEXT NOT NULL,
    session_id TEXT,
    current_stage TEXT NOT NULL DEFAULT 'new',
    current_topic TEXT,
    collected_fields_json TEXT NOT NULL DEFAULT '{}',
    refused_fields_json TEXT NOT NULL DEFAULT '[]',
    pending_confirmation_json TEXT NOT NULL DEFAULT '{}',
    attempts_in_round INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, user_id)
);

CREATE TABLE IF NOT EXISTS admission_turn_contexts (
    token_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    session_id TEXT,
    trace_id TEXT NOT NULL,
    message_id TEXT,
    original_text TEXT NOT NULL,
    profile_file_paths_json TEXT NOT NULL DEFAULT '[]',
    knowledge_file_paths_json TEXT NOT NULL DEFAULT '[]',
    pending_confirmation_json TEXT NOT NULL DEFAULT '{}',
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_admission_turn_contexts_expiry
ON admission_turn_contexts(expires_at);

CREATE TABLE IF NOT EXISTS pending_knowledge_uploads (
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT,
    trace_id TEXT NOT NULL,
    message_id TEXT,
    original_text TEXT NOT NULL DEFAULT '',
    file_paths_json TEXT NOT NULL DEFAULT '[]',
    requested_scope TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, user_id)
);

CREATE INDEX IF NOT EXISTS idx_pending_knowledge_uploads_expiry
ON pending_knowledge_uploads(expires_at);

CREATE TABLE IF NOT EXISTS admission_profiles (
    platform TEXT NOT NULL DEFAULT 'wecom',
    user_id TEXT NOT NULL,
    name TEXT,
    phone TEXT,
    gender TEXT,
    age INTEGER,
    company TEXT,
    position TEXT,
    project_experience TEXT,
    undergraduate_school TEXT,
    undergraduate_major TEXT,
    highest_degree TEXT,
    mgmt_knowledge_base TEXT,
    learning_experience TEXT,
    student_auth_intent TEXT,
    source_file TEXT,
    collection_status TEXT NOT NULL DEFAULT 'none',
    status TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, user_id)
);
CREATE TABLE IF NOT EXISTS phone_whitelist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL DEFAULT 'manual',
    phone TEXT NOT NULL,
    staff_id TEXT,
    name TEXT,
    role TEXT NOT NULL DEFAULT 'student',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (phone, batch_id)
);
CREATE TABLE IF NOT EXISTS phone_whitelist_batch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

def _ensure_phone_whitelist_batch_schema(conn: sqlite3.Connection) -> None:
    """Rebuild the pre-batch ``phone_whitelist`` (PK = phone) into an entry table.

    Old rows become the ``manual`` batch so single adds keep working unchanged.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(phone_whitelist)")}
    if not existing or "id" in existing:
        return
    conn.executescript(
        """
        ALTER TABLE phone_whitelist RENAME TO phone_whitelist_legacy;
        CREATE TABLE phone_whitelist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL DEFAULT 'manual',
            phone TEXT NOT NULL,
            staff_id TEXT,
            name TEXT,
            role TEXT NOT NULL DEFAULT 'student',
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (phone, batch_id)
        );
        INSERT INTO phone_whitelist (batch_id, phone, staff_id, name, role, note, created_at, updated_at)
            SELECT 'manual', phone, staff_id, name, role, note, created_at, updated_at
            FROM phone_whitelist_legacy;
        DROP TABLE phone_whitelist_legacy;
        """
    )
