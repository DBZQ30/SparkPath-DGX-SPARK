"""tests/security 公共夹具：全离线、角色文件与 DB 路径隔离。

安全套件按通用安全规范（OWASP 类别）组织：
authentication / authorization / input-validation / injection /
audit-tamper / secrets / session-isolation。

已知未修复问题（保持 skip，见 docs/04-security/）：
- tool-dispatch-authz-bypass.md（Hermes 执行层越权）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_base.auth import role_store

REPO_ROOT = Path(__file__).resolve().parents[2]   # agent4som/
MONOREPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _isolate_role_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """角色文件指向临时位置，任何角色变更都不落真实 roles.json。"""
    monkeypatch.setattr(role_store, "ROLES_PATH", tmp_path / "roles.json")
    yield


@pytest.fixture
def seed_roles(tmp_path: Path):
    """预置角色：wecom 平台 zhangsan=admin、lisi=teacher、wangwu=student。"""
    roles_file = tmp_path / "roles.json"
    roles_file.write_text(
        json.dumps({"wecom": {"zhangsan": "admin", "lisi": "teacher",
                              "wangwu": "student"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return roles_file
