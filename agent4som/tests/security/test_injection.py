"""注入（Injection）测试：SQL 参数化 / 检索链路特殊字符 / scope 注入。

覆盖：
- I01 SQLite 全 DAO 参数化：引号/分号/注释序列作为字面值存取，不炸不注入
- I02 scope 注入：构造串在 ACL 层 fail-closed，进不了存储层
- I03 查询链路：BM25/审计 detail 的特殊字符不破坏系统
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_base.core.audit_logger import AuditLogger
from knowledge_base.repository.sqlite_metadata import (
    DatabaseManager,
    StudentAuthRequestDAO,
    TeacherAuthRequestDAO,
)
from knowledge_base.retrieval.acl_filter import (
    UnauthorizedAccessError, check_write_permission)

INJECTION_PAYLOADS = [
    "'; DROP TABLE auth_requests;--",
    'x" OR "1"="1',
    "union select * from sqlite_master--",
    "admin'--",
    "Robert');--",
    "1001; SHUTDOWN;",
]


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    return DatabaseManager(str(tmp_path / "inj.db"))


# ── I01：SQLite 参数化（DAO 写读往返） ─────────────────────────────


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_teacher_dao_neutralizes_sql_payloads(db, payload):
    """恶意 staff_id/name 作为字面值完整往返，无表可丢、无数可漏。"""
    dao = TeacherAuthRequestDAO(db)
    req = dao.create_or_update_pending("wecom", payload, name=payload, staff_id=payload)
    fetched = dao.get_pending_for_user("wecom", payload)
    assert fetched is not None
    assert fetched.staff_id == payload
    # 决议不受注入影响
    decided = dao.decide(req.id, status="approved", reviewed_by=payload)
    assert decided.status == "approved"


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_student_dao_neutralizes_sql_payloads(db, payload):
    """学号含引号/注释串：snapshot JSON 与 LIKE 查询都安全。"""
    dao = StudentAuthRequestDAO(db)
    req = dao.create_or_update_pending(
        "wecom", payload, name=payload, phone=payload,
        profile_snapshot={"student_id": payload},
    )
    fetched = dao.get_by_id(req.id)
    assert fetched.profile_snapshot["student_id"] == payload


def test_tables_survive_mass_injection(db):
    """批量注入后 schema 完整（没有表被 DROP）。"""
    dao = TeacherAuthRequestDAO(db)
    for i, payload in enumerate(INJECTION_PAYLOADS):
        dao.create_or_update_pending("wecom", f"u{i}", name=payload, staff_id=payload)
    with db.connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "schema_migrations" in tables
    assert len(dao.list_pending("wecom")) == len(INJECTION_PAYLOADS)  # 查询不抛、行数无损


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_audit_log_neutralizes_sql_payloads(tmp_path, payload):
    """审计行的 detail/filename 也参数化（审计是最不该被注入腐蚀的层）。"""
    audit = AuditLogger(str(tmp_path / "audit.db"))
    audit.log_event("file_ingested", user_id=payload, role="admin",
                    filename=payload, scope="global", detail={"note": payload})
    rows = audit.query(user_id=payload, limit=5)
    assert rows and rows[0].get("filename") == payload


# ── I02：scope 注入（ACL 层 fail-closed） ─────────────────────────


SCOPE_PAYLOADS = [
    "users/u1' OR '1'='1",
    "users/../../global",           # 路径穿越式 scope
    "global\0teachers",
    "global; DROP SCOPE",
    "users/",
]


@pytest.mark.parametrize("payload", SCOPE_PAYLOADS)
def test_scope_injection_rejected_by_acl(payload):
    """构造 scope 在 ACL 层拒绝：或非三级格式、或 user_id 含非法字符。"""
    with pytest.raises(UnauthorizedAccessError):
        check_write_permission("admin", payload, "u1")


# ── I03：检索链路特殊字符 ─────────────────────────────────────────


def test_bm25_query_special_characters_do_not_crash():
    """BM25 分词与空索引检索对特殊字符鲁棒（面向用户输入的搜索框）。"""
    from knowledge_base.retrieval.bm25_search import BM25Index, _tokenise

    index = BM25Index()  # 空索引：不触发 Chroma lazy-populate
    for query in ('课程"计划"', " sele%c_t ", "\\", ".*", "^$", "学'费",
                  "\n\t混排", "'; DROP INDEX;--"):
        tokens = _tokenise(query)      # 分词不抛
        assert isinstance(tokens, list)
        assert index.search(query, top_k=3) == []  # 空索引安全返回
