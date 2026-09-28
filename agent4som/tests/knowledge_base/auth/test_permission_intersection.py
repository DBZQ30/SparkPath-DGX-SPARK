"""
Negative tests for the role-toolset intersection logic in
``_get_platform_tools()``.

IMPORTANT: These tests are SKIPPED because ``_get_platform_tools()`` does
NOT accept a ``role`` parameter in Hermes v0.14+.  Per architecture review
C-5, the strategy shifted from "gateway-layer role filtering" (patching the
Hermes function) to "tool-internal role filtering" (ACLFilter inside each
tool).  See ``docs/01-architecture/som-kb-role-acl-architecture.md§5.6``.

The test code is preserved as a reference for the original design
contract.  Remove this ``skip`` marker if Hermes ever supports ``role``.

Test philosophy (negative):
  - Unauthorized roles MUST have their toolset correctly restricted
  - Authorized roles (owner, None, unknown, "*") MUST NOT be restricted
  - Empty / missing ``role_toolsets`` config MUST NOT restrict
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

pytestmark = pytest.mark.skip(reason="_get_platform_tools() lacks role param; gateway-layer role filtering descoped in favor of tool-internal ACLFilter (C-5)")

from hermes_cli.tools_config import _get_platform_tools


# ── Helpers ───────────────────────────────────────────────────────────


def _make_config(
    platform_tools: list[str] | None = None,
    role_toolsets: Dict[str, list[str]] | None = None,
) -> Dict[str, Any]:
    """Build a minimal config dict for testing role intersection.

    Uses the ``cli`` platform with an explicit toolset list so the
    resolution path is deterministic and fast.
    """
    cfg: Dict[str, Any] = {}
    if platform_tools is not None:
        cfg["platform_toolsets"] = {"cli": platform_tools}
    if role_toolsets is not None:
        cfg["role_toolsets"] = role_toolsets
    return cfg


# ── Positive: role does NOT restrict ──────────────────────────────────


class TestNoRestriction:
    """Cases where the role filter must NOT intersect the toolset."""

    def test_no_role_returns_all_platform_tools(self):
        """``role=None`` — no role-based filtering applied."""
        config = _make_config(
            platform_tools=["web", "file", "skills"],
        )
        enabled = _get_platform_tools(config, "cli")
        assert "web" in enabled
        assert "file" in enabled
        assert "skills" in enabled

    def test_owner_bypasses_intersection(self):
        """``role="owner"`` must return all platform tools unchanged."""
        config = _make_config(
            platform_tools=["web", "terminal"],
            role_toolsets={"guest": ["skills"]},
        )
        enabled = _get_platform_tools(config, "cli", role="owner")
        assert "web" in enabled
        assert "terminal" in enabled

    def test_unknown_role_no_restriction(self):
        """A role not present in ``role_toolsets`` gets no restrictions."""
        config = _make_config(
            platform_tools=["web", "file"],
            role_toolsets={"guest": ["skills"]},
        )
        enabled = _get_platform_tools(config, "cli", role="nonexistent_role")
        assert "web" in enabled
        assert "file" in enabled

    def test_wildcard_allowed_all_tools(self):
        """A role with ``"*"`` in its allowed list gets all platform tools."""
        config = _make_config(
            platform_tools=["web", "terminal", "file"],
            role_toolsets={"admin": ["*"]},
        )
        enabled = _get_platform_tools(config, "cli", role="admin")
        assert "web" in enabled
        assert "terminal" in enabled
        assert "file" in enabled

    def test_empty_role_toolsets_config_no_restriction(self):
        """When config has no ``role_toolsets`` key, role param is ignored."""
        config = _make_config(
            platform_tools=["web", "file"],
            # no role_toolsets
        )
        enabled = _get_platform_tools(config, "cli", role="student")
        assert "web" in enabled
        assert "file" in enabled

    def test_missing_role_toolsets_key_no_restriction(self):
        """``role_toolsets: null`` must not crash and not restrict."""
        config = _make_config(
            platform_tools=["web", "file"],
        )
        config["role_toolsets"] = None
        enabled = _get_platform_tools(config, "cli", role="student")
        assert "web" in enabled
        assert "file" in enabled


# ── Negative: correct intersection ────────────────────────────────────


class TestCorrectIntersection:
    """Cases where the role MUST intersect the toolset."""

    def test_guest_role_only_skills(self):
        """Guest role (``["skills"]``) restricts to skills only."""
        config = _make_config(
            platform_tools=["web", "terminal", "file", "skills", "todo"],
            role_toolsets={"guest": ["skills"]},
        )
        enabled = _get_platform_tools(config, "cli", role="guest")
        assert enabled == {"skills"}

    def test_role_with_multiple_allowed_tools(self):
        """Role with multiple allowed toolsets keeps the intersection."""
        config = _make_config(
            platform_tools=["web", "terminal", "file", "skills", "todo", "vision"],
            role_toolsets={"teacher": ["file", "skills", "todo", "vision"]},
        )
        enabled = _get_platform_tools(config, "cli", role="teacher")
        assert enabled == {"file", "skills", "todo", "vision"}

    def test_student_default_role_correct_intersection(self):
        """Student role (file/skills/todo) correctly intersected."""
        config = _make_config(
            platform_tools=["web", "terminal", "file", "skills", "todo"],
            role_toolsets={"student": ["file", "skills", "todo"]},
        )
        enabled = _get_platform_tools(config, "cli", role="student")
        assert enabled == {"file", "skills", "todo"}

    def test_no_overlap_returns_empty(self):
        """Role whose allowed tools have no overlap with platform → empty."""
        config = _make_config(
            platform_tools=["web", "terminal"],
            role_toolsets={"guest": ["skills"]},
        )
        enabled = _get_platform_tools(config, "cli", role="guest")
        assert enabled == set()

    def test_admin_role_has_web_but_no_terminal(self):
        """Admin has ``web`` but not ``terminal`` in role_toolsets."""
        config = _make_config(
            platform_tools=["web", "terminal", "file", "skills", "todo"],
            role_toolsets={"admin": ["web", "file", "skills", "todo"]},
        )
        enabled = _get_platform_tools(config, "cli", role="admin")
        assert "web" in enabled
        assert "terminal" not in enabled
        assert "file" in enabled
        assert "skills" in enabled
        assert "todo" in enabled

    def test_role_toolset_names_are_normalized_to_str(self):
        """Role toolset names may be stored as str; intersection works."""
        config = _make_config(
            platform_tools=["web", "file"],
            role_toolsets={"limited": ["file"]},
        )
        enabled = _get_platform_tools(config, "cli", role="limited")
        assert enabled == {"file"}


# ── Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary conditions for the role intersection."""

    def test_empty_role_list_allows_nothing(self):
        """A role with an empty allowed list gets no platform tools."""
        config = _make_config(
            platform_tools=["web", "file", "skills"],
            role_toolsets={"restricted": []},
        )
        enabled = _get_platform_tools(config, "cli", role="restricted")
        assert enabled == set()

    def test_owner_role_ignores_empty_role_toolsets(self):
        """Owner bypasses even when role_toolsets has restrictive entries."""
        config = _make_config(
            platform_tools=["web", "terminal"],
            role_toolsets={"owner": ["skills"]},  # irrelevant — owner bypasses
        )
        enabled = _get_platform_tools(config, "cli", role="owner")
        assert "web" in enabled
        assert "terminal" in enabled

    def test_real_world_wecom_role_config(self):
        """Verify the production role_toolsets config from config.yaml works."""
        config = _make_config(
            platform_tools=["web", "terminal", "file", "skills", "todo", "rag", "vision"],
            role_toolsets={
                "admin": ["web", "file", "skills", "todo", "rag"],
                "teacher": ["file", "skills", "todo", "rag", "vision"],
                "student": ["file", "skills", "todo", "rag"],
                "guest": ["rag", "skills"],
            },
        )
        assert _get_platform_tools(config, "cli", role="admin") == {
            "web", "file", "skills", "todo", "rag",
        }
        assert _get_platform_tools(config, "cli", role="teacher") == {
            "file", "skills", "todo", "rag", "vision",
        }
        assert _get_platform_tools(config, "cli", role="student") == {
            "file", "skills", "todo", "rag",
        }
        assert _get_platform_tools(config, "cli", role="guest") == {
            "rag", "skills",
        }
        assert _get_platform_tools(config, "cli", role="owner") == {
            "web", "terminal", "file", "skills", "todo", "rag", "vision",
        }
