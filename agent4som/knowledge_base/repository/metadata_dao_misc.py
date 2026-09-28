"""会话/审计/审核上下文/待入库上传/招生档案 DAO（原 sqlite_metadata.py 拆分）。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import fields
from typing import Any, ClassVar, Dict, Iterable, Optional

from knowledge_base.repository.metadata_db import DatabaseManager
from knowledge_base.repository.metadata_models import (
    ConversationSession,
    PendingKnowledgeUpload,
    AdmissionProfile,
    ManagementReviewContext,
    AuditLogEntry,
)


class ManagementReviewContextDAO:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.db.initialize()

    def get(self, platform: str, reviewer_user_id: str) -> Optional[ManagementReviewContext]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM management_review_context
                WHERE platform=? AND reviewer_user_id=?
                """,
                (platform, reviewer_user_id),
            ).fetchone()
        if not row:
            return None
        return ManagementReviewContext(
            platform=row["platform"],
            reviewer_user_id=row["reviewer_user_id"],
            request_type=row["request_type"],
            request_id=int(row["request_id"]),
            pending_action=row["pending_action"],
            updated_at=row["updated_at"],
        )

    def set(
        self,
        platform: str,
        reviewer_user_id: str,
        *,
        request_type: str,
        request_id: int,
        pending_action: Optional[str] = None,
    ) -> ManagementReviewContext:
        if request_type not in {"teacher", "student"}:
            raise ValueError(f"unsupported review request type: {request_type}")
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO management_review_context(
                    platform, reviewer_user_id, request_type, request_id, pending_action
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform, reviewer_user_id) DO UPDATE SET
                    request_type=excluded.request_type,
                    request_id=excluded.request_id,
                    pending_action=excluded.pending_action,
                    updated_at=datetime('now')
                """,
                (platform, reviewer_user_id, request_type, request_id, pending_action),
            )
        return self.get(platform, reviewer_user_id) or ManagementReviewContext(
            platform,
            reviewer_user_id,
            request_type,
            request_id,
            pending_action,
        )

    def clear(self, platform: str, reviewer_user_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                DELETE FROM management_review_context
                WHERE platform=? AND reviewer_user_id=?
                """,
                (platform, reviewer_user_id),
            )




class AuditLogDAO:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.db.initialize()

    def record(
        self,
        platform: str,
        operator_id: str,
        operator_role: str,
        action: str,
        target_type: str,
        *,
        target_id: Optional[str] = None,
        result: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditLogEntry:
        detail_json = json.dumps(detail or {}, ensure_ascii=False)
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO audit_log(
                    platform, operator_id, operator_role, action,
                    target_type, target_id, result, detail_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    platform,
                    operator_id,
                    operator_role,
                    action,
                    target_type,
                    target_id,
                    result,
                    detail_json,
                ),
            )
            audit_id = int(cursor.lastrowid)
        return self.get_by_id(audit_id) or AuditLogEntry(
            audit_id,
            platform,
            operator_id,
            operator_role,
            action,
            target_type,
            target_id,
            result,
            detail_json,
        )

    def get_by_id(self, audit_id: int) -> Optional[AuditLogEntry]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM audit_log WHERE id=?",
                (audit_id,),
            ).fetchone()
        return _audit_log_from_row(row) if row else None

    def list_recent(self, limit: int = 50) -> list[AuditLogEntry]:
        safe_limit = max(1, min(int(limit), 500))
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [_audit_log_from_row(row) for row in rows]


class ConversationSessionDAO:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.db.initialize()

    def get(self, platform: str, user_id: str) -> Optional[ConversationSession]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_sessions WHERE platform=? AND user_id=?",
                (platform, user_id),
            ).fetchone()
        return _session_from_row(row) if row else None

    def upsert(
        self,
        platform: str,
        user_id: str,
        *,
        session_id: Optional[str] = None,
        current_stage: str = "new",
        current_topic: Optional[str] = None,
        collected_fields: Optional[Dict[str, Any]] = None,
        refused_fields: Optional[Iterable[str]] = None,
        pending_confirmation: Optional[Dict[str, Any]] = None,
        attempts_in_round: int = 0,
        summary: Optional[str] = None,
    ) -> ConversationSession:
        collected_json = json.dumps(collected_fields or {}, ensure_ascii=False)
        refused_json = json.dumps(list(refused_fields or []), ensure_ascii=False)
        pending_json = json.dumps(pending_confirmation or {}, ensure_ascii=False)
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_sessions(
                    platform, user_id, session_id, current_stage, current_topic,
                    collected_fields_json, refused_fields_json, pending_confirmation_json,
                    attempts_in_round, summary
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    session_id=excluded.session_id,
                    current_stage=excluded.current_stage,
                    current_topic=excluded.current_topic,
                    collected_fields_json=excluded.collected_fields_json,
                    refused_fields_json=excluded.refused_fields_json,
                    pending_confirmation_json=excluded.pending_confirmation_json,
                    attempts_in_round=excluded.attempts_in_round,
                    summary=excluded.summary,
                    updated_at=datetime('now')
                """,
                (
                    platform,
                    user_id,
                    session_id,
                    current_stage,
                    current_topic,
                    collected_json,
                    refused_json,
                    pending_json,
                    attempts_in_round,
                    summary,
                ),
            )
        return self.get(platform, user_id) or ConversationSession(platform, user_id)


class PendingKnowledgeUploadDAO:
    """Short-lived server-bound attachments awaiting a KB scope choice."""

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.db.initialize()

    def get(self, platform: str, user_id: str) -> Optional[PendingKnowledgeUpload]:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM pending_knowledge_uploads WHERE expires_at < datetime('now')")
            row = conn.execute(
                "SELECT * FROM pending_knowledge_uploads WHERE platform=? AND user_id=?",
                (platform, user_id),
            ).fetchone()
        return _pending_knowledge_upload_from_row(row) if row else None

    def upsert(
        self,
        platform: str,
        user_id: str,
        *,
        file_paths: Iterable[str],
        trace_id: str,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        original_text: str = "",
        requested_scope: Optional[str] = None,
        ttl_minutes: int = 30,
    ) -> PendingKnowledgeUpload:
        paths = [str(item) for item in file_paths if str(item)]
        ttl = max(1, min(int(ttl_minutes or 30), 24 * 60))
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO pending_knowledge_uploads(
                    platform, user_id, session_id, trace_id, message_id,
                    original_text, file_paths_json, requested_scope, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', ?))
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    session_id=excluded.session_id,
                    trace_id=excluded.trace_id,
                    message_id=excluded.message_id,
                    original_text=excluded.original_text,
                    file_paths_json=excluded.file_paths_json,
                    requested_scope=excluded.requested_scope,
                    expires_at=excluded.expires_at,
                    updated_at=datetime('now')
                """,
                (
                    platform, user_id, session_id, trace_id, message_id,
                    original_text, json.dumps(paths, ensure_ascii=False), requested_scope,
                    f"+{ttl} minutes",
                ),
            )
        pending = self.get(platform, user_id)
        if pending is None:
            raise RuntimeError("failed to persist pending knowledge upload")
        return pending

    def delete(self, platform: str, user_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM pending_knowledge_uploads WHERE platform=? AND user_id=?",
                (platform, user_id),
            )


