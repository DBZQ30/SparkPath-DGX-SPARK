"""Tests for ACLFilter — 学业规划助手三种知识库模型."""
import pytest
from knowledge_base.retrieval.acl_filter import (
    ACLFilter, UnauthorizedAccessError, check_write_permission,
)


# ── Student ───────────────────────────────────────────────────────────


class TestStudent:
    def test_can_read_global_and_own_user(self):
        acl = ACLFilter(current_user_id="student_A", current_role="student")
        scopes = acl.get_allowed_scopes()

        assert "global" in scopes
        assert "teachers" not in scopes
        assert "users/student_A" in scopes
        assert "users/student_B" not in scopes

    def test_cannot_read_other_user(self):
        acl = ACLFilter(current_user_id="student_A", current_role="student")
        with pytest.raises(UnauthorizedAccessError):
            acl.validate_requested_scope("users/student_B")

    def test_can_read_own_scope(self):
        acl = ACLFilter(current_user_id="student_A", current_role="student")
        acl.validate_requested_scope("users/student_A")  # no error

    def test_can_write_own_user(self):
        """Students can write to their own personal KB."""
        check_write_permission("student", "users/student_A", "student_A")  # no error

    def test_cannot_write_global(self):
        """Students cannot write to public KB."""
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("student", "global", "student_A")

    def test_cannot_write_teachers(self):
        """Students cannot write to teacher KB."""
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("student", "teachers", "student_A")


# ── Admin ─────────────────────────────────────────────────────────────


class TestAdmin:
    def test_can_read_all(self):
        acl = ACLFilter(current_user_id="admin_1", current_role="admin")
        scopes = acl.get_allowed_scopes()

        assert "global" in scopes
        assert "teachers" in scopes
        assert "users/admin_1" in scopes

    def test_can_read_other_user(self):
        """Admin can read other users' data (audited)."""
        acl = ACLFilter(current_user_id="admin_1", current_role="admin")
        assert acl.can_access_users_scope()
        acl.validate_requested_scope("users/student_A")  # no error

    def test_can_write_global(self):
        check_write_permission("admin", "global", "admin_1")  # no error

    def test_can_write_teachers(self):
        check_write_permission("admin", "teachers", "admin_1")  # no error

    def test_can_write_own_user(self):
        """Admin can also have a personal KB."""
        check_write_permission("admin", "users/admin_1", "admin_1")  # no error


# ── Teacher ───────────────────────────────────────────────────────────


class TestTeacher:
    def test_can_read_global_teachers_own(self):
        acl = ACLFilter(current_user_id="teacher_1", current_role="teacher")
        scopes = acl.get_allowed_scopes()

        assert "global" in scopes
        assert "teachers" in scopes
        assert "users/teacher_1" in scopes
        assert "users/student_A" not in scopes

    def test_cannot_read_other_user(self):
        acl = ACLFilter(current_user_id="teacher_1", current_role="teacher")
        with pytest.raises(UnauthorizedAccessError):
            acl.validate_requested_scope("users/student_A")

    def test_can_read_own_scope(self):
        acl = ACLFilter(current_user_id="teacher_1", current_role="teacher")
        acl.validate_requested_scope("users/teacher_1")  # no error

    def test_can_write_teachers(self):
        check_write_permission("teacher", "teachers", "teacher_1")  # no error

    def test_can_write_own_user(self):
        check_write_permission("teacher", "users/teacher_1", "teacher_1")  # no error

    def test_cannot_write_global(self):
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("teacher", "global", "teacher_1")


# ── Guest (预留) ────────────────────────────────────────────────────────


class TestGuest:
    def test_global_only(self):
        acl = ACLFilter(current_user_id="visitor", current_role="guest")
        scopes = acl.get_allowed_scopes()

        assert "global" in scopes
        assert "teachers" not in scopes
        assert not any(s.startswith("users/") for s in scopes)

    def test_cannot_read_any_user_scope(self):
        acl = ACLFilter(current_user_id="visitor", current_role="guest")
        with pytest.raises(UnauthorizedAccessError):
            acl.validate_requested_scope("users/student_A")

    def test_global_scope_ok(self):
        acl = ACLFilter(current_user_id="visitor", current_role="guest")
        acl.validate_requested_scope("global")  # no error

    def test_cannot_write_anything(self):
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("guest", "global", "visitor")
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("guest", "teachers", "visitor")
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("guest", "users/visitor", "visitor")


# ── Owner role ──────────────────────────────────────────────────────────


class TestOwner:
    def test_can_read_all(self):
        acl = ACLFilter("owner_user", "owner")
        scopes = acl.get_allowed_scopes()
        assert "global" in scopes
        assert "teachers" in scopes
        assert "users/owner_user" in scopes

    def test_can_access_other_users(self):
        """Owner can read other users' data (like admin)."""
        acl = ACLFilter("owner_user", "owner")
        assert acl.can_access_users_scope() is True

    def test_can_write_global(self):
        check_write_permission("owner", "global", "owner_user")

    def test_can_write_teachers(self):
        check_write_permission("owner", "teachers", "owner_user")

    def test_can_write_own_user(self):
        check_write_permission("owner", "users/owner_user", "owner_user")

    def test_cannot_write_other_user(self):
        """Owner CANNOT write to other users' personal KB."""
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("owner", "users/student_A", "owner_user")

    def test_validate_requested_scope_allows_other_user(self):
        """Owner can READ other users' data (audit), but not write."""
        acl = ACLFilter("owner_user", "owner")
        acl.validate_requested_scope("users/student_B")  # read: no error


# ── Write permission — edge cases ──────────────────────────────────────


class TestWritePermissionEdgeCases:
    def test_cannot_write_other_user(self):
        """A student cannot write to another user's personal KB."""
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("student", "users/other", "student_A")

    def test_teacher_cannot_write_other_user(self):
        """A teacher cannot write to another user's personal KB."""
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("teacher", "users/other", "teacher_1")

    def test_admin_cannot_write_other_user(self):
        """Admin CANNOT write to another user's personal KB."""
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("admin", "users/student_A", "admin_1")

    def test_owner_cannot_write_other_user(self):
        """Owner CANNOT write to another user's personal KB."""
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("owner", "users/student_B", "owner_user")

    def test_admin_can_write_own_user(self):
        """Admin CAN write to their own personal KB."""
        check_write_permission("admin", "users/admin_1", "admin_1")  # no error

    def test_unknown_role_denied(self):
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("unknown_role", "global", "user_x")

    # ── New fail-closed behaviour (2026-08-14) ─────────────────────────

    def test_unknown_scope_denied(self):
        """Unknown scopes are rejected instead of silently passing through
        to fail later at the storage layer with a misleading error."""
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("admin", "foo", "admin_1")
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("admin", "users", "admin_1")
        with pytest.raises(UnauthorizedAccessError):
            check_write_permission("admin", "assistants/x", "admin_1")

    def test_invalid_user_id_denied(self):
        """user_ids with characters the storage layer refuses (Chinese,
        spaces, @, etc.) fail early with a clear message."""
        with pytest.raises(UnauthorizedAccessError, match="非法字符"):
            check_write_permission("student", "users/张三", "张三")
        with pytest.raises(UnauthorizedAccessError, match="非法字符"):
            check_write_permission("student", "users/zhang san", "zhang san")
