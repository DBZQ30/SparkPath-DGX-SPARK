"""教师/管理员/学生认证请求 DAO 与状态机（原 sqlite_metadata.py 拆分）。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import fields
from typing import Any, Dict, Optional

from knowledge_base.repository.metadata_db import DatabaseManager
from knowledge_base.repository.metadata_models import (
    TeacherAuthRequest,
    AdminAuthRequest,
    StudentAuthRequest,
)


class TeacherAuthRequestDAO:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.db.initialize()

    def create_or_update_pending(
        self,
        platform: str,
        user_id: str,
        *,
        name: str,
        staff_id: str,
        raw: Optional[Dict[str, Any]] = None,
    ) -> TeacherAuthRequest:
        raw_json = json.dumps(raw or {}, ensure_ascii=False)
        with self.db.connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM user_profiles
                WHERE platform=? AND user_id=? AND status='teacher_pending'
                ORDER BY id DESC LIMIT 1
                """,
                (platform, user_id),
            ).fetchone()
            if existing:
                request_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE user_profiles
                    SET name=?, staff_id=?, raw_json=?, updated_at=datetime('now')
                    WHERE id=?
                    """,
                    (name, staff_id, raw_json, request_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO user_profiles(platform, user_id, name, staff_id, status, raw_json)
                    VALUES (?, ?, ?, ?, 'teacher_pending', ?)
                    ON CONFLICT(platform, user_id) DO UPDATE SET
                        name=excluded.name, staff_id=excluded.staff_id,
                        status='teacher_pending',
                        raw_json=excluded.raw_json, updated_at=datetime('now')
                    """,
                    (platform, user_id, name, staff_id, raw_json),
                )
                row = conn.execute(
                    "SELECT id FROM user_profiles WHERE platform=? AND user_id=?",
                    (platform, user_id),
                ).fetchone()
                request_id = int(row["id"])
        return self.get_by_id(request_id) or TeacherAuthRequest(
            request_id, platform, user_id, name, staff_id, "pending", raw_json
        )

    def get_by_id(self, request_id: int) -> Optional[TeacherAuthRequest]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE id=? AND status LIKE 'teacher_%'",
                (request_id,),
            ).fetchone()
        return _teacher_request_from_row(row) if row else None

    def get_pending_for_user(self, platform: str, user_id: str) -> Optional[TeacherAuthRequest]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_profiles
                WHERE platform=? AND user_id=? AND status='teacher_pending'
                ORDER BY id DESC LIMIT 1
                """,
                (platform, user_id),
            ).fetchone()
        return _teacher_request_from_row(row) if row else None

    def get_latest_for_user(self, platform: str, user_id: str) -> Optional[TeacherAuthRequest]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_profiles
                WHERE platform=? AND user_id=? AND status LIKE 'teacher_%'
                ORDER BY id DESC LIMIT 1
                """,
                (platform, user_id),
            ).fetchone()
        return _teacher_request_from_row(row) if row else None

    def list_pending(self, platform: str | None = "wecom") -> list:
        with self.db.connect() as conn:
            if platform is None:
                rows = conn.execute(
                    "SELECT * FROM user_profiles WHERE status='teacher_pending' ORDER BY created_at ASC, id ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM user_profiles WHERE platform=? AND status='teacher_pending' ORDER BY created_at ASC, id ASC",
                    (platform,),
                ).fetchall()
        return [_teacher_request_from_row(row) for row in rows]

    def decide(
        self,
        request_id: int,
        *,
        status: str,
        reviewed_by: str,
        review_note: Optional[str] = None,
    ) -> Optional[TeacherAuthRequest]:
        if status not in {"approved", "rejected"}:
            raise ValueError(f"unsupported teacher auth status: {status}")
        actual_status = f"teacher_{status}"
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE user_profiles
                SET status=?, reviewed_by=?, reviewed_at=datetime('now'),
                    review_note=?, updated_at=datetime('now')
                WHERE id=? AND status='teacher_pending'
                """,
                (actual_status, reviewed_by, review_note, request_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_by_id(request_id)


class AdminAuthRequestDAO:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.db.initialize()

    def create_or_update_pending(
        self,
        platform: str,
        user_id: str,
        *,
        name: str,
        staff_id: str,
        reason: str,
        raw: Optional[Dict[str, Any]] = None,
    ) -> AdminAuthRequest:
        raw_json = json.dumps(raw or {}, ensure_ascii=False)
        with self.db.connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM user_profiles
                WHERE platform=? AND user_id=? AND status='admin_pending'
                ORDER BY id DESC LIMIT 1
                """,
                (platform, user_id),
            ).fetchone()
            if existing:
                request_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE user_profiles
                    SET name=?, staff_id=?, reason=?, raw_json=?, updated_at=datetime('now')
                    WHERE id=?
                    """,
                    (name, staff_id, reason, raw_json, request_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO user_profiles(platform, user_id, name, staff_id, reason, status, raw_json)
                    VALUES (?, ?, ?, ?, ?, 'admin_pending', ?)
                    ON CONFLICT(platform, user_id) DO UPDATE SET
                        name=excluded.name, staff_id=excluded.staff_id,
                        reason=excluded.reason, status='admin_pending',
                        raw_json=excluded.raw_json, updated_at=datetime('now')
                    """,
                    (platform, user_id, name, staff_id, reason, raw_json),
                )
                row = conn.execute(
                    "SELECT id FROM user_profiles WHERE platform=? AND user_id=?",
                    (platform, user_id),
                ).fetchone()
                request_id = int(row["id"])
        return self.get_by_id(request_id) or AdminAuthRequest(
            request_id,
            platform,
            user_id,
            name,
            staff_id,
            reason,
            "pending",
            raw_json,
        )

    def get_by_id(self, request_id: int) -> Optional[AdminAuthRequest]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE id=? AND status LIKE 'admin_%'",
                (request_id,),
            ).fetchone()
        return _admin_request_from_row(row) if row else None

    def get_pending_for_user(self, platform: str, user_id: str) -> Optional[AdminAuthRequest]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_profiles
                WHERE platform=? AND user_id=? AND status='admin_pending'
                ORDER BY id DESC LIMIT 1
                """,
                (platform, user_id),
            ).fetchone()
        return _admin_request_from_row(row) if row else None

    def get_latest_for_user(self, platform: str, user_id: str) -> Optional[AdminAuthRequest]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_profiles
                WHERE platform=? AND user_id=? AND status LIKE 'admin_%'
                ORDER BY id DESC LIMIT 1
                """,
                (platform, user_id),
            ).fetchone()
        return _admin_request_from_row(row) if row else None

    def list_pending(self, platform: str | None = "wecom") -> list:
        with self.db.connect() as conn:
            if platform is None:
                rows = conn.execute(
                    "SELECT * FROM user_profiles WHERE status='admin_pending' ORDER BY created_at ASC, id ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM user_profiles WHERE platform=? AND status='admin_pending' ORDER BY created_at ASC, id ASC",
                    (platform,),
                ).fetchall()
        return [_admin_request_from_row(row) for row in rows]

    def decide(
        self,
        request_id: int,
        *,
        status: str,
        reviewed_by: str,
        review_note: Optional[str] = None,
    ) -> Optional[AdminAuthRequest]:
        if status not in {"approved", "rejected"}:
            raise ValueError(f"unsupported admin auth status: {status}")
        actual_status = f"admin_{status}"
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE user_profiles
                SET status=?, reviewed_by=?, reviewed_at=datetime('now'),
                    review_note=?, updated_at=datetime('now')
                WHERE id=? AND status='admin_pending'
                """,
                (actual_status, reviewed_by, review_note, request_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_by_id(request_id)


class StudentAuthRequestDAO:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.db.initialize()

    def create_or_update_pending(
        self,
        platform: str,
        user_id: str,
        *,
        name: str,
        phone: str,
        profile_snapshot: Optional[Dict[str, Any]] = None,
    ) -> StudentAuthRequest:
        snapshot_json = json.dumps(profile_snapshot or {}, ensure_ascii=False)
        with self.db.connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM student_auth_requests
                WHERE platform=? AND user_id=? AND status='pending'
                ORDER BY id DESC LIMIT 1
                """,
                (platform, user_id),
            ).fetchone()
            if existing:
                request_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE student_auth_requests
                    SET name=?, phone=?, profile_snapshot_json=?, updated_at=datetime('now')
                    WHERE id=?
                    """,
                    (name, phone, snapshot_json, request_id),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO student_auth_requests(
                        platform, user_id, name, phone, profile_snapshot_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (platform, user_id, name, phone, snapshot_json),
                )
                request_id = int(cursor.lastrowid)
        return self.get_by_id(request_id) or StudentAuthRequest(
            request_id,
            platform,
            user_id,
            name,
            phone,
            "pending",
            snapshot_json,
        )

    def get_by_id(self, request_id: int) -> Optional[StudentAuthRequest]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM student_auth_requests WHERE id=?",
                (request_id,),
            ).fetchone()
        return _student_request_from_row(row) if row else None

    def get_pending_for_user(self, platform: str, user_id: str) -> Optional[StudentAuthRequest]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM student_auth_requests
                WHERE platform=? AND user_id=? AND status='pending'
                ORDER BY id DESC LIMIT 1
                """,
                (platform, user_id),
            ).fetchone()
        return _student_request_from_row(row) if row else None

    def get_latest_for_user(self, platform: str, user_id: str) -> Optional[StudentAuthRequest]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM student_auth_requests
                WHERE platform=? AND user_id=?
                ORDER BY id DESC LIMIT 1
                """,
                (platform, user_id),
            ).fetchone()
        return _student_request_from_row(row) if row else None

    def list_pending(self, platform: str | None = "wecom") -> list:
        with self.db.connect() as conn:
            if platform is None:
                rows = conn.execute(
                    "SELECT * FROM student_auth_requests WHERE status='pending' ORDER BY created_at ASC, id ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM student_auth_requests WHERE platform=? AND status='pending' ORDER BY created_at ASC, id ASC",
                    (platform,),
                ).fetchall()
        return [_student_request_from_row(row) for row in rows]

    def decide(
        self,
        request_id: int,
        *,
        status: str,
        reviewed_by: str,
        review_note: Optional[str] = None,
    ) -> Optional[StudentAuthRequest]:
        if status not in {"approved", "rejected"}:
            raise ValueError(f"unsupported student auth status: {status}")
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE student_auth_requests
                SET status=?, reviewed_by=?, reviewed_at=datetime('now'),
                    review_note=?, updated_at=datetime('now')
                WHERE id=? AND status='pending'
                """,
                (status, reviewed_by, review_note, request_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_by_id(request_id)


def _teacher_request_from_row(row: sqlite3.Row) -> TeacherAuthRequest:
    names = {field.name for field in fields(TeacherAuthRequest)}
    data = {k: row[k] for k in row.keys() if k in names}
    status = str(data.get("status") or "")
    if status.startswith("teacher_"):
        data["status"] = status.removeprefix("teacher_")
    return TeacherAuthRequest(**data)


def _admin_request_from_row(row: sqlite3.Row) -> AdminAuthRequest:
    names = {field.name for field in fields(AdminAuthRequest)}
    data = {k: row[k] for k in row.keys() if k in names}
    status = str(data.get("status") or "")
    if status.startswith("admin_"):
        data["status"] = status.removeprefix("admin_")
    return AdminAuthRequest(**data)


def _student_request_from_row(row: sqlite3.Row) -> StudentAuthRequest:
    names = {field.name for field in fields(StudentAuthRequest)}
    return StudentAuthRequest(**{k: row[k] for k in row.keys() if k in names})
