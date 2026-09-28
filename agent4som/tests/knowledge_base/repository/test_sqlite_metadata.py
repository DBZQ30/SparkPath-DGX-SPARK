"""sqlite_metadata DAO 单元测试（1700 行核心持久化层，此前零直接覆盖）。

全部离线：SQLite 落 tmp_path。重点覆盖各 DAO 的 CRUD 语义、
状态机流转（pending → approved/rejected）与 JSON 字段往返。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_base.repository.sqlite_metadata import (
    AdminAuthRequestDAO,
    AuditLogDAO,
    ConversationSessionDAO,
    DatabaseManager,
    LeadCandidateDAO,
    LeadContactStateDAO,
    ManagementReviewContextDAO,
    StudentAuthRequestDAO,
    TeacherAuthRequestDAO,
    resolve_sqlite_db_path,
)

PLATFORM = "miniapp"


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    return DatabaseManager(tmp_path / "meta.db")


# ── DatabaseManager / 路径解析 ──────────────────────────────────────


def test_resolve_sqlite_db_path_appends_default_name():
    path = resolve_sqlite_db_path("data/sqlite/")
    assert path.name == "miniapp.db"


def test_resolve_sqlite_db_path_keeps_db_suffix(tmp_path: Path):
    target = tmp_path / "custom.sqlite"
    assert resolve_sqlite_db_path(str(target)) == target


def test_db_initialize_idempotent(db: DatabaseManager):
    db.initialize()
    db.initialize()  # 不应抛错（重复建表）
    with db.connect() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert {"user_profiles", "audit_log", "conversation_sessions"} <= tables


# ── TeacherAuthRequestDAO ───────────────────────────────────────────


def test_teacher_request_lifecycle(db: DatabaseManager):
    dao = TeacherAuthRequestDAO(db)

    # 新建 → pending
    req = dao.create_or_update_pending(PLATFORM, "u1", name="张三", staff_id="T001")
    assert req.status == "pending"
    assert dao.get_pending_for_user(PLATFORM, "u1").id == req.id

    # 同一用户再次提交 → 更新同一条（不新增）
    req2 = dao.create_or_update_pending(PLATFORM, "u1", name="张三改", staff_id="T002")
    assert req2.id == req.id
    assert req2.staff_id == "T002"
    pending = dao.list_pending(PLATFORM)
    assert len(pending) == 1 and pending[0].id == req.id

    # decide → approved，pending 清空
    decided = dao.decide(req.id, status="approved", reviewed_by="admin-1")
    assert decided.status == "approved"
    assert decided.reviewed_by == "admin-1"
    assert dao.get_pending_for_user(PLATFORM, "u1") is None
    # get_latest_for_user 仍能追溯到已定案记录
    assert dao.get_latest_for_user(PLATFORM, "u1").id == req.id


def test_teacher_request_reject_and_invalid_status(db: DatabaseManager):
    dao = TeacherAuthRequestDAO(db)
    req = dao.create_or_update_pending(PLATFORM, "u1", name="张三", staff_id="T001")

    decided = dao.decide(req.id, status="rejected", reviewed_by="admin-1",
                         review_note="材料不全")
    assert decided.status == "rejected"
    assert decided.review_note == "材料不全"

    with pytest.raises(ValueError):
        dao.decide(req.id, status="frozen", reviewed_by="admin-1")


def test_teacher_request_double_decide_returns_none(db: DatabaseManager):
    dao = TeacherAuthRequestDAO(db)
    req = dao.create_or_update_pending(PLATFORM, "u1", name="张三", staff_id="T001")
    assert dao.decide(req.id, status="approved", reviewed_by="admin-1") is not None
    # 已不是 pending → 再 decide 返回 None（并发互斥语义）
    assert dao.decide(req.id, status="approved", reviewed_by="admin-2") is None


def test_teacher_list_pending_platform_filter(db: DatabaseManager):
    dao = TeacherAuthRequestDAO(db)
    dao.create_or_update_pending("wecom", "u1", name="a", staff_id="T1")
    dao.create_or_update_pending("miniapp", "u2", name="b", staff_id="T2")

    wecom_ids = {r.id for r in dao.list_pending("wecom")}
    all_ids = {r.id for r in dao.list_pending(None)}
    assert len(wecom_ids) == 1
    assert all_ids == wecom_ids | {r.id for r in dao.list_pending("miniapp")}


# ── AdminAuthRequestDAO / StudentAuthRequestDAO ────────────────────


def test_admin_request_lifecycle(db: DatabaseManager):
    dao = AdminAuthRequestDAO(db)
    req = dao.create_or_update_pending(
        PLATFORM, "u1", name="李四", staff_id="T002", reason="教务中心需要"
    )
    assert req.status == "pending" and req.reason == "教务中心需要"

    listed = dao.list_pending(PLATFORM)
    assert [r.id for r in listed] == [req.id]

    decided = dao.decide(req.id, status="approved", reviewed_by="admin-1")
    assert decided.status == "approved"
    assert dao.list_pending(PLATFORM) == []


def test_student_request_lifecycle(db: DatabaseManager):
    dao = StudentAuthRequestDAO(db)
    req = dao.create_or_update_pending(
        PLATFORM, "u1", name="王五", phone="13800000000",
        profile_snapshot={"student_id": "2023001"},
    )
    assert req.status == "pending" and req.phone == "13800000000"
    # snapshot JSON 往返
    fetched = dao.get_by_id(req.id)
    assert fetched.profile_snapshot["student_id"] == "2023001"

    decided = dao.decide(req.id, status="approved", reviewed_by="admin-1")
    assert "approved" in decided.status


# ── ConversationSessionDAO ──────────────────────────────────────────


def test_session_upsert_insert_then_update(db: DatabaseManager):
    dao = ConversationSessionDAO(db)
    assert dao.get(PLATFORM, "u1") is None

    s1 = dao.upsert(PLATFORM, "u1", session_id="s-1", current_stage="collecting",
                    collected_fields={"identity": "teacher"},
                    pending_confirmation={"action": "confirm"})
    assert s1.current_stage == "collecting"
    assert s1.collected_fields == {"identity": "teacher"}
    assert s1.pending_confirmation["action"] == "confirm"

    # 同 key 再 upsert → 覆盖同一行
    s2 = dao.upsert(PLATFORM, "u1", session_id="s-2", current_stage="done",
                    current_topic="认证", collected_fields={}, refused_fields=["phone"])
    got = dao.get(PLATFORM, "u1")
    assert got.session_id == "s-2"
    assert got.current_stage == "done"
    assert got.refused_fields == ["phone"]


def test_session_different_users_isolated(db: DatabaseManager):
    dao = ConversationSessionDAO(db)
    dao.upsert(PLATFORM, "u1", current_stage="a")
    dao.upsert(PLATFORM, "u2", current_stage="b")
    assert dao.get(PLATFORM, "u1").current_stage == "a"
    assert dao.get(PLATFORM, "u2").current_stage == "b"


# ── AuditLogDAO ─────────────────────────────────────────────────────


def test_audit_record_and_query(db: DatabaseManager):
    dao = AuditLogDAO(db)
    entry = dao.record(
        PLATFORM, "admin-1", "admin", "approve_teacher", "teacher_auth_request",
        target_id="42", result="success", detail={"assigned_role": "teacher"},
    )
    fetched = dao.get_by_id(entry.id)
    assert fetched.action == "approve_teacher"
    assert fetched.result == "success"
    assert fetched.detail == {"assigned_role": "teacher"}

    dao.record(PLATFORM, "admin-1", "admin", "reject_teacher",
               "teacher_auth_request", result="success")
    recent = dao.list_recent(10)
    assert len(recent) == 2
    # 默认倒序（最新在前）
    assert recent[0].action == "reject_teacher"


# ── LeadCandidate / LeadContactState / ReviewContext ────────────────


def test_lead_candidate_upsert_and_list(db: DatabaseManager):
    candidates = LeadCandidateDAO(db)
    states = LeadContactStateDAO(db)

    candidates.upsert(PLATFORM, "lead-1", source="miniapp",
                      raw={"name": "赵六", "intent": "咨询培养方案"})
    got = candidates.get(PLATFORM, "lead-1")
    assert got.source == "miniapp"
    assert got.raw["name"] == "赵六"

    active = candidates.list_active(PLATFORM, limit=10)
    assert [c.user_id for c in active] == ["lead-1"]

    # 接触状态：record → 计数增长
    states.upsert(PLATFORM, "lead-1")
    states.record_contact(PLATFORM, "lead-1", contact_type="chat", message="你好",
                          sent_at="2026-09-26T10:00:00", next_followup_at=None)
    states.record_contact(PLATFORM, "lead-1", contact_type="chat", message="再联系",
                          sent_at="2026-09-26T11:00:00", next_followup_at=None)
    state = states.get(PLATFORM, "lead-1")
    assert states.log_count(PLATFORM, "lead-1") == 2

    # opt-out 只标记接触状态；候选名单中的记录保留为 active
    states.set_opted_out(PLATFORM, "lead-1", opted_out=True)
    assert bool(states.get(PLATFORM, "lead-1").opted_out) is True
    assert "lead-1" in candidates.list_active_user_ids(PLATFORM)


def test_review_context_set_get_clear(db: DatabaseManager):
    dao = ManagementReviewContextDAO(db)
    assert dao.get(PLATFORM, "reviewer-1") is None

    dao.set(PLATFORM, "reviewer-1", request_type="teacher", request_id=7,
            pending_action="approve")
    ctx = dao.get(PLATFORM, "reviewer-1")
    assert ctx.request_type == "teacher" and ctx.request_id == 7

    dao.clear(PLATFORM, "reviewer-1")
    assert dao.get(PLATFORM, "reviewer-1") is None