class AdmissionProfileDAO:
    FIELDS: ClassVar[set[str]] = {
        "name",
        "phone",
        "gender",
        "age",
        "company",
        "position",
        "project_experience",
        "undergraduate_school",
        "undergraduate_major",
        "highest_degree",
        "mgmt_knowledge_base",
        "learning_experience",
        "student_auth_intent",
        "source_file",
        "collection_status",
        "status",
    }

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.db.initialize()

    def get(self, platform: str, user_id: str) -> Optional[AdmissionProfile]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM admission_profiles WHERE platform=? AND user_id=?",
                (platform, user_id),
            ).fetchone()
        return _profile_from_row(row) if row else None

    def find_by_user_or_name(self, platform: str, target: str, limit: int = 5) -> list[AdmissionProfile]:
        query = str(target or "").strip()
        if not query:
            return []
        safe_limit = max(1, min(int(limit or 5), 20))
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM admission_profiles
                WHERE platform=?
                  AND (user_id=? OR name=? OR name LIKE ?)
                ORDER BY
                  CASE
                    WHEN user_id=? THEN 0
                    WHEN name=? THEN 1
                    ELSE 2
                  END,
                  updated_at DESC,
                  user_id ASC
                LIMIT ?
                """,
                (platform, query, query, f"%{query}%", query, query, safe_limit),
            ).fetchall()
        return [_profile_from_row(row) for row in rows]

    def upsert_fields(
        self,
        platform: str,
        user_id: str,
        values: Dict[str, Any],
    ) -> AdmissionProfile:
        clean = {k: v for k, v in values.items() if k in self.FIELDS and v not in (None, "")}
        columns = ["platform", "user_id", *clean.keys()]
        params = [platform, user_id, *clean.values()]
        update_columns = [k for k in clean if k not in {"platform", "user_id"}]
        if update_columns:
            update_sql = ", ".join(f"{k}=excluded.{k}" for k in update_columns)
            update_sql += ", updated_at=datetime('now')"
        else:
            update_sql = "updated_at=datetime('now')"
        placeholders = ", ".join("?" for _ in columns)
        with self.db.connect() as conn:
            conn.execute(
                f"""
                INSERT INTO admission_profiles({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(platform, user_id) DO UPDATE SET {update_sql}
                """,
                params,
            )
        return self.get(platform, user_id) or AdmissionProfile(platform, user_id)

    def delete(self, platform: str, user_id: str) -> bool:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM admission_profiles WHERE platform=? AND user_id=?",
                (platform, user_id),
            )
            return cursor.rowcount > 0


def _session_from_row(row: sqlite3.Row) -> ConversationSession:
    names = {field.name for field in fields(ConversationSession)}
    return ConversationSession(**{k: row[k] for k in row.keys() if k in names})


def _pending_knowledge_upload_from_row(row: sqlite3.Row) -> PendingKnowledgeUpload:
    names = {field.name for field in fields(PendingKnowledgeUpload)}
    return PendingKnowledgeUpload(**{key: row[key] for key in row.keys() if key in names})


def _profile_from_row(row: sqlite3.Row) -> AdmissionProfile:
    names = {field.name for field in fields(AdmissionProfile)}
    return AdmissionProfile(**{k: row[k] for k in row.keys() if k in names})


def _audit_log_from_row(row: sqlite3.Row) -> AuditLogEntry:
    names = {field.name for field in fields(AuditLogEntry)}
    return AuditLogEntry(**{k: row[k] for k in row.keys() if k in names})
