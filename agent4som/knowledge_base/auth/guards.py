"""Code-level authorization guards for management operations.

Role-based decision helpers used by :mod:`management_flow.service`: pure
functions that turn a caller's role into an allow/deny decision.  No I/O and no
dependency on the trusted ``QueryContext`` — callers resolve the role first and
pass it in.
"""

from __future__ import annotations

from dataclasses import dataclass

MANAGEMENT_ROLES = {"admin", "owner"}


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str = ""


def can_review_teacher_auth(role: str) -> GuardDecision:
    normalized = (role or "guest").strip().lower()
    if normalized in MANAGEMENT_ROLES:
        return GuardDecision(True)
    return GuardDecision(False, "只有管理员可以审核教师认证申请。")


def can_assign_role(operator_role: str, target_role: str) -> GuardDecision:
    operator = (operator_role or "guest").strip().lower()
    target = (target_role or "").strip().lower()
    if operator not in MANAGEMENT_ROLES:
        return GuardDecision(False, "只有管理员可以变更用户角色。")
    if target == "owner":
        return GuardDecision(False, "owner只能通过系统部署配置指定。")
    return GuardDecision(True)
