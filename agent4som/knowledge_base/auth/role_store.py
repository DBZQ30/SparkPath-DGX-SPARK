"""
Role Store — lightweight user-to-role mapping backed by a JSON file.

This module provides a simple, file-based role persistence layer.
Roles are stored as ``{platform: {user_id: role_name}}`` in a JSON file.

Default roles:
  - ``owner``:   unrestricted access (all platform tools)
  - ``admin``:   can use web/file/skills/todo/rag
  - ``teacher``: can use file/skills/todo/rag/vision
  - ``student``: can use file/skills/todo/rag  (default for paired users)
  - ``guest``:   can use rag/skills only
"""

from __future__ import annotations
from typing import Dict

import fcntl
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Optional audit logger — set via init_role_audit() by kb_init hook or CLI.
# When None (default), role changes are logged via standard logging only.
_role_audit_logger = None


def init_role_audit(audit_logger):
    """Inject an AuditLogger instance for role-change auditing.

    Called by kb_init hook at Gateway startup and by admin_cli on first use.
    When set, set_role() / unset_role() write structured audit events in
    addition to standard log messages.
    """
    global _role_audit_logger
    _role_audit_logger = audit_logger

# Default location: HERMES_HOME/roles.json
_HERMES_HOME = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
ROLES_PATH = _HERMES_HOME / "roles.json"

# Well-known role names
ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_TEACHER = "teacher"
ROLE_STUDENT = "student"
ROLE_GUEST = "guest"

DEFAULT_ROLE = ROLE_STUDENT

# 平台级默认角色（D15）：小程序未登记用户 = 访客，未知平台仍是学生。
DEFAULT_ROLE_BY_PLATFORM = {"miniapp": ROLE_GUEST}


def default_role_for(platform: str) -> str:
    """Return the default role for *platform* (``DEFAULT_ROLE`` if unlisted)."""
    return DEFAULT_ROLE_BY_PLATFORM.get(platform, DEFAULT_ROLE)


VALID_ROLES = {ROLE_OWNER, ROLE_ADMIN, ROLE_TEACHER, ROLE_STUDENT, ROLE_GUEST}


# NOTE (D-4): Role data is read from roles.json on every resolve_role() call,
# so admin_cli roles set takes effect immediately.  There is no cache invalidation
# mechanism for active gateway sessions — mid-conversation role changes are visible
# on the next tool call, which is acceptable for the current single-VM deployment.
# If horizontal scaling is added, roles.json should be replaced with a shared store
# (e.g. SQLite or Redis) with pub/sub invalidation.

def _load_roles() -> dict[str, dict[str, str]]:
    """Load roles from ``roles.json``.  Returns ``{}`` on any error."""
    if not ROLES_PATH.exists():
        return {}
    try:
        raw = ROLES_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            logger.warning("roles.json is not a dict — ignoring")
            return {}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load roles.json: %s", exc)
        return {}


def _save_roles(data: dict[str, dict[str, str]]) -> None:
    """Atomically write roles to ``roles.json`` (P2-2 fix: file-locked)."""
    ROLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ROLES_PATH.with_suffix(".json.lock")
    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            tmp = ROLES_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(ROLES_PATH)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def get_role(platform: str, user_id: str) -> str:
    """Return the role for *user_id* on *platform*, or the platform default.

    The *owner* role is auto-assigned if the user is the configured
    WECOM_HOME_CHANNEL or HERMES_OWNER (see :func:`resolve_role`).
    """
    roles = _load_roles()
    platform_roles = roles.get(platform, {})
    return platform_roles.get(user_id, default_role_for(platform))


def set_role(platform: str, user_id: str, role: str) -> bool:
    """Assign *role* to *user_id* on *platform*.

    Returns ``True`` on success, ``False`` if *role* is not in ``VALID_ROLES``.
    """
    role = role.lower().strip()
    if role not in VALID_ROLES:
        return False
    old_role = get_role(platform, user_id)
    data = _load_roles()
    data.setdefault(platform, {})[user_id] = role
    _save_roles(data)
    # Audit the role change (P2-3 fix)
    logger.info("Role changed: platform=%s user=%s %s→%s", platform, user_id, old_role, role)
    if _role_audit_logger:
        try:
            _role_audit_logger.log_event(
                event_type="role_change",
                user_id=user_id,
                role=role,
                detail={"platform": platform, "old_role": old_role, "new_role": role},
            )
        except Exception:
            logger.warning("Failed to write role_change audit event", exc_info=True)
    return True


def unset_role(platform: str, user_id: str) -> bool:
    """Remove any explicit role for *user_id*, reverting to the platform default.

    Returns ``True`` if a role was removed, ``False`` if none was set.
    """
    old_role = get_role(platform, user_id)
    data = _load_roles()
    platform_roles = data.get(platform, {})
    if user_id not in platform_roles:
        return False
    del platform_roles[user_id]
    if not platform_roles:
        data.pop(platform, None)
    _save_roles(data)
    logger.info("Role removed: platform=%s user=%s (was %s)", platform, user_id, old_role)
    if _role_audit_logger:
        try:
            fallback = default_role_for(platform)
            _role_audit_logger.log_event(
                event_type="role_change",
                user_id=user_id,
                role=fallback,
                detail={"platform": platform, "old_role": old_role, "new_role": fallback},
            )
        except Exception:
            logger.warning("Failed to write role_change audit event", exc_info=True)
    return True


def resolve_role(platform: str, user_id: str, config: dict | None = None) -> str:
    """Resolve the effective role for a user.

    Resolution order:
    1. If *user_id* matches the **owner** (WECOM_HOME_CHANNEL env var or
       ``HERMES_OWNER`` env var) → ``owner``
    2. If *user_id* has an explicit role in ``roles.json`` → that role
    3. Otherwise → :func:`default_role_for` (miniapp = guest, else student)

    Note: *config* is accepted for backward compatibility but no longer used
    for role resolution.  All role assignments should go through roles.json
    or the ``admin_cli.py roles`` commands.
    """
    _ = config  # accepted for backward compatibility, no longer used

    # 1. Owner auto-detection
    owner_id = _detect_owner(platform)
    if owner_id and user_id == owner_id:
        return ROLE_OWNER

    # 2. roles.json explicit mapping — get_role already falls back to the
    #    platform default, so no comparison against DEFAULT_ROLE here: a
    #    miniapp user explicitly registered as "student" would otherwise
    #    look unregistered and silently downgrade to guest (M2).
    return get_role(platform, user_id)


def _detect_owner(platform: str) -> str | None:
    """Return the owner's user_id if detectable, else None.

    Checks (in order):
      - ``HERMES_OWNER`` env var
      - ``WECOM_HOME_CHANNEL`` env var (WeCom only)
    """
    env_owner = os.getenv("HERMES_OWNER", "").strip()
    if env_owner:
        return env_owner

    if platform == "wecom":
        wecom_home = os.getenv("WECOM_HOME_CHANNEL", "").strip()
        if wecom_home:
            return wecom_home

    return None


# ── Convenience helpers for the /promote skill ──────────────────────────

def list_roles(platform: str) -> Dict[str, str]:
    """Return all explicit role assignments for *platform*."""
    data = _load_roles()
    return dict(data.get(platform, {}))


def list_all_assignments() -> dict[str, dict[str, str]]:
    """Return the full role mapping dict ``{platform: {user_id: role}}``."""
    return _load_roles()
