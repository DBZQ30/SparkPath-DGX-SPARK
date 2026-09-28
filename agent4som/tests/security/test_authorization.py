"""授权（Authorization）测试：角色决策矩阵 + 三级 scope 读写权限。

覆盖：
- Z01 管理流角色矩阵（guards.py：审核教师认证 / 变更角色）
- Z02 写权限矩阵（acl_filter.check_write_permission：role × scope 全组合，含 fail-closed）
- Z03 读权限矩阵（ACLFilter：跨用户个人库隔离，admin/owner 审计性放行）
- Z04 默认 scope 派生（不可借默认通道越权）
"""

from __future__ import annotations

import pytest

from knowledge_base.auth.guards import (
    can_assign_role,
    can_review_teacher_auth,
)
from knowledge_base.retrieval.acl_filter import (
    UnauthorizedAccessError,
    ACLFilter,
    check_write_permission,
    derive_default_scope,
)

ALL_ROLES = ["owner", "admin", "teacher", "student", "guest"]


# ── Z01：管理流角色矩阵 ─────────────────────────────────────────────


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_management_roles_can_review_teacher_auth(role):
    assert can_review_teacher_auth(role).allowed is True


@pytest.mark.parametrize("role", ["teacher", "student", "guest", "", None])
def test_non_management_roles_cannot_review(role):
    decision = can_review_teacher_auth(role)
    assert decision.allowed is False
    assert "管理员" in decision.reason


@pytest.mark.parametrize("operator", ["teacher", "student", "guest", "", None])
def test_only_management_roles_can_assign_roles(operator):
    assert can_assign_role(operator, "student").allowed is False


@pytest.mark.parametrize("operator", ["admin", "owner"])
def test_management_cannot_assign_owner_role(operator):
    """owner 只能经部署配置指定——即使 admin/owner 也不可通过 API 授予。"""
    decision = can_assign_role(operator, "owner")
    assert decision.allowed is False
    assert "部署配置" in decision.reason


@pytest.mark.parametrize("operator", ["admin", "owner"])
@pytest.mark.parametrize("target", ["teacher", "student", "guest"])
def test_management_can_assign_ordinary_roles(operator, target):
    assert can_assign_role(operator, target).allowed is True


def test_role_normalization_in_guards():
    """大写/空白角色照样归一化判定，不留绕过缝。"""
    assert can_review_teacher_auth("  ADMIN ").allowed is True
    assert can_assign_role("Admin", "OWNER").allowed is False


# ── Z02：写权限矩阵（check_write_permission） ──────────────────────


def _assert_write(role, scope, user_id, *, allowed: bool):
    if allowed:
        check_write_permission(role, scope, user_id)  # 不抛即通过
    else:
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission(role, scope, user_id)


@pytest.mark.parametrize("role", ["owner", "admin"])
@pytest.mark.parametrize("scope", ["global", "teachers"])
def test_privileged_can_write_shared_scopes(role, scope):
    _assert_write(role, scope, "u1", allowed=True)


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_privileged_cannot_write_others_personal(role):
    """个人库只属于本人——管理员也不行（审计模型而非写通道）。"""
    _assert_write(role, "users/someone_else", "admin1", allowed=False)


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_privileged_can_write_own_personal(role):
    _assert_write(role, "users/admin1", "admin1", allowed=True)


def test_teacher_write_matrix():
    _assert_write("teacher", "teachers", "t1", allowed=True)
    _assert_write("teacher", "users/t1", "t1", allowed=True)
    _assert_write("teacher", "global", "t1", allowed=False)
    _assert_write("teacher", "users/t2", "t1", allowed=False)


def test_student_write_matrix():
    _assert_write("student", "users/s1", "s1", allowed=True)
    _assert_write("student", "global", "s1", allowed=False)
    _assert_write("student", "teachers", "s1", allowed=False)
    _assert_write("student", "users/other", "s1", allowed=False)


def test_guest_writes_nothing():
    for scope in ("global", "teachers", "users/g1"):
        _assert_write("guest", scope, "g1", allowed=False)


def test_unknown_role_writes_nothing():
    """未知角色 fail-closed：WRITE_PERMISSIONS.get(role, set()) 为空集合。"""
    _assert_write("superuser", "global", "x", allowed=False)
    _assert_write("", "users/x", "x", allowed=False)


def test_unknown_scope_rejected_fail_closed():
    """非三级 scope 格式直接拒（否则会在存储层误导性报错）。"""
    for role in ("admin", "teacher", "student"):
        _assert_write(role, "everyone", "u1", allowed=False)
        _assert_write(role, "users/", "u1", allowed=False)


# ── Z03：读权限矩阵（ACLFilter，跨用户隔离） ───────────────────────


def test_student_read_scopes_isolated():
    f = ACLFilter("s1", "student")
    assert f.get_allowed_scopes() == ["global", "users/s1"]
    assert f.can_access_users_scope() is False


def test_teacher_read_scopes():
    f = ACLFilter("t1", "teacher")
    assert f.get_allowed_scopes() == ["global", "teachers", "users/t1"]
    assert f.can_access_users_scope() is False


def test_guest_reads_global_only():
    assert ACLFilter("", "guest").get_allowed_scopes() == ["global"]


def test_admin_reads_all_user_scopes_for_audit():
    f = ACLFilter("a1", "admin")
    scopes = f.get_allowed_scopes(all_user_scopes=["users/x", "users/y", "users/a1"])
    assert "users/x" in scopes and "users/y" in scopes
    assert f.can_access_users_scope() is True


def test_cross_user_read_denied_for_teacher_and_student():
    for role in ("teacher", "student"):
        with pytest.raises(UnauthorizedAccessError):
            ACLFilter("u1", role).validate_requested_scope("users/u2")


def test_cross_user_read_allowed_for_admin():
    ACLFilter("a1", "admin").validate_requested_scope("users/u2")   # 审计性放行
    ACLFilter("o1", "owner").validate_requested_scope("users/u2")


def test_validate_requested_scope_unknown_scope_denied():
    with pytest.raises(UnauthorizedAccessError):
        ACLFilter("a1", "admin").validate_requested_scope("cosmic")


# ── Z04：默认 scope 派生 ────────────────────────────────────────────


@pytest.mark.parametrize(
    "role,expected",
    [("owner", "global"), ("admin", "global"), ("teacher", "teachers"),
     ("student", None), ("guest", None)],
)
def test_default_scope_never_escalates(role, expected):
    user_id = "u1"
    if expected is None:
        expected = f"users/{user_id}"
    assert derive_default_scope(role, user_id) == expected
