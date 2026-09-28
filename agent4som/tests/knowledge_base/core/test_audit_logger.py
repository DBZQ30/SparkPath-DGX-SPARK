"""Tests for AuditLogger."""

from knowledge_base.core.audit_logger import AuditLogger


def test_log_and_query(tmp_path):
    db = AuditLogger(str(tmp_path / "audit.db"))
    db.log_event("test_event", user_id="u1", role="student", detail={"action": "search"})
    db.log_event("test_event", user_id="u2", role="admin", detail={"action": "ingest"})
    rows = db.query(limit=10)
    assert len(rows) == 2
    assert rows[0]["user_id"] == "u2"
    assert rows[1]["user_id"] == "u1"


def test_query_filter_by_event_type(tmp_path):
    db = AuditLogger(str(tmp_path / "audit.db"))
    db.log_event("auth_violation", user_id="u1", role="student")
    db.log_event("file_ingested", user_id="u2", role="admin")
    rows = db.query(event_type="auth_violation")
    assert len(rows) == 1


def test_query_filter_by_user(tmp_path):
    db = AuditLogger(str(tmp_path / "audit.db"))
    db.log_event("event", user_id="target_user", role="student")
    db.log_event("event", user_id="other_user", role="admin")
    rows = db.query(user_id="target_user")
    assert len(rows) == 1
    assert rows[0]["user_id"] == "target_user"


def test_empty_db(tmp_path):
    db = AuditLogger(str(tmp_path / "audit.db"))
    rows = db.query(limit=10)
    assert rows == []


def test_detail_serialization(tmp_path):
    db = AuditLogger(str(tmp_path / "audit.db"))
    detail = {"scope": "users/private", "reason": "cross_user_access"}
    db.log_event("auth_violation", user_id="u1", role="student", detail=detail)
    rows = db.query()
    assert rows[0]["detail"] == detail


def test_audit_timestamps_are_set(tmp_path):
    db = AuditLogger(str(tmp_path / "audit.db"))
    db.log_event("test")
    rows = db.query()
    assert rows[0]["timestamp"] != ""
