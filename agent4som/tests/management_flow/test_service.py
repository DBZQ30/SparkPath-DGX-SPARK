"""ManagementService 审核 workflow 单元测试（management_flow/service.py，零覆盖补齐）。

全部离线：SQLite 落 tmp_path，角色写入用注入的 fake（service 构造参数支持），
role_store.ROLES_PATH monkeypatch 到临时文件使 list_roles 不触真实 ~/.hermes。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_base.auth import role_store
from management_flow.service import (
    ManagementCommand,
    ManagementResult,
    ManagementService,
    _format_pending_admin_requests,
)

PLATFORM = "miniapp"
OPERATOR = "admin-001"
TEACHER_USER = "teacher-001"


class FakeRoleStore:
    """记录调用的角色写入器，用于断言审核动作的副作用。"""

    def __init__(self):
        self.set_calls: list[tuple[str, str, str]] = []
        self.unset_calls: list[tuple[str, str]] = []
        self.fail_set = False

    def set_role(self, platform: str, user_id: str, role: str) -> bool:
        if self.fail_set:
            return False
        self.set_calls.append((platform, user_id, role))
        return True

    def unset_role(self, platform: str, user_id: str) -> bool:
        self.unset_calls.append((platform, user_id))
        return True


@pytest.fixture(autouse=True)
def _isolate_roles_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """role_store.list_roles 在审核路径中被直接调用，指向 tmp 防触真实环境。"""
    monkeypatch.setattr(role_store, "ROLES_PATH", tmp_path / "roles.json")


@pytest.fixture
def fake_roles() -> FakeRoleStore:
    return FakeRoleStore()


@pytest.fixture
def service(tmp_path: Path, fake_roles: FakeRoleStore) -> ManagementService:
    return ManagementService(
        tmp_path / "mgmt.db",
        role_setter=fake_roles.set_role,
        role_unsetter=fake_roles.unset_role,
    )


def _make_teacher_request(service: ManagementService, user_id: str = TEACHER_USER):
    return service.requests.create_or_update_pending(
        PLATFORM, user_id, name="张三", staff_id="T1001"
    )


def _cmd(action: str, request_id: int | None = None, reason: str = "") -> ManagementCommand:
    return ManagementCommand(action=action, request_id=request_id, reason=reason)


def _run(service: ManagementService, command: ManagementCommand, role: str = "admin"):
    return service.process_command(
        platform=PLATFORM,
        operator_id=OPERATOR,
        operator_role=role,
        command=command,
        trace_id="trace-test",
    )


# ── 教师认证审核 ────────────────────────────────────────────────────


def test_approve_teacher_happy_path(service: ManagementService, fake_roles: FakeRoleStore):
    req = _make_teacher_request(service)
    result = _run(service, _cmd("approve_teacher", req.id))

    assert result.handled is True
    assert "已批准" in result.reply_text
    assert str(req.id) in result.reply_text
    # 角色写入 teacher
    assert fake_roles.set_calls == [(PLATFORM, TEACHER_USER, role_store.ROLE_TEACHER)]
    # 申请状态流转
    decided = service.requests.get_by_id(req.id)
    assert decided.status == "approved"
    assert decided.reviewed_by == OPERATOR
    # 通知申请人
    assert len(result.notifications) == 1
    note = result.notifications[0]
    assert note.platform == PLATFORM and note.user_id == TEACHER_USER
    assert "已通过" in note.text
    # 会话状态写入
    session = service.sessions.get(PLATFORM, TEACHER_USER)
    assert session is not None
    assert session.current_stage == "teacher_auth_approved"
    # 审计成功记录 + trace_id 透传
    entries = service.audit.list_recent(20)
    assert any(
        e.action == "approve_teacher" and e.result == "success" for e in entries
    ), [e.action for e in entries]
    assert any(e.detail.get("trace_id") == "trace-test" for e in entries)


def test_reject_teacher_with_reason(service: ManagementService, fake_roles: FakeRoleStore):
    req = _make_teacher_request(service)
    result = _run(service, _cmd("reject_teacher", req.id, reason="教职工号无法核实"))

    assert "已拒绝" in result.reply_text
    assert "教职工号无法核实" in result.reply_text
    decided = service.requests.get_by_id(req.id)
    assert decided.status == "rejected"
    # 拒绝不写角色
    assert fake_roles.set_calls == []
    # 通知 + 会话待确认信息（引导重新申请）
    assert "未通过" in result.notifications[0].text
    session = service.sessions.get(PLATFORM, TEACHER_USER)
    assert session.current_stage == "teacher_auth_rejected"
    assert session.pending_confirmation.get("action") == "teacher_auth_reapply"


def test_reject_teacher_requires_reason(service: ManagementService):
    req = _make_teacher_request(service)
    result = _run(service, _cmd("reject_teacher", req.id, reason=""))

    assert "请提供拒绝原因" in result.reply_text
    # 申请仍处于 pending
    assert "pending" in service.requests.get_by_id(req.id).status


def test_student_cannot_review(service: ManagementService, fake_roles: FakeRoleStore):
    req = _make_teacher_request(service)
    result = _run(service, _cmd("approve_teacher", req.id), role="student")

    assert "只有管理员" in result.reply_text
    assert fake_roles.set_calls == []
    assert "pending" in service.requests.get_by_id(req.id).status
    # 审计记 denied
    assert any(e.result == "denied" for e in service.audit.list_recent(10))


def test_teacher_cannot_review_admin_requests(service: ManagementService):
    """teacher 角色对管理员认证审核同样被 guard 拒绝。"""
    req = service.admin_requests.create_or_update_pending(
        PLATFORM, "admin-cand", name="李四", staff_id="T1002", reason="教务中心需要"
    )
    result = _run(service, _cmd("approve_admin", req.id), role="teacher")
    assert "只有管理员" in result.reply_text


def test_review_missing_request(service: ManagementService):
    result = _run(service, _cmd("approve_teacher", 99999))
    assert "没有找到" in result.reply_text
    assert any(e.result == "not_found" for e in service.audit.list_recent(10))


def test_double_review_rejected(service: ManagementService):
    req = _make_teacher_request(service)
    _run(service, _cmd("approve_teacher", req.id))
    result = _run(service, _cmd("approve_teacher", req.id))

    assert "不能重复审核" in result.reply_text
    assert any(e.result == "already_processed" for e in service.audit.list_recent(10))


def test_role_write_failure_aborts(service: ManagementService, fake_roles: FakeRoleStore):
    req = _make_teacher_request(service)
    fake_roles.fail_set = True
    result = _run(service, _cmd("approve_teacher", req.id))

    assert "角色写入失败" in result.reply_text
    # 申请保持 pending（未完成审核）
    assert "pending" in service.requests.get_by_id(req.id).status
    assert any(e.result == "failed" for e in service.audit.list_recent(10))


# ── 管理员认证审核 ──────────────────────────────────────────────────


def test_list_admin_requests_empty(service: ManagementService):
    result = _run(service, _cmd("list_admin_requests"))
    assert result.reply_text == "当前没有待审核的管理员认证申请。"


def test_admin_review_full_flow(service: ManagementService, fake_roles: FakeRoleStore):
    req = service.admin_requests.create_or_update_pending(
        PLATFORM, "admin-cand", name="李四", staff_id="T1002", reason="教务中心需要"
    )
    # 列表展示
    listing = _run(service, _cmd("list_admin_requests"))
    assert "1条待审核" in listing.reply_text
    assert "李四" in listing.reply_text and "T1002" in listing.reply_text

    # 批准 → admin 角色 + 通知
    result = _run(service, _cmd("approve_admin", req.id))
    assert "已批准管理员认证申请" in result.reply_text
    assert fake_roles.set_calls == [(PLATFORM, "admin-cand", role_store.ROLE_ADMIN)]
    assert any(
        e.action == "approve_admin" and e.result == "success"
        for e in service.audit.list_recent(10)
    )


def test_reject_admin_with_reason(service: ManagementService):
    req = service.admin_requests.create_or_update_pending(
        PLATFORM, "admin-cand", name="李四", staff_id="T1002", reason="教务中心需要"
    )
    result = _run(service, _cmd("reject_admin", req.id, reason="审批理由不足"))
    assert "已拒绝管理员认证申请" in result.reply_text
    decided = service.admin_requests.get_by_id(req.id)
    assert "rejected" in decided.status


def test_reject_admin_requires_reason(service: ManagementService):
    req = service.admin_requests.create_or_update_pending(
        PLATFORM, "admin-cand", name="李四", staff_id="T1002", reason="教务中心需要"
    )
    result = _run(service, _cmd("reject_admin", req.id, reason=""))
    assert "请提供拒绝原因" in result.reply_text


def test_review_missing_admin_request(service: ManagementService):
    result = _run(service, _cmd("approve_admin", 99999))
    assert "没有找到编号为99999的管理员认证申请" in result.reply_text


# ── 辅助函数 ────────────────────────────────────────────────────────


def test_format_pending_admin_requests_empty():
    assert _format_pending_admin_requests([]) == "当前没有待审核的管理员认证申请。"


def test_format_pending_admin_requests_lists_all_fields():
    from knowledge_base.repository.sqlite_metadata import AdminAuthRequest

    reqs = [
        AdminAuthRequest(
            7, PLATFORM, "u7", "王五", "T1003", "学院推荐", "pending", "{}"
        )
    ]
    text = _format_pending_admin_requests(reqs)
    assert "当前有1条待审核" in text
    assert "7. 姓名：王五" in text and "T1003" in text
    assert "批准管理员申请编号" in text


def test_management_result_defaults():
    r = ManagementResult(True)
    assert r.notifications == [] and r.command is None
