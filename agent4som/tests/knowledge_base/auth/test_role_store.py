"""
Unit tests for ``knowledge_base/auth/role_store.py``.

Tests are pure unit tests — no Hermes gateway dependency.
The ``ROLES_PATH`` global is monkeypatched to a temp file for each test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_base.auth import role_store

# Well-known roles from the module
ROLE_OWNER = role_store.ROLE_OWNER
ROLE_ADMIN = role_store.ROLE_ADMIN
ROLE_TEACHER = role_store.ROLE_TEACHER
ROLE_STUDENT = role_store.ROLE_STUDENT
ROLE_GUEST = role_store.ROLE_GUEST
DEFAULT_ROLE = role_store.DEFAULT_ROLE
VALID_ROLES = role_store.VALID_ROLES


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_roles_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``ROLES_PATH`` to a temp file so tests never touch ``~/.hermes/roles.json``."""
    temp_roles = tmp_path / "roles.json"
    monkeypatch.setattr(role_store, "ROLES_PATH", temp_roles)


@pytest.fixture
def seed_admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pre-seed ``roles.json`` with one admin user on ``wecom``."""
    roles_file = _make_path(tmp_path, monkeypatch)
    roles_file.write_text(
        json.dumps({"wecom": {"zhangsan": "admin"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return roles_file


def _make_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return the temp ROLES_PATH after patching."""
    p = tmp_path / "roles.json"
    monkeypatch.setattr(role_store, "ROLES_PATH", p)
    return p


# ── get_role ──────────────────────────────────────────────────────────


class TestGetRole:
    def test_existing_user_returns_role(self, seed_admin: Path):
        """get_role returns the stored role for an existing user."""
        assert role_store.get_role("wecom", "zhangsan") == "admin"

    def test_missing_user_returns_default(self, seed_admin: Path):
        """get_role returns DEFAULT_ROLE for a user not in roles.json."""
        assert role_store.get_role("wecom", "nobody") == DEFAULT_ROLE

    def test_missing_platform_returns_default(self, seed_admin: Path):
        """get_role returns DEFAULT_ROLE for a non-existent platform."""
        assert role_store.get_role("telegram", "zhangsan") == DEFAULT_ROLE

    def test_empty_file_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """get_role returns DEFAULT_ROLE when roles.json is empty dict."""
        p = _make_path(tmp_path, monkeypatch)
        p.write_text("{}", encoding="utf-8")
        assert role_store.get_role("wecom", "anyone") == DEFAULT_ROLE

    def test_file_not_found_returns_default(self):
        """get_role returns DEFAULT_ROLE when roles.json does not exist."""
        # Roles path points to non-existent file (default from fixture)
        assert role_store.get_role("wecom", "anyone") == DEFAULT_ROLE

    def test_corrupted_json_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """get_role returns DEFAULT_ROLE when roles.json is corrupted."""
        p = _make_path(tmp_path, monkeypatch)
        p.write_text("{{invalid json}}", encoding="utf-8")
        assert role_store.get_role("wecom", "anyone") == DEFAULT_ROLE

    def test_non_dict_top_level_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """get_role returns DEFAULT_ROLE when roles.json top-level is not a dict."""
        p = _make_path(tmp_path, monkeypatch)
        p.write_text('["not", "a", "dict"]', encoding="utf-8")
        assert role_store.get_role("wecom", "anyone") == DEFAULT_ROLE

    def test_multiple_platforms_isolated(self, seed_admin: Path):
        """get_role keeps platforms isolated."""
        assert role_store.get_role("wecom", "zhangsan") == "admin"
        assert role_store.get_role("telegram", "zhangsan") == DEFAULT_ROLE


# ── set_role ──────────────────────────────────────────────────────────


