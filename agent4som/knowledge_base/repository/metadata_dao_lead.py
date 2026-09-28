"""线索/招生域 DAO：欢迎日志、线索状态、线索候选人（原 sqlite_metadata.py 拆分）。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import fields
from typing import Any, Dict, Optional

from knowledge_base.repository.metadata_db import DatabaseManager
from knowledge_base.repository.metadata_models import (
    LeadContactState,
    LeadCandidate,
)


class WelcomeSentLogDAO:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.db.initialize()

    def has_sent(self, platform: str, user_id: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM welcome_sent_log WHERE platform=? AND user_id=?",
                (platform, user_id),
            ).fetchone()
        return row is not None

    def sent_at(self, platform: str, user_id: str) -> Optional[str]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT sent_at FROM welcome_sent_log WHERE platform=? AND user_id=?",
                (platform, user_id),
            ).fetchone()
        return str(row["sent_at"]) if row else None

    def mark_sent(self, platform: str, user_id: str, welcome_type: str) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO welcome_sent_log(platform, user_id, welcome_type)
                VALUES (?, ?, ?)
                """,
                (platform, user_id, welcome_type),
            )
        return cur.rowcount > 0


class LeadContactStateDAO:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.db.initialize()

    def get(self, platform: str, user_id: str) -> Optional[LeadContactState]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM lead_contact_state WHERE platform=? AND user_id=?",
                (platform, user_id),
            ).fetchone()
        return _lead_state_from_row(row) if row else None

    def upsert(
        self,
        platform: str,
        user_id: str,
        *,
        status: str = "active",
        contact_count: int = 0,
        last_contact_type: Optional[str] = None,
        last_contact_at: Optional[str] = None,
        next_followup_at: Optional[str] = None,
        opted_out: bool = False,
    ) -> LeadContactState:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO lead_contact_state(
                    platform, user_id, status, contact_count, last_contact_type,
                    last_contact_at, next_followup_at, opted_out
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    status=excluded.status,
                    contact_count=excluded.contact_count,
                    last_contact_type=excluded.last_contact_type,
                    last_contact_at=excluded.last_contact_at,
                    next_followup_at=excluded.next_followup_at,
                    opted_out=excluded.opted_out,
                    updated_at=datetime('now')
                """,
                (
                    platform,
                    user_id,
                    status,
                    contact_count,
                    last_contact_type,
                    last_contact_at,
                    next_followup_at,
                    1 if opted_out else 0,
                ),
            )
        return self.get(platform, user_id) or LeadContactState(platform, user_id)

    def record_contact(
        self,
        platform: str,
        user_id: str,
        *,
        contact_type: str,
        message: str,
        sent_at: str,
        next_followup_at: Optional[str],
        delivery_status: str = "sent",
        error: Optional[str] = None,
        count_contact: bool = True,
    ) -> LeadContactState:
        current = self.get(platform, user_id)
        contact_count = (current.contact_count if current else 0) + (1 if count_contact else 0)
        status = current.status if current else "active"
        opted_out = bool(current.opted_out) if current else False
        last_contact_type = contact_type if count_contact else (current.last_contact_type if current else None)
        last_contact_at = sent_at if count_contact else (current.last_contact_at if current else None)
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO proactive_contact_log(
                    platform, user_id, contact_type, message, sent_at, delivery_status, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (platform, user_id, contact_type, message, sent_at, delivery_status, error),
            )
            conn.execute(
                """
                INSERT INTO lead_contact_state(
                    platform, user_id, status, contact_count, last_contact_type,
                    last_contact_at, next_followup_at, opted_out
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    status=excluded.status,
                    contact_count=excluded.contact_count,
                    last_contact_type=excluded.last_contact_type,
                    last_contact_at=excluded.last_contact_at,
                    next_followup_at=excluded.next_followup_at,
                    opted_out=excluded.opted_out,
                    updated_at=datetime('now')
                """,
                (
                    platform,
                    user_id,
                    status,
                    contact_count,
                    last_contact_type,
                    last_contact_at,
                    next_followup_at,
                    1 if opted_out else 0,
                ),
            )
        return self.get(platform, user_id) or LeadContactState(platform, user_id)

    def set_opted_out(self, platform: str, user_id: str, opted_out: bool = True) -> LeadContactState:
        current = self.get(platform, user_id)
        return self.upsert(
            platform,
            user_id,
            status=current.status if current else "active",
            contact_count=current.contact_count if current else 0,
            last_contact_type=current.last_contact_type if current else None,
            last_contact_at=current.last_contact_at if current else None,
            next_followup_at=current.next_followup_at if current else None,
            opted_out=opted_out,
        )

    def mark_engaged(
        self,
        platform: str,
        user_id: str,
        *,
        contact_type: str,
        engaged_at: str,
        next_followup_at: Optional[str],
    ) -> LeadContactState:
        current = self.get(platform, user_id)
        return self.upsert(
            platform,
            user_id,
            status=current.status if current else "active",
            contact_count=current.contact_count if current else 0,
            last_contact_type=current.last_contact_type if current and current.last_contact_type else contact_type,
            last_contact_at=current.last_contact_at if current and current.last_contact_at else engaged_at,
            next_followup_at=current.next_followup_at if current and current.next_followup_at else next_followup_at,
            opted_out=bool(current.opted_out) if current else False,
        )

    def refresh_engagement(
        self,
        platform: str,
        user_id: str,
        *,
        contact_type: str,
        engaged_at: str,
        next_followup_at: Optional[str],
    ) -> LeadContactState:
        current = self.get(platform, user_id)
        return self.upsert(
            platform,
            user_id,
            status=current.status if current else "active",
            contact_count=current.contact_count if current else 0,
            last_contact_type=contact_type,
            last_contact_at=engaged_at,
            next_followup_at=next_followup_at,
            opted_out=bool(current.opted_out) if current else False,
        )

    def log_count(self, platform: str, user_id: str) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM proactive_contact_log WHERE platform=? AND user_id=?",
                (platform, user_id),
            ).fetchone()
        return int(row["c"]) if row else 0


class LeadCandidateDAO:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.db.initialize()

    def upsert(
        self,
        platform: str,
        user_id: str,
        *,
        source: str = "manual",
        status: str = "active",
        raw: Optional[Dict[str, Any]] = None,
    ) -> LeadCandidate:
        raw_json = json.dumps(raw or {}, ensure_ascii=False)
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO lead_candidates(platform, user_id, source, status, raw_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    source=excluded.source,
                    status=excluded.status,
                    raw_json=excluded.raw_json,
                    last_seen_at=datetime('now'),
                    updated_at=datetime('now')
                """,
                (platform, user_id, source, status, raw_json),
            )
        return self.get(platform, user_id) or LeadCandidate(platform, user_id, source, status, raw_json)

    def get(self, platform: str, user_id: str) -> Optional[LeadCandidate]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM lead_candidates WHERE platform=? AND user_id=?",
                (platform, user_id),
            ).fetchone()
        return _candidate_from_row(row) if row else None

    def list_active_user_ids(self, platform: str) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id FROM lead_candidates
                WHERE platform=? AND status='active'
                ORDER BY discovered_at DESC, user_id ASC
                """,
                (platform,),
            ).fetchall()
        return [str(row["user_id"]) for row in rows]

    def list_active(self, platform: str, limit: int = 20) -> list[LeadCandidate]:
        safe_limit = max(1, min(int(limit or 20), 100))
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM lead_candidates
                WHERE platform=? AND status='active'
                ORDER BY last_seen_at DESC, discovered_at DESC, user_id ASC
                LIMIT ?
                """,
                (platform, safe_limit),
            ).fetchall()
        return [_candidate_from_row(row) for row in rows]


def _lead_state_from_row(row: sqlite3.Row) -> LeadContactState:
    names = {field.name for field in fields(LeadContactState)}
    data = {k: row[k] for k in row.keys() if k in names}
    if "opted_out" in data:
        data["opted_out"] = bool(data["opted_out"])
    return LeadContactState(**data)


def _candidate_from_row(row: sqlite3.Row) -> LeadCandidate:
    names = {field.name for field in fields(LeadCandidate)}
    return LeadCandidate(**{k: row[k] for k in row.keys() if k in names})
