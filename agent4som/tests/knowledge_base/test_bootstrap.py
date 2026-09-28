"""bootstrap.py 组件工厂测试（192 行，此前零直接覆盖）。

离线：embedding/Chroma 用注入 fake，不触网络与真实数据库。
"""

from __future__ import annotations


import pytest

from knowledge_base import bootstrap
from knowledge_base.repository.chroma_repository import ChromaRepository


# ── load_dotenv ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_dotenv_flag(monkeypatch):
    """每个用例重置幂等标记，允许重新加载不同的 env 文件。"""
    monkeypatch.setattr(bootstrap, "_dotenv_loaded", False)


def test_load_dotenv_parses_basic_forms(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# 注释行\n"
        "PLAIN=value1\n"
        'QUOTED="value2"\n'
        "export EXPORTED=value3\n"
        "\n"
        "INVALID_LINE_NO_EQUALS\n"
    )
    monkeypatch.delenv("PLAIN", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)
    monkeypatch.delenv("EXPORTED", raising=False)

    bootstrap.load_dotenv(str(env))

    import os
    assert os.environ["PLAIN"] == "value1"
    assert os.environ["QUOTED"] == "value2"
    assert os.environ["EXPORTED"] == "value3"


def test_load_dotenv_never_overwrites_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("BOOTSTRAP_TEST_VAR=from_file\n")
    monkeypatch.setenv("BOOTSTRAP_TEST_VAR", "from_env")

    bootstrap.load_dotenv(str(env))

    import os
    assert os.environ["BOOTSTRAP_TEST_VAR"] == "from_env"  # setdefault 语义


def test_load_dotenv_idempotent(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("A_FIRST_LOAD=1\n")
    monkeypatch.delenv("A_FIRST_LOAD", raising=False)

    bootstrap.load_dotenv(str(env))
    # 第二次加载（幂等标记生效）不应重新读文件——改文件内容也不生效
    env.write_text("A_FIRST_LOAD=2\n")
    bootstrap.load_dotenv(str(env))

    import os
    assert os.environ["A_FIRST_LOAD"] == "1"


def test_load_dotenv_missing_file_is_noop(tmp_path):
    bootstrap.load_dotenv(str(tmp_path / "not-exist.env"))
    # 不抛错即通过


# ── build_embedding_function ────────────────────────────────────────


def test_build_embedding_function_requires_env(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_EMBEDDING_URL", raising=False)
    with pytest.raises(RuntimeError, match="QWEN_API_KEY"):
        bootstrap.build_embedding_function()


def test_build_embedding_function_reads_env(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "sk-test")
    monkeypatch.setenv("QWEN_EMBEDDING_URL", "http://localhost:8001/v1")
    monkeypatch.setenv("QWEN_EMBEDDING_MODEL", "qwen3-embedding")
    fn = bootstrap.build_embedding_function()
    assert fn is not None


# ── create_ingestion_orchestrator（注入 fake repo） ────────────────


class _FakeRepo:
    """IngestionOrchestrator 构造即拆包依赖，只需可挂属性的占位。"""


def test_create_ingestion_orchestrator_wires_dependencies():
    orch = bootstrap.create_ingestion_orchestrator(repo=_FakeRepo())
    assert orch is not None
    # QuotaManager / VersionManager / ExtractorRouter 均已注入
    assert orch._quota is not None
    assert orch._version is not None


def test_init_singleton_short_circuits_when_ready(monkeypatch):
    """已初始化时 init_chroma_repository_singleton 不重复创建。"""
    calls = []

    def _boom(*a, **kw):
        calls.append(1)
        raise AssertionError("不应重新创建 singleton")

    monkeypatch.setattr(bootstrap, "create_chroma_repository", _boom)
    # 用一个假的"已就绪"状态：直接挂 _instance
    ChromaRepository._instance = object()  # type: ignore[attr-defined]
    try:
        bootstrap.init_chroma_repository_singleton()
        assert calls == []
    finally:
        ChromaRepository._instance = None  # type: ignore[attr-defined]
