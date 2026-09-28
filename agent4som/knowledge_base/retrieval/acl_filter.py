"""ACL Filter — 学业规划助手三种知识库权限模型。

Scope model:
  - ``global``:    公共知识库，全员可读
  - ``teachers``:  教师知识库，admin + teacher 可读写
  - ``users/{id}``: 个人知识库，本人可读写，admin 可读（审计记录）

Roles: admin, teacher, student, guest

This is the **single source of truth** for all permission logic.
- ``WRITE_PERMISSIONS`` — who can write to which scope
- ``get_allowed_scopes()`` — who can read from which scope
- ``derive_default_scope()`` — which scope to use by default for a role
- Gateway hooks and tools delegate to these; no inline permission checks elsewhere.
"""

from __future__ import annotations

import logging
from typing import ClassVar
import re

logger = logging.getLogger(__name__)


class UnauthorizedAccessError(Exception):
    def __init__(self, message: str, user_id: str = "", role: str = "", detail: dict | None = None):
        super().__init__(message)
        self.user_id = user_id
        self.role = role
        self.detail = detail or {}


# ── Centralized permission matrices ──────────────────────────────────────

# Write permission: which scopes each role can write to.
# NOTE: "users" means the user's OWN personal KB (users/{self}), not any users/ scope.
# No role can write to another user's personal KB — that's private space.
WRITE_PERMISSIONS = {
    "owner":   {"global", "teachers", "users"},
    "admin":   {"global", "teachers", "users"},
    "teacher": {"teachers", "users"},
    "student": {"users"},
    "guest":   set(),
}

# Default ingestion scope per role (used by tools/hooks when user doesn't specify)
DEFAULT_SCOPE_BY_ROLE = {
    "admin":   "global",
    "owner":   "global",
    "teacher": "teachers",
    # student/guest: falls back to f"users/{user_id}"
}


def derive_default_scope(role: str, user_id: str) -> str:
    """Return the default ingestion scope for *role*.

    Used by knowledge_ingest tool and kb_init watcher to avoid hardcoded
    role-to-scope mappings scattered across the codebase.
    """
    if role in DEFAULT_SCOPE_BY_ROLE:
        return DEFAULT_SCOPE_BY_ROLE[role]
    return f"users/{user_id}"


# Valid user_id characters — must match _VALID_SCOPE_RE in chroma_repository
# so users/{id} scopes pass storage-layer validation.
_VALID_USER_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")


def check_write_permission(role: str, scope: str, user_id: str) -> None:
    """Gateway-layer write gate.

    Raises UnauthorizedAccessError if *role* is not allowed to write to *scope*.
    Unknown scopes are rejected (fail-closed) — they would otherwise fail
    later at the storage layer with a misleading "ChromaDB write failed".
    """
    allowed = WRITE_PERMISSIONS.get(role, set())

    if scope == "global":
        if "global" not in allowed:
            raise UnauthorizedAccessError(
                "只有管理员可以管理公共知识库。",
                role=role,
                detail={"scope": scope, "reason": "global_write_denied"},
            )
    elif scope == "teachers":
        if "teachers" not in allowed:
            raise UnauthorizedAccessError(
                "只有教师和管理员可以管理教师知识库。",
                role=role,
                detail={"scope": scope, "reason": "teachers_write_denied"},
            )
    elif scope.startswith("users/"):
        target_user = scope.split("/", 1)[1]
        # Reject user_ids with characters the storage layer will refuse
        # (Chinese, spaces, @, etc.) — fail early with a clear message.
        if not _VALID_USER_ID_RE.match(target_user):
            raise UnauthorizedAccessError(
                f"用户ID含非法字符，无法创建个人知识库：{target_user!r}",
                user_id=user_id,
                role=role,
                detail={"scope": scope, "reason": "invalid_user_id"},
            )
        # Only the owner of a personal KB can write to it — not even admin/owner
        if "users" in allowed and user_id == target_user:
            return
        raise UnauthorizedAccessError(
            "只能管理自己的个人知识库。",
            user_id=user_id,
            role=role,
            detail={"scope": scope, "reason": "user_write_denied"},
        )
    else:
        # Unknown scope format — fail closed instead of silently passing
        # through to fail later at the storage layer.
        raise UnauthorizedAccessError(
            f"未知的 scope: {scope}",
            user_id=user_id,
            role=role,
            detail={"scope": scope, "reason": "unknown_scope"},
        )


# ── ACLFilter ───────────────────────────────────────────────────────────

class ACLFilter:
    """Data-plane ACL for 学业规划助手 three-KB model.

    Read-permission matrix::

        | 角色    | global | teachers | users/{self} | users/{他人}    |
        |---------|--------|----------|--------------|----------------|
        | admin   | ✅     | ✅       | ✅           | ✅ (审计记录)    |
        | teacher | ✅     | ✅       | ✅           | ❌             |
        | student | ✅     | ❌       | ✅           | ❌             |
        | guest   | ✅     | ❌       | ❌           | ❌ (预留)       |
    """

    def __init__(self, current_user_id: str, current_role: str):
        self.user_id = current_user_id
        self.role = current_role

    # Roles that have full access to all user scopes (for auditing etc.)
    _FULL_ACCESS_ROLES: ClassVar[set[str]] = {"admin", "owner"}

    def get_allowed_scopes(self, all_user_scopes: list[str] | None = None) -> list[str]:
        """Return scopes visible to the current user (read path).

        When *all_user_scopes* is provided and the role is admin/owner,
        all existing ``users/*`` scopes are included for cross-user
        auditing.  Callers should obtain this list via
        ``ChromaRepository.get_all_user_scopes()``.
        """
        scopes = ["global"]

        if self.role in self._FULL_ACCESS_ROLES:
            scopes.append("teachers")
            if self.user_id:
                scopes.append(f"users/{self.user_id}")
            # admin/owner can read all users' personal KBs for auditing
            if all_user_scopes:
                scopes.extend(s for s in all_user_scopes if s not in scopes)
            return scopes

        if self.role == "teacher":
            scopes.append("teachers")
            if self.user_id:
                scopes.append(f"users/{self.user_id}")
            return scopes

        if self.role == "student":
            if self.user_id:
                scopes.append(f"users/{self.user_id}")
            return scopes

        if self.role == "guest":
            return scopes  # global only

        return scopes

    def can_access_users_scope(self) -> bool:
        """Check whether the current user can read *other* users' data.

        Admin and owner can — all such accesses are audited.
        """
        return self.role in self._FULL_ACCESS_ROLES

    def validate_requested_scope(self, requested_scope: str):
        """Raise UnauthorizedAccessError if the user cannot read *requested_scope*."""
        if requested_scope in ("global", "teachers"):
            return  # handled by get_allowed_scopes caller

        if requested_scope.startswith("users/"):
            target_user = requested_scope.split("/", 1)[1]
            if self.role in self._FULL_ACCESS_ROLES:
                logger.info(
                    "ACL: admin/owner cross-user read — actor=%s target=%s scope=%s",
                    self.user_id, target_user, requested_scope,
                )
                return  # admin/owner can read any user (audited)
            if self.role in ("teacher", "student") and target_user == self.user_id:
                return
            raise UnauthorizedAccessError(
                "无法读取其他用户的个人知识库。",
                user_id=self.user_id,
                role=self.role,
                detail={"requested_scope": requested_scope, "reason": "cross_user_read_denied"},
            )

        raise UnauthorizedAccessError(
            f"未知的 scope: {requested_scope}",
            user_id=self.user_id,
            role=self.role,
            detail={"requested_scope": requested_scope, "reason": "unknown_scope"},
        )
