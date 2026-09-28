"""
Negative tests for ``GatewayRunner._resolve_user_role()``.

IMPORTANT: These tests are SKIPPED because ``_resolve_user_role()`` was
NEVER implemented in Hermes ``gateway/run.py``.  Per architecture review
C-5, the strategy shifted from "gateway-layer role filtering" (patching
Hermes) to "tool-internal role filtering" (ACLFilter inside each tool).
See ``docs/01-architecture/som-kb-role-acl-architecture.md§5.6`` and the
``shared_skills/auth-manager/SKILL.md`` for the current approach.

The test code is preserved as a reference for the original design
contract.  Remove ``skipif`` if Hermes ever adds ``_resolve_user_role``.

Kept tests:
1. Owner env vars (HERMES_OWNER / WECOM_HOME_CHANNEL)
2. ``roles.json`` file mapping
3. Legacy config-based role mapping
4. Default fallback (no role)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest

# All tests skipped: Hermes never implemented _resolve_user_role();
# strategy changed to tool-internal filtering per C-5.
pytestmark = pytest.mark.skip(reason="Hermes GatewayRunner._resolve_user_role() never implemented; gateway-layer role filtering descoped in favor of tool-internal ACLFilter (C-5)")
# conftest 只有在 ~/.hermes/hermes-agent 存在时才加 sys.path；开发机未装
# Hermes 框架时这两行 import 会 ModuleNotFoundError（收集期中断整个测试运行），
# 改为 importorskip 让本文件整体跳过而非报错。
pytest.importorskip("gateway.config", reason="Hermes 框架未安装（~/.hermes/hermes-agent 缺失）")
from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner, SessionSource


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def runner() -> GatewayRunner:
    """A bare GatewayRunner with default config — no side effects."""
    return GatewayRunner(config=GatewayConfig())


def make_source(platform: Platform, user_id: Optional[str] = "test_user") -> SessionSource:
    """Create a minimal SessionSource with the given platform and user_id."""
    return SessionSource(
        platform=platform,
        chat_id="test_chat",
        user_id=user_id,
        user_name="test",
    )


# ── Tests: platform / user_id guards ──────────────────────────────────


class TestGuards:
    """Cases that short-circuit and return None."""

    def test_homeassistant_platform_returns_none(self, runner: GatewayRunner):
        """HOMEASSISTANT platform returns None (no role filtering)."""
        source = make_source(Platform.HOMEASSISTANT)
        assert runner._resolve_user_role(source, {}) is None

    def test_webhook_platform_returns_none(self, runner: GatewayRunner):
        """WEBHOOK platform returns None (no role filtering)."""
        source = make_source(Platform.WEBHOOK)
        assert runner._resolve_user_role(source, {}) is None

    def test_no_user_id_returns_none(self, runner: GatewayRunner):
        """source with no user_id returns None."""
        source = make_source(Platform.WECOM, user_id=None)
        assert runner._resolve_user_role(source, {}) is None


# ── Tests: owner auto-detection ───────────────────────────────────────


class TestOwnerDetection:
    """Owner detection via env vars."""

    def test_hermes_owner_env(self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch):
        """HERMES_OWNER matching user_id returns 'owner'."""
        monkeypatch.setenv("HERMES_OWNER", "lizhen")
        source = make_source(Platform.WECOM, user_id="lizhen")
        assert runner._resolve_user_role(source, {}) == "owner"

    def test_hermes_owner_not_matching(self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch):
        """HERMES_OWNER not matching user_id does NOT return owner."""
        monkeypatch.setenv("HERMES_OWNER", "reallizhen")
        source = make_source(Platform.WECOM, user_id="impostor")
        assert runner._resolve_user_role(source, {}) is None

    def test_wecom_home_channel_env(self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch):
        """WECOM_HOME_CHANNEL matching user_id returns 'owner'."""
        monkeypatch.setenv("WECOM_HOME_CHANNEL", "lizhen")
        source = make_source(Platform.WECOM, user_id="lizhen")
        assert runner._resolve_user_role(source, {}) == "owner"

    def test_owner_env_prefers_hermes_owner(self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch):
        """HERMES_OWNER takes precedence over WECOM_HOME_CHANNEL."""
        monkeypatch.setenv("HERMES_OWNER", "primary")
        monkeypatch.setenv("WECOM_HOME_CHANNEL", "secondary")
        source = make_source(Platform.WECOM, user_id="primary")
        assert runner._resolve_user_role(source, {}) == "owner"
        source2 = make_source(Platform.WECOM, user_id="secondary")
        assert runner._resolve_user_role(source2, {}) != "owner"  # not owner if WECOM_HOME_CHANNEL only


# ── Tests: roles.json mapping ─────────────────────────────────────────


class TestRolesJson:
    """Role resolution from ~/.hermes/roles.json."""

    def test_roles_json_explicit_role(
        self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Explicit role in roles.json is returned."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        roles_file = tmp_path / "roles.json"
        roles_file.write_text(
            json.dumps({"wecom": {"zhangsan": "admin"}}), encoding="utf-8"
        )
        source = make_source(Platform.WECOM, user_id="zhangsan")
        assert runner._resolve_user_role(source, {}) == "admin"

    def test_roles_json_user_not_found(
        self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """User not in roles.json returns None (no role)."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        roles_file = tmp_path / "roles.json"
        roles_file.write_text(
            json.dumps({"wecom": {"zhangsan": "admin"}}), encoding="utf-8"
        )
        source = make_source(Platform.WECOM, user_id="nobody")
        assert runner._resolve_user_role(source, {}) is None

    def test_roles_json_empty_file(
        self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Empty roles.json returns None."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        (tmp_path / "roles.json").write_text("{}", encoding="utf-8")
        source = make_source(Platform.WECOM, user_id="anyone")
        assert runner._resolve_user_role(source, {}) is None

    def test_roles_json_not_exists(
        self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """No roles.json file returns None."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        source = make_source(Platform.WECOM, user_id="anyone")
        assert runner._resolve_user_role(source, {}) is None

    def test_roles_json_different_platform(
        self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Roles for one platform don't leak to another."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        roles_file = tmp_path / "roles.json"
        roles_file.write_text(
            json.dumps({"telegram": {"tg_user": "admin"}}), encoding="utf-8"
        )
        source = make_source(Platform.WECOM, user_id="tg_user")
        assert runner._resolve_user_role(source, {}) is None

    def test_roles_json_owner_still_wins(self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Owner detection takes precedence over roles.json."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_OWNER", "boss")
        roles_file = tmp_path / "roles.json"
        roles_file.write_text(
            json.dumps({"wecom": {"boss": "student"}}), encoding="utf-8"
        )
        source = make_source(Platform.WECOM, user_id="boss")
        assert runner._resolve_user_role(source, {}) == "owner"

    def test_roles_json_corrupted_file(
        self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Corrupted roles.json is silently ignored, returns None."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        (tmp_path / "roles.json").write_text("{{{broken json}}", encoding="utf-8")
        source = make_source(Platform.WECOM, user_id="anyone")
        assert runner._resolve_user_role(source, {}) is None


# ── Tests: legacy config-based role mapping ───────────────────────────


class TestLegacyConfig:
    """Fallback to config.yaml roles.* mapping."""

    def test_legacy_config_role(self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Legacy config role mapping works."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        # No roles.json
        source = make_source(Platform.WECOM, user_id="legacy_user")
        user_config = {"roles": {"wecom": {"legacy_user": "teacher"}}}
        assert runner._resolve_user_role(source, user_config) == "teacher"

    def test_legacy_config_not_used_when_roles_json_exists(self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """roles.json takes precedence over legacy config."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        roles_file = tmp_path / "roles.json"
        roles_file.write_text(
            json.dumps({"wecom": {"user_a": "admin"}}), encoding="utf-8"
        )
        source = make_source(Platform.WECOM, user_id="user_a")
        user_config = {"roles": {"wecom": {"user_a": "guest"}}}
        assert runner._resolve_user_role(source, user_config) == "admin"

    def test_legacy_config_empty(self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Empty legacy config returns None."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        source = make_source(Platform.WECOM, user_id="anyone")
        assert runner._resolve_user_role(source, {}) is None


# ── Tests: default fallback ───────────────────────────────────────────


class TestDefaultFallback:
    """When no role can be determined, returns None (no role filtering)."""

    def test_no_matching_owner_no_roles_json_no_config(self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """No owner match, no roles.json, no legacy config → None."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        source = make_source(Platform.WECOM, user_id="ordinary_user")
        assert runner._resolve_user_role(source, {}) is None

    def test_non_wecom_platform_without_owner(self, runner: GatewayRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Non-wecom platform with no owner config → None."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_OWNER", raising=False)
        monkeypatch.delenv("WECOM_HOME_CHANNEL", raising=False)
        source = make_source(Platform.TELEGRAM, user_id="tg_user")
        assert runner._resolve_user_role(source, {}) is None
