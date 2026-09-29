"""会话隔离（Session Isolation）测试：QueryContext contextvars + 角色泄漏回归。

覆盖：
- N01 QueryContext（contextvars）跨并发 Task / 线程不串号
- N02 角色泄漏回归（docs/04-security/tool-defs-cache-role-bleed.md）：
     Hermes 工具清单缓存角色无关是上游已知问题（未修）；
     agent4som 工具层的权限判定必须**逐次执行**（不缓存决策结果），
     即使学生被"广告"出 knowledge_ingest，执行时也拦得住。
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from knowledge_base.core.query_context import (
    QueryContext,
    current_context,
    inject_context,
)

pytestmark = [pytest.mark.security]


@pytest.fixture(autouse=True)
def _reset_context():
    inject_context(QueryContext())
    yield
    inject_context(QueryContext())


# ── N01：contextvars 隔离 ──────────────────────────────────────────


def test_context_isolated_across_asyncio_tasks():
    """并发 Task 各自注入的 user_id 互不可见（asyncio.Task 复制上下文）。"""
    results: dict[str, str] = {}

    async def worker(user: str, barrier: asyncio.Barrier):
        inject_context(QueryContext(platform="wecom", user_id=user))
        for _ in range(50):  # 制造交错窗口
            await asyncio.sleep(0)
        results[user] = current_context().user_id
        await barrier.wait()

    async def main():
        barrier = asyncio.Barrier(3)
        await asyncio.gather(
            worker("alice", barrier), worker("bob", barrier), worker("carol", barrier))

    asyncio.run(main())
    assert results == {"alice": "alice", "bob": "bob", "carol": "carol"}


def test_context_isolated_across_threads():
    """线程各自注入互不串扰（contextvars 是线程本地的默认快照）。"""
    results: dict[str, str] = {}
    errors: list[BaseException] = []

    def worker(user: str):
        try:
            inject_context(QueryContext(platform="wecom", user_id=user))
            for _ in range(1000):
                pass  # 忙等制造交错
            results[user] = current_context().user_id
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(u,))
               for u in ("alice", "bob", "carol")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert results == {"alice": "alice", "bob": "bob", "carol": "carol"}


def test_default_context_is_empty_guest():
    """未注入时（上游适配器漏 inject_context）默认空 user + guest，不残留上一请求。"""
    inject_context(QueryContext(platform="wecom", user_id="someone"))
    # 模拟新请求线程/任务的默认快照：contextvars 不跨线程传播
    seen: dict[str, str] = {}

    def probe():
        ctx = current_context()
        seen["user_id"] = ctx.user_id
        seen["role"] = ctx.role

    t = threading.Thread(target=probe)
    t.start()
    t.join()
    assert seen["user_id"] == ""
    assert seen["role"] == "guest" or seen["user_id"] != "someone"


# ── N02：角色泄漏回归（agent4som 工具层补偿性防线） ────────────────


class _BoomOrch:
    """ingest_file 一旦被调用即抛错——权限应在其之前拦下。"""

    def ingest_file(self, *a, **kw):  # pragma: no cover
        raise AssertionError("student 写 global 本不应到达 ingest_file")


def _knowledge_ingest_module():
    """加载 hermes_overlay 的 knowledge_ingest（依赖上游 ``tools.registry``）。

    Hermes 框架（``~/.hermes/hermes-agent``）未安装时该模块不可导入 ——
    CI 是干净 checkout、不装框架，此时跳过依赖它的用例而非报错
    （口径同 tests/knowledge_base/auth/test_gateway_role_resolution.py）。
    """
    import importlib.util
    from pathlib import Path

    try:
        import tools.registry  # noqa: F401
    except ModuleNotFoundError as exc:  # pragma: no cover - CI 环境分支
        pytest.skip(f"Hermes 框架未安装（~/.hermes/hermes-agent 缺失）：{exc}")

    src = Path(__file__).resolve().parents[2] / "hermes_overlay" / "tools" / "knowledge_ingest.py"
    spec = importlib.util.spec_from_file_location("ki_isolation_probe", src)
    ki = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ki)
    return ki


def test_ingest_permission_rechecked_per_call():
    """同一 worker 上角色混杂调用：权限决策不是一次性缓存。

    学生 → global 拒；随后的 admin → global 放行；再来的学生 → 仍拒。
    若权限结果被（角色无关地）缓存，第二个学生的调用会被错误放行。
    """
    ki = _knowledge_ingest_module()

    boom = _BoomOrch()

    # 学生：拒（且不触发 ingest_file）
    denied = ki._ingest_one(boom, "student-1", "/x/f.pdf", "f.pdf", "global", "student")
    assert "权限不足" in denied

    # admin：放行路径（权限通过，ingest_file 被真实调用）
    class _OkOrch:
        def __init__(self):
            self.calls = 0

        def ingest_file(self, *a, **kw):
            self.calls += 1
            from knowledge_base.ingestion.orchestrator import IngestionStatus
            return type("R", (), {
                "status": IngestionStatus.INGESTED, "node_count": 1,
                "file_size_bytes": 1, "notification": "", "error_detail": ""})()

    from knowledge_base.retrieval import response_verifier
    original_audit = response_verifier._audit
    response_verifier._audit = None
    try:
        ok = _OkOrch()
        out = ki._ingest_one(ok, "adm-1", "/x/f.pdf", "f.pdf", "global", "admin")
        assert "SUCCESS" in out and ok.calls == 1

        # 学生再次调用：必须再次拒（不能沿用上次的 admin 决策）
        denied2 = ki._ingest_one(boom, "student-2", "/x/f.pdf", "f.pdf", "global", "student")
        assert "权限不足" in denied2
    finally:
        response_verifier._audit = original_audit


def test_cross_user_personal_scope_still_denied_after_admin_call():
    """admin 调用后，学生写他人个人库仍拒（时序不产生权限残留）。"""
    ki = _knowledge_ingest_module()

    denied = ki._ingest_one(_BoomOrch(), "s1", "/x/f.pdf", "f.pdf",
                            "users/victim", "student")
    assert "权限不足" in denied