class TestSetRole:
    def test_set_valid_role_returns_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """set_role with a valid role returns True and persists."""
        p = _make_path(tmp_path, monkeypatch)
        assert role_store.set_role("wecom", "lisi", "admin") is True
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["wecom"]["lisi"] == "admin"

    def test_set_invalid_role_returns_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """set_role with an invalid role returns False and does NOT persist."""
        p = _make_path(tmp_path, monkeypatch)
        assert role_store.set_role("wecom", "lisi", "superadmin") is False
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            assert "lisi" not in data.get("wecom", {})

    def test_set_guest_role(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """set_role with 'guest' is valid."""
        p = _make_path(tmp_path, monkeypatch)
        assert role_store.set_role("wecom", "visitor", "guest") is True
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["wecom"]["visitor"] == "guest"

    def test_set_overwrites_existing(self, seed_admin: Path):
        """set_role overwrites an existing role mapping."""
        assert role_store.set_role("wecom", "zhangsan", "teacher") is True
        assert role_store.get_role("wecom", "zhangsan") == "teacher"

    def test_set_creates_platform_if_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """set_role creates a new platform entry if it doesn't exist."""
        p = _make_path(tmp_path, monkeypatch)
        role_store.set_role("telegram", "user_t", "student")
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["telegram"]["user_t"] == "student"

    def test_set_role_case_normalized(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """set_role lowercases and strips the role name."""
        p = _make_path(tmp_path, monkeypatch)
        assert role_store.set_role("wecom", "testuser", "  ADMIN  ") is True
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["wecom"]["testuser"] == "admin"

    def test_set_role_empty_string_invalid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """set_role with empty string is invalid."""
        assert role_store.set_role("wecom", "testuser", "") is False


# ── unset_role ────────────────────────────────────────────────────────


class TestUnsetRole:
    def test_unset_existing_user_returns_true(self, seed_admin: Path):
        """unset_role on an existing user returns True and removes the entry."""
        assert role_store.unset_role("wecom", "zhangsan") is True
        assert role_store.get_role("wecom", "zhangsan") == DEFAULT_ROLE

    def test_unset_non_existent_user_returns_false(self):
        """unset_role on a non-existent user returns False."""
        assert role_store.unset_role("wecom", "nobody") is False

    def test_unset_removes_empty_platform(self, seed_admin: Path):
        """unset_role removes the platform key when it becomes empty."""
        role_store.unset_role("wecom", "zhangsan")
        data = role_store._load_roles()
        assert "wecom" not in data

    def test_unset_does_not_affect_other_platforms(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """unset_role on one platform does not affect another."""
        p = _make_path(tmp_path, monkeypatch)
        p.write_text(
            json.dumps({"wecom": {"user_a": "admin"}, "telegram": {"user_b": "teacher"}}),
            encoding="utf-8",
        )
        role_store.unset_role("wecom", "user_a")
        data = json.loads(p.read_text(encoding="utf-8"))
        assert "wecom" not in data
        assert data["telegram"]["user_b"] == "teacher"

    def test_unset_then_set_round_trip(self, seed_admin: Path):
        """Unset then set the same user works correctly."""
        role_store.unset_role("wecom", "zhangsan")
        assert role_store.get_role("wecom", "zhangsan") == DEFAULT_ROLE
        role_store.set_role("wecom", "zhangsan", "teacher")
        assert role_store.get_role("wecom", "zhangsan") == "teacher"


# ── resolve_role ──────────────────────────────────────────────────────


class TestResolveRole:
    def test_owner_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """resolve_role returns 'owner' when HERMES_OWNER matches user_id."""
        _make_path(tmp_path, monkeypatch)
        monkeypatch.setenv("HERMES_OWNER", "lizhen")
        assert role_store.resolve_role("wecom", "lizhen") == ROLE_OWNER

    def test_owner_wecom_home_channel(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """resolve_role returns 'owner' when WECOM_HOME_CHANNEL matches user_id."""
        _make_path(tmp_path, monkeypatch)
        monkeypatch.setenv("WECOM_HOME_CHANNEL", "lizhen")
        assert role_store.resolve_role("wecom", "lizhen") == ROLE_OWNER

    def test_owner_not_matching_non_owner(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """resolve_role does NOT return owner when env var does not match user_id."""
        _make_path(tmp_path, monkeypatch)
        monkeypatch.setenv("HERMES_OWNER", "reallizhen")
        # A different user should not be owner
        assert role_store.resolve_role("wecom", "impostor") != ROLE_OWNER

    def test_explicit_role_overrides_default(self, seed_admin: Path):
        """resolve_role returns the explicit role from roles.json."""
        assert role_store.resolve_role("wecom", "zhangsan") == "admin"

    def test_no_owner_no_explicit_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """resolve_role returns DEFAULT_ROLE when no owner match and no explicit role."""
        _make_path(tmp_path, monkeypatch)
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        assert role_store.resolve_role("wecom", "anyone") == DEFAULT_ROLE

    def test_legacy_config_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """resolve_role ignores legacy config — roles.json is the only source."""
        _make_path(tmp_path, monkeypatch)
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        config = {"roles": {"wecom": {"legacy_user": "teacher"}}}
        # Config is ignored — falls back to DEFAULT_ROLE (student)
        assert role_store.resolve_role("wecom", "legacy_user", config=config) == DEFAULT_ROLE

    def test_explicit_role_still_works(self, seed_admin: Path):
        """resolve_role returns explicit roles.json role regardless of config."""
        config = {"roles": {"wecom": {"zhangsan": "guest"}}}
        # zhangsan is admin in seed_admin — explicit wins, config ignored
        assert role_store.resolve_role("wecom", "zhangsan", config=config) == "admin"

    def test_owner_env_overrides_explicit(self, seed_admin: Path, monkeypatch: pytest.MonkeyPatch):
        """resolve_role returns owner even when roles.json has a different role."""
        monkeypatch.setenv("HERMES_OWNER", "zhangsan")
        assert role_store.resolve_role("wecom", "zhangsan") == ROLE_OWNER

    def test_config_owner_detection_removed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """resolve_role no longer detects owner from config — env vars only."""
        _make_path(tmp_path, monkeypatch)
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        config = {"platforms": {"wecom": {"extra": {"home_channel": "boss"}}}}
        # Config is ignored — falls back to DEFAULT_ROLE
        assert role_store.resolve_role("wecom", "boss", config=config) == DEFAULT_ROLE


# ── list_roles / list_all_assignments ─────────────────────────────────


class TestListRoles:
    def test_list_roles_known_platform(self, seed_admin: Path):
        """list_roles returns all assignments for a platform."""
        roles = role_store.list_roles("wecom")
        assert roles == {"zhangsan": "admin"}

    def test_list_roles_unknown_platform(self):
        """list_roles returns empty dict for a platform with no roles."""
        assert role_store.list_roles("telegram") == {}

    def test_list_all_assignments(self, seed_admin: Path):
        """list_all_assignments returns the full mapping."""
        all_roles = role_store.list_all_assignments()
        assert all_roles == {"wecom": {"zhangsan": "admin"}}

    def test_list_all_assignments_empty(self):
        """list_all_assignments returns empty dict when no file exists."""
        assert role_store.list_all_assignments() == {}


# ── Atomic write safety ──────────────────────────────────────────────


class TestAtomicWrite:
    def test_atomic_write_creates_parent_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """set_role creates parent directories if they don't exist."""
        deep_path = tmp_path / "nested" / "subdir" / "roles.json"
        monkeypatch.setattr(role_store, "ROLES_PATH", deep_path)
        assert role_store.set_role("wecom", "user", "student") is True
        assert deep_path.exists()

    def test_atomic_write_does_not_corrupt_on_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """If the write fails, the original file is not corrupted (tmp file approach)."""
        p = _make_path(tmp_path, monkeypatch)
        p.write_text(json.dumps({"wecom": {"safe": "admin"}}), encoding="utf-8")
        role_store.set_role("wecom", "new_user", "teacher")
        assert role_store.get_role("wecom", "new_user") == "teacher"


# ── Negative / edge cases ────────────────────────────────────────────


class TestEdgeCases:
    def test_invalid_role_not_in_valid_roles(self):
        """set_role with a role name outside VALID_ROLES is rejected."""
        for bad_role in ("superadmin", "moderator", "user", "robot", ""):
            assert bad_role not in VALID_ROLES, f"{bad_role!r} should not be in VALID_ROLES"

    def test_owner_cannot_be_set_via_set_role(self):
        """set_role should allow 'owner' (even though normally assigned via env)."""
        # The module allows 'owner' as a valid role in set_role
        assert ROLE_OWNER in VALID_ROLES

    def test_resolve_role_non_wecom_platform_no_owner_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """resolve_role on non-wecom platform with no env var returns default."""
        _make_path(tmp_path, monkeypatch)
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        assert role_store.resolve_role("telegram", "someone") == DEFAULT_ROLE

    def test_all_valid_roles_are_strings(self):
        """All valid roles should be non-empty strings."""
        for r in VALID_ROLES:
            assert isinstance(r, str) and len(r) > 0

    def test_default_role_is_student(self):
        """The default role should be 'student'."""
        assert DEFAULT_ROLE == ROLE_STUDENT

    def test_load_roles_oserror_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """_load_roles returns {} on OSError (e.g. permission denied)."""
        p = _make_path(tmp_path, monkeypatch)
        p.write_text(json.dumps({"wecom": {"a": "admin"}}), encoding="utf-8")
        # Make file unreadable
        p.chmod(0o000)
        try:
            result = role_store._load_roles()
            assert result == {}
        finally:
            p.chmod(0o644)


# ── 平台级默认角色（D15 / §4.7.5，v1.5）────────────────────────────────


class TestPlatformDefaults:
    def test_default_role_for_miniapp_is_guest(self):
        """default_role_for('miniapp') == guest；其它平台仍是 student。"""
        assert role_store.default_role_for("miniapp") == ROLE_GUEST
        assert role_store.default_role_for("wecom") == ROLE_STUDENT
        assert role_store.default_role_for("telegram") == ROLE_STUDENT

    def test_unregistered_miniapp_user_resolves_to_guest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """未登记的小程序用户 = guest（D15）——不能上传、不可列。"""
        _make_path(tmp_path, monkeypatch)
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        assert role_store.resolve_role("miniapp", "openid-new") == ROLE_GUEST
        assert role_store.get_role("miniapp", "openid-new") == ROLE_GUEST

    def test_unregistered_wecom_user_still_student(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """企业微信未登记用户仍是 student（D15 只改小程序）。"""
        _make_path(tmp_path, monkeypatch)
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        assert role_store.resolve_role("wecom", "someone") == ROLE_STUDENT

    def test_registered_miniapp_student_not_downgraded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """M2 回归网：已登记为 student 的小程序用户仍为 student，不得静默降级成 guest。

        改法前 `resolve_role` 用 `stored != DEFAULT_ROLE`（"student"）判定「是否登记」，
        小程序默认值改成 guest 后这类用户会被判为未登记 → 降级。
        """
        p = _make_path(tmp_path, monkeypatch)
        p.write_text(json.dumps({"miniapp": {"openid-student": "student"}}), encoding="utf-8")
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        assert role_store.resolve_role("miniapp", "openid-student") == ROLE_STUDENT
        assert role_store.resolve_role("miniapp", "openid-other") == ROLE_GUEST

    def test_registered_miniapp_teacher_admin_unaffected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """已登记 teacher / admin 的小程序用户不受默认值改动影响。"""
        p = _make_path(tmp_path, monkeypatch)
        p.write_text(json.dumps({
            "miniapp": {"openid-teacher": "teacher", "openid-admin": "admin"},
        }), encoding="utf-8")
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        assert role_store.resolve_role("miniapp", "openid-teacher") == ROLE_TEACHER
        assert role_store.resolve_role("miniapp", "openid-admin") == ROLE_ADMIN

    def test_unset_role_audits_platform_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """unset_role 的审计写 default_role_for(platform)，而非模块常量 student。"""
        p = _make_path(tmp_path, monkeypatch)
        p.write_text(json.dumps({"miniapp": {"openid-x": "admin"}}), encoding="utf-8")
        events = []

        class _FakeAudit:
            def log_event(self, **kwargs):
                events.append(kwargs)

        monkeypatch.setattr(role_store, "_role_audit_logger", _FakeAudit())
        assert role_store.unset_role("miniapp", "openid-x") is True
        assert len(events) == 1
        assert events[0]["role"] == ROLE_GUEST
        assert events[0]["detail"]["new_role"] == ROLE_GUEST
        assert role_store.resolve_role("miniapp", "openid-x") == ROLE_GUEST
