"""Deterministic teacher-authentication review workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from knowledge_base.auth import role_store
from knowledge_base.auth.guards import (
    can_assign_role,
    can_review_teacher_auth,
)
from knowledge_base.repository.sqlite_metadata import (
    AdminAuthRequest,
    AdminAuthRequestDAO,
    AuditLogDAO,
    ConversationSessionDAO,
    DatabaseManager,
    LeadCandidateDAO,
    LeadContactStateDAO,
    AdmissionProfileDAO,
    ManagementReviewContextDAO,
    StudentAuthRequestDAO,
    TeacherAuthRequest,
    TeacherAuthRequestDAO,
)


@dataclass(frozen=True)
class ManagementCommand:
    action: str
    request_id: Optional[int] = None
    reason: str = ""
    target: str = ""


@dataclass(frozen=True)
class OutboundNotification:
    platform: str
    user_id: str
    text: str


@dataclass(frozen=True)
class ManagementResult:
    handled: bool
    reply_text: str = ""
    command: Optional[ManagementCommand] = None
    notifications: list[OutboundNotification] = field(default_factory=list)


class ManagementService:
    def __init__(
        self,
        db_path: str | Path,
        *,
        role_setter: Callable[[str, str, str], bool] = role_store.set_role,
        role_unsetter: Callable[[str, str], bool] = role_store.unset_role,
    ):
        db = DatabaseManager(db_path)
        self.requests = TeacherAuthRequestDAO(db)
        self.admin_requests = AdminAuthRequestDAO(db)
        self.student_requests = StudentAuthRequestDAO(db)
        self.profiles = AdmissionProfileDAO(db)
        self.candidates = LeadCandidateDAO(db)
        self.contact_states = LeadContactStateDAO(db)
        self.review_context = ManagementReviewContextDAO(db)
        self.audit = AuditLogDAO(db)
        self.sessions = ConversationSessionDAO(db)
        self._role_setter = role_setter
        self._role_unsetter = role_unsetter
        self._trace_id = ""


    def process_command(
        self,
        *,
        platform: str,
        operator_id: str,
        operator_role: str,
        command: ManagementCommand,
        trace_id: str = "",
    ) -> ManagementResult:
        self._trace_id = str(trace_id or self._trace_id or "")

        if command.action in {
            "list_admin_requests",
            "approve_admin",
            "reject_admin",
        }:
            return self._process_admin_review(
                platform=platform,
                operator_id=operator_id,
                operator_role=operator_role,
                command=command,
            )


        guard = can_review_teacher_auth(operator_role)
        if not guard.allowed:
            self._audit(
                platform,
                operator_id,
                operator_role,
                command,
                result="denied",
                detail={"reason": guard.reason},
            )
            return ManagementResult(True, guard.reason, command)


        request = self.requests.get_by_id(int(command.request_id or 0))
        if request is None:
            self._audit(
                platform,
                operator_id,
                operator_role,
                command,
                result="not_found",
            )
            return ManagementResult(True, f"没有找到编号为{command.request_id}的教师认证申请。", command)
        if request.status and "pending" not in request.status:
            self._audit(
                platform, operator_id, operator_role, command,
                target=request, result="already_processed",
                detail={"status": request.status},
            )
            status_label = "已批准" if request.status and "approved" in request.status else "已拒绝"
            return ManagementResult(True, f"教师认证申请{request.id}{status_label}，不能重复审核。", command)

        if command.action == "reject_teacher":
            if not command.reason:
                self._audit(
                    platform,
                    operator_id,
                    operator_role,
                    command,
                    target=request,
                    result="invalid",
                    detail={"reason": "missing_rejection_reason"},
                )
                return ManagementResult(
                    True,
                    f"请提供拒绝原因，例如：/reject_teacher {request.id} 教职工号无法核实",
                    command,
                )
            decided = self.requests.decide(
                request.id or 0,
                status="rejected",
                reviewed_by=operator_id,
                review_note=command.reason,
            )
            if decided is None:
                return self._already_processed(platform, operator_id, operator_role, command, request)
            self._set_teacher_session_state(
                decided,
                current_stage="teacher_auth_rejected",
                pending_confirmation={
                    "action": "teacher_auth_reapply",
                    "question": (
                        f"您的教师认证申请未通过。原因：{command.reason}。"
                        "如需重新申请，请发送“重新申请”或直接发送新的姓名和教职工号；"
                        "如需改为访客身份，请发送“访客”。"
                    ),
                },
            )
            self._audit(
                platform,
                operator_id,
                operator_role,
                command,
                target=decided,
                result="success",
                detail={"review_note": command.reason},
            )
            return ManagementResult(
                True,
                f"已拒绝教师认证申请{decided.id}：姓名：{decided.name}；教职工号：{decided.staff_id}。原因：{command.reason}。",
                command,
                [
                    OutboundNotification(
                        decided.platform,
                        decided.user_id,
                        f"您的教师认证申请未通过。原因：{command.reason}。"
                        "如需重新申请，请发送“重新申请”或直接发送新的姓名和教职工号；"
                        "如需改为访客身份，请发送“访客”。",
                    )
                ],
            )

        assign_guard = can_assign_role(operator_role, role_store.ROLE_TEACHER)
        if not assign_guard.allowed:
            self._audit(
                platform,
                operator_id,
                operator_role,
                command,
                target=request,
                result="denied",
                detail={"reason": assign_guard.reason},
            )
            return ManagementResult(True, assign_guard.reason, command)

        previous_explicit_role = role_store.list_roles(request.platform).get(request.user_id)
        if not self._role_setter(request.platform, request.user_id, role_store.ROLE_TEACHER):
            self._audit(
                platform,
                operator_id,
                operator_role,
                command,
                target=request,
                result="failed",
                detail={"reason": "role_write_failed"},
            )
            return ManagementResult(True, "角色写入失败，本次审核未完成，请稍后重试。", command)

        decided = self.requests.decide(
            request.id or 0,
            status="approved",
            reviewed_by=operator_id,
            review_note="",
        )
        if decided is None:
            self._restore_role(request.platform, request.user_id, previous_explicit_role)
            return self._already_processed(platform, operator_id, operator_role, command, request)

        self._set_teacher_session_state(
            decided,
            current_stage="teacher_auth_approved",
            pending_confirmation={},
        )
        self._audit(
            platform,
            operator_id,
            operator_role,
            command,
            target=decided,
            result="success",
            detail={"assigned_role": role_store.ROLE_TEACHER},
        )
        return ManagementResult(
            True,
            f"已批准教师认证申请{decided.id}：姓名：{decided.name}；教职工号：{decided.staff_id}。用户已获得教师权限。",
            command,
            [
                OutboundNotification(
                    decided.platform,
                    decided.user_id,
                    f"您的教师认证申请已通过。姓名：{decided.name}；教职工号：{decided.staff_id}。您现在已获得教师权限。",
                )
            ],
        )


    def _process_admin_review(
        self,
        *,
        platform: str,
        operator_id: str,
        operator_role: str,
        command: ManagementCommand,
    ) -> ManagementResult:
        guard = can_review_teacher_auth(operator_role)
        if not guard.allowed:
            self._audit_admin(platform, operator_id, operator_role, command, result="denied")
            return ManagementResult(True, guard.reason, command)

        if command.action == "list_admin_requests":
            pending = self.admin_requests.list_pending(None)
            self._audit_admin(
                platform,
                operator_id,
                operator_role,
                command,
                result="success",
                detail={"count": len(pending)},
            )
            return ManagementResult(True, _format_pending_admin_requests(pending), command)

        request = self.admin_requests.get_by_id(int(command.request_id or 0))
        if request is None:
            self._audit_admin(platform, operator_id, operator_role, command, result="not_found")
            return ManagementResult(True, f"没有找到编号为{command.request_id}的管理员认证申请。", command)
        if request.status and "pending" not in request.status:
            self._audit_admin(platform, operator_id, operator_role, command,
                target=request, result="already_processed",
                detail={"status": request.status},
            )
            status_label = "已批准" if request.status and "approved" in request.status else "已拒绝"
            return ManagementResult(True, f"管理员认证申请{request.id}{status_label}，不能重复审核。", command)

        if command.action == "reject_admin":
            if not command.reason:
                self._audit_admin(
                    platform,
                    operator_id,
                    operator_role,
                    command,
                    target=request,
                    result="invalid",
                    detail={"reason": "missing_rejection_reason"},
                )
                return ManagementResult(
                    True,
                    f"请提供拒绝原因，例如：/reject_admin {request.id} 审批理由不足",
                    command,
                )
            decided = self.admin_requests.decide(
                request.id or 0,
                status="rejected",
                reviewed_by=operator_id,
                review_note=command.reason,
            )
            if decided is None:
                return ManagementResult(True, f"管理员认证申请{request.id}已处理，不能重复审核。", command)
            self._audit_admin(
                platform,
                operator_id,
                operator_role,
                command,
                target=decided,
                result="success",
                detail={"review_note": command.reason},
            )
            return ManagementResult(
                True,
                f"已拒绝管理员认证申请{decided.id}：姓名：{decided.name}；教职工号：{decided.staff_id}。原因：{command.reason}。",
                command,
                [
                    OutboundNotification(
                        decided.platform,
                        decided.user_id,
                        f"您的管理员认证申请未通过。原因：{command.reason}。",
                    )
                ],
            )

        assign_guard = can_assign_role(operator_role, role_store.ROLE_ADMIN)
        if not assign_guard.allowed:
            self._audit_admin(
                platform,
                operator_id,
                operator_role,
                command,
                target=request,
                result="denied",
                detail={"reason": assign_guard.reason},
            )
            return ManagementResult(True, assign_guard.reason, command)

        previous_explicit_role = role_store.list_roles(request.platform).get(request.user_id)
        if not self._role_setter(request.platform, request.user_id, role_store.ROLE_ADMIN):
            self._audit_admin(
                platform,
                operator_id,
                operator_role,
                command,
                target=request,
                result="failed",
                detail={"reason": "role_write_failed"},
            )
            return ManagementResult(True, "角色写入失败，本次审核未完成，请稍后重试。", command)

        decided = self.admin_requests.decide(
            request.id or 0,
            status="approved",
            reviewed_by=operator_id,
            review_note="",
        )
        if decided is None:
            self._restore_role(request.platform, request.user_id, previous_explicit_role)
            return ManagementResult(True, f"管理员认证申请{request.id}已处理，不能重复审核。", command)

        self._audit_admin(
            platform,
            operator_id,
            operator_role,
            command,
            target=decided,
            result="success",
            detail={"assigned_role": role_store.ROLE_ADMIN},
        )
        return ManagementResult(
            True,
            f"已批准管理员认证申请{decided.id}：姓名：{decided.name}；教职工号：{decided.staff_id}。用户已获得管理员权限。",
            command,
            [
                OutboundNotification(
                    decided.platform,
                    decided.user_id,
                    f"您的管理员认证申请已通过。姓名：{decided.name}；教职工号：{decided.staff_id}。您现在已获得管理员权限。",
                )
            ],
        )


    def _set_teacher_session_state(
        self,
        request: TeacherAuthRequest,
        *,
        current_stage: str,
        pending_confirmation: dict[str, Any],
    ) -> None:
        current = self.sessions.get(request.platform, request.user_id)
        self.sessions.upsert(
            request.platform,
            request.user_id,
            session_id=current.session_id if current else None,
            current_stage=current_stage,
            current_topic=current.current_topic if current else None,
            collected_fields={"identity": "teacher"},
            pending_confirmation=pending_confirmation,
        )


    def _restore_role(self, platform: str, user_id: str, previous_role: Optional[str]) -> None:
        if previous_role:
            self._role_setter(platform, user_id, previous_role)
        else:
            self._role_unsetter(platform, user_id)

    def _already_processed(
        self,
        platform: str,
        operator_id: str,
        operator_role: str,
        command: ManagementCommand,
        request: TeacherAuthRequest,
    ) -> ManagementResult:
        current = self.requests.get_by_id(request.id or 0)
        status = current.status if current else request.status
        self._audit(
            platform,
            operator_id,
            operator_role,
            command,
            target=current or request,
            result="already_processed",
            detail={"status": status},
        )
        return ManagementResult(True, f"教师认证申请{request.id}已被其他管理员处理，请刷新待审核列表。", command)

    def _audit(
        self,
        platform: str,
        operator_id: str,
        operator_role: str,
        command: ManagementCommand,
        *,
        target: Optional[TeacherAuthRequest] = None,
        result: str,
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        target_id = str(target.id) if target and target.id is not None else (
            str(command.request_id) if command.request_id is not None else None
        )
        self.audit.record(
            platform,
            operator_id,
            operator_role,
            command.action,
            "teacher_auth_request",
            target_id=target_id,
            result=result,
            detail=self._detail_with_trace(detail),
        )


    def _audit_admin(
        self,
        platform: str,
        operator_id: str,
        operator_role: str,
        command: ManagementCommand,
        *,
        target: Optional[AdminAuthRequest] = None,
        result: str,
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        target_id = str(command.request_id or "")
        if target is not None and target.id is not None:
            target_id = str(target.id)
        self.audit.record(
            platform,
            operator_id,
            operator_role,
            command.action,
            "admin_auth_request",
            target_id=target_id,
            result=result,
            detail=self._detail_with_trace(detail),
        )


    def _detail_with_trace(self, detail: Optional[dict[str, Any]]) -> dict[str, Any]:
        merged = dict(detail or {})
        if self._trace_id:
            merged["trace_id"] = self._trace_id
        return merged


def _format_pending_admin_requests(requests: list[AdminAuthRequest]) -> str:
    if not requests:
        return "当前没有待审核的管理员认证申请。"
    lines = [f"当前有{len(requests)}条待审核的管理员认证申请："]
    for request in requests:
        lines.append(
            f"{request.id}. 姓名：{request.name}；教职工号：{request.staff_id}；"
            f"申请理由：{request.reason}；账号：{request.user_id}；提交时间：{request.created_at or '未知'}"
            + f"；来源：{request.platform}"
        )
    lines.append("可发送“批准管理员申请编号”或“拒绝管理员申请编号，原因：xxx”进行处理。")
    return "\n".join(lines)
