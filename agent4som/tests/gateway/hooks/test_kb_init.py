"""kb-init 启动钩子测试（103 行 handler，此前零覆盖）。

导入约束：handler 在 import 时校验 AGENT4SOM_HOME / QWEN_EMBEDDING_URL，
环境变量必须在 import 前设置（文件顶部完成）。
全部离线：Chroma/Sqlite/审计/BM25 均替换为 fake，不触网络与真实数据库。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("AGENT4SOM_HOME", str(_REPO_ROOT))
os.environ.setdefault("QWEN_EMBEDDING_URL", "http://embedding-test:8001/v1")

# 注意：不能 `from gateway.hooks.kb_init import handler` —— conftest 已把
# ~/.hermes/hermes-agent 插到 sys.path 首位，那里的 gateway 包会遮蔽 agent4som
# 自身的 gateway 包。按文件路径直接加载。
import importlib.util

_handler_path = _REPO_ROOT / "gateway" / "hooks" / "kb_init" / "handler.py"
_spec = importlib.util.spec_from_file_location("kb_init_handler_under_test", _handler_path)
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)


def _run_handle(event_type: str, context: dict) -> None:
    asyncio.run(handler.handle(event_type, context))


def _make_fakes(monkeypatch, tmp_path: Path) -> dict:
    """为 handle 的全部外部依赖注入 fake，返回调用记录。"""
    from knowledge_base.repository import chroma_repository as chroma_mod
    from knowledge_base.core import sqlite_store as sqlite_mod
    from knowledge_base.retrieval import response_verifier as verifier_mod
    from knowledge_base.auth import role_store as role_mod
    from knowledge_base.retrieval import bm25_search as bm25_mod

    calls: dict[str, list] = {k: [] for k in
                              ("client", "store", "init_instance",
                               "audit", "role_audit", "bm25")}

    class _FakeClient:
        pass

    class _FakeAudit:
        pass

    monkeypatch.setattr(handler, "_CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(chroma_mod, "get_chroma_client",
                        lambda path: (calls["client"].append(path), _FakeClient())[1])
    monkeypatch.setattr(sqlite_mod, "SqliteStore",
                        lambda path: (calls["store"].append(path), object())[1])
    monkeypatch.setattr(handler.ChromaRepository, "init_instance",
                        classmethod(lambda cls, client, **kw: (
                            calls["init_instance"].append((client, kw)), None)[1]))
    monkeypatch.setattr(verifier_mod, "init_audit_logger",
                        lambda db_path: (calls["audit"].append(db_path), _FakeAudit())[1])
    monkeypatch.setattr(role_mod, "init_role_audit",
                        lambda audit: calls["role_audit"].append(audit))

    async def fake_preload():
        calls["bm25"].append(1)
        return 7

    monkeypatch.setattr(bm25_mod, "preload_bm25_index", fake_preload)
    return calls


# ── 事件过滤 ────────────────────────────────────────────────────────


def test_ignores_non_startup_events(monkeypatch, tmp_path):
    """非 gateway:startup 事件直接返回，不初始化任何组件。"""
    calls = _make_fakes(monkeypatch, tmp_path)
    _run_handle("gateway:shutdown", {})
    _run_handle("tool:registered", {})
    assert calls["init_instance"] == []
    assert calls["bm25"] == []


# ── 启动初始化 ──────────────────────────────────────────────────────


def test_startup_initializes_all_components(monkeypatch, tmp_path):
    """gateway:startup → 单例/审计/角色审计/BM25 预热全部装载。"""
    calls = _make_fakes(monkeypatch, tmp_path)
    _run_handle("gateway:startup", {})

    # chroma 目录已创建
    assert (tmp_path / "chroma").is_dir()
    # 单例以 HTTP client + embedding + sqlite store 初始化
    assert len(calls["init_instance"]) == 1
    client, kwargs = calls["init_instance"][0]
    assert client is not None
    assert "embedding_function" in kwargs and "sqlite_store" in kwargs
    # sqlite/audit 落在 chroma 目录的同级
    assert calls["store"][0] == str(tmp_path / "quota.db")
    assert calls["audit"][0] == str(tmp_path / "audit.db")
    # role_store 审计拿到的是 init_audit_logger 的返回值
    assert calls["role_audit"][0] is not None
    # BM25 预热被执行
    assert calls["bm25"] == [1]


def test_startup_survives_bm25_preload_failure(monkeypatch, tmp_path):
    """BM25 预热失败只降级告警，不影响启动（不抛出）。"""
    from knowledge_base.retrieval import bm25_search as bm25_mod

    calls = _make_fakes(monkeypatch, tmp_path)

    async def broken_preload():
        raise RuntimeError("chroma unreachable")

    monkeypatch.setattr(bm25_mod, "preload_bm25_index", broken_preload)
    _run_handle("gateway:startup", {})  # 不应抛异常
    assert len(calls["init_instance"]) == 1


# ── build_embedding_function ────────────────────────────────────────


def test_build_embedding_function_is_fallback_composition():
    from knowledge_base.repository.embedding_providers import (
        BuiltinEmbeddingFunction,
        FallbackEmbeddingFunction,
        OpenAICompatibleEmbeddingFunction,
    )

    fn = handler.build_embedding_function()
    assert isinstance(fn, FallbackEmbeddingFunction)
    assert isinstance(fn._primary, OpenAICompatibleEmbeddingFunction)
    assert isinstance(fn._fallback, BuiltinEmbeddingFunction)


# ── import 时环境守卫（子进程隔离，避免污染本进程模块状态） ────────


def _import_in_subprocess(extra_env: dict[str, str]) -> "subprocess.CompletedProcess[bytes]":
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(_REPO_ROOT)}
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", "import gateway.hooks.kb_init.handler"],
        env=env, capture_output=True,
        check=False,  # 失败信息由调用方从 returncode/stderr 断言
    )


def test_import_requires_agent4som_home():
    r = _import_in_subprocess({"QWEN_EMBEDDING_URL": "http://x:1/v1"})
    assert r.returncode != 0
    assert b"AGENT4SOM_HOME" in r.stderr


def test_import_requires_embedding_url():
    r = _import_in_subprocess({"AGENT4SOM_HOME": str(_REPO_ROOT)})
    assert r.returncode != 0
    assert b"QWEN_EMBEDDING_URL" in r.stderr
