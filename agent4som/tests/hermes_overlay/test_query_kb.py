"""hermes_overlay/tools/query_kb.py 检索管线单元测试（此前零覆盖）。

离线：不初始化 Chroma、不触 embedding/rerank 真实服务——
管线 helper 用假 repo / 假节点直测，handle 级用例 monkeypatch ``_retrieve``。
``_retrieve`` 的 Chroma 未就绪守卫在离线环境天然走早退分支。
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "hermes_overlay" / "tools" / "query_kb.py"
_spec = importlib.util.spec_from_file_location("query_kb_under_test", _SRC)
qk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qk)


@dataclass
class FakeNode:
    node_id: str = ""
    scope: str = "global"
    source_file: str = "文件.pdf"
    section_title: str = ""
    page_start: int | None = None
    sheet_name: str = ""
    row_start: int | None = None
    row_end: int | None = None
    content: str = ""
    source_path: str = ""


@pytest.fixture(autouse=True)
def _reset_query_context():
    # QueryContext contextvar 全局可见——用完复位，别污染其他用例
    from knowledge_base.core.query_context import QueryContext, inject_context
    inject_context(QueryContext())
    yield
    inject_context(QueryContext())


# ── 参数校验（LLM 网关不保证执行 JSON Schema 约束）──────────────────


def test_validate_args_rejects_empty_and_short():
    for bad in ("", "   ", "x"):
        err = qk._validate_knowledge_search_args({"query": bad})
        assert err and "参数错误" in err
    # query 缺失键同样拒绝
    assert qk._validate_knowledge_search_args({}) is not None
    assert qk._validate_knowledge_search_args({"query": "转专业申请条件"}) is None


# ── Query Expansion / 无命中文案 ────────────────────────────────────


def test_expand_query_appends_synonyms():
    assert qk._expand_query("分流") == "分流 专业选择 志愿填报"
    # 多命中词累积追加
    out = qk._expand_query("大类招生 调课")
    assert out.startswith("大类招生 调课 ")
    assert "专业选择" in out and "课程调整 调停课" in out
    # 无命中原样返回
    assert qk._expand_query("宿舍分配") == "宿舍分配"


def test_empty_result_message_scope_labels():
    msg = qk._empty_result_message(["global", "teachers", "users/u1"])
    assert msg == "暂未收录该内容（已检索 公共知识库、教师知识库、个人知识库），请联系教务办公室。"
    # 未知 scope 原样展示（不 KeyError）
    assert "自定义库" in qk._empty_result_message(["自定义库"])


# ── ACL scope 白名单（真实 ACLFilter + 假 repo）────────────────────


class _FakeRepo:
    def __init__(self, all_user_scopes=None):
        self._all = all_user_scopes or []
        self.all_scopes_calls = 0

    def get_all_user_scopes(self):
        self.all_scopes_calls += 1
        return list(self._all)


def test_resolve_scopes_student_no_cross_user():
    repo = _FakeRepo(all_user_scopes=["users/other"])
    scopes = qk._resolve_scopes(repo, "u1", "student")
    assert scopes == ["global", "users/u1"]
    assert repo.all_scopes_calls == 0   # 非 admin 不做跨全体查询


def test_resolve_scopes_admin_includes_all_users():
    repo = _FakeRepo(all_user_scopes=["users/x", "users/u1", "users/y"])
    scopes = qk._resolve_scopes(repo, "u1", "admin")
    assert scopes == ["global", "teachers", "users/u1", "users/x", "users/y"]
    assert repo.all_scopes_calls == 1


# ── Dense + Step-Back 双查询融合 ────────────────────────────────────


class _FakeSearchRepo:
    def __init__(self, results):
        self._results = results          # node_id → 返回节点列表
        self.calls = []

    def search_nodes(self, scopes, query_embedding, top_k, include_distances):
        self.calls.append((tuple(scopes), tuple(query_embedding), top_k))
        # 用 embedding 首元素区分查询来源
        return list(self._results[query_embedding[0]])


def test_search_with_stepback_embedding_failure(monkeypatch):
    monkeypatch.setattr(qk, "_get_embedding", lambda q: [])
    out = qk._search_with_stepback(_FakeSearchRepo({}), ["global"], "q", 5)
    assert out is None   # 调用方转『无法向量化查询』文案


def test_search_with_stepback_fusion_dedup(monkeypatch):
    monkeypatch.setattr(qk, "_step_back_rewrite", lambda q: "回退查询")
    emb = {"q": [1.0], "回退查询": [2.0]}
    monkeypatch.setattr(qk, "_get_embedding", lambda q: emb[q])
    repo = _FakeSearchRepo({1.0: [FakeNode(node_id="a"), FakeNode(node_id="b")],
                            2.0: [FakeNode(node_id="b"), FakeNode(node_id="c")]})
    nodes = qk._search_with_stepback(repo, ["global"], "q", 5)
    # 两路结果合并去重：a、b 只出现一次，c 来自 step-back 路
    assert [n.node_id for n in nodes] == ["a", "b", "c"]
    assert [c[2] for c in repo.calls] == [20, 20]   # top_k=max(5,20)


def test_search_with_stepback_no_rewrite(monkeypatch):
    monkeypatch.setattr(qk, "_step_back_rewrite", lambda q: None)
    monkeypatch.setattr(qk, "_get_embedding", lambda q: [1.0])
    repo = _FakeSearchRepo({1.0: [FakeNode(node_id="a")]})
    nodes = qk._search_with_stepback(repo, ["global"], "q", 5)
    assert [n.node_id for n in nodes] == ["a"]
    assert len(repo.calls) == 1


def test_search_with_stepback_secondary_failure_tolerated(monkeypatch):
    """step-back 路 repo 异常 → 告警回退原始结果（不向上抛）。"""
    monkeypatch.setattr(qk, "_step_back_rewrite", lambda q: "回退查询")
    emb = {"q": [1.0], "回退查询": [2.0]}
    monkeypatch.setattr(qk, "_get_embedding", lambda q: emb[q])

    class _BoomRepo(_FakeSearchRepo):
        def search_nodes(self, scopes, query_embedding, top_k, include_distances):
            if query_embedding[0] == 2.0:
                raise RuntimeError("chroma down")
            return super().search_nodes(scopes, query_embedding, top_k,
                                        include_distances)

    repo = _BoomRepo({1.0: [FakeNode(node_id="a")]})
    nodes = qk._search_with_stepback(repo, ["global"], "q", 5)
    assert [n.node_id for n in nodes] == ["a"]


# ── BM25 混排 ───────────────────────────────────────────────────────


def test_bm25_hybrid_no_index_passthrough(monkeypatch):
    import knowledge_base.retrieval.bm25_search as bm

    class _EmptyIdx:
        _total_docs = 0

    monkeypatch.setattr(bm, "get_bm25_index", lambda: _EmptyIdx())
    nodes = [FakeNode(node_id="a")]
    assert qk._bm25_hybrid(nodes, "查询") is nodes   # 索引未建 → 原样返回


def test_bm25_hybrid_delegates_weights(monkeypatch):
    import knowledge_base.retrieval.bm25_search as bm

    class _Idx:
        _total_docs = 50

    recorded = {}

    def _hybrid(idx, nodes, query, top_k, bm25_weight):
        recorded.update(top_k=top_k, weight=bm25_weight, query=query)
        return [nodes[-1]]

    monkeypatch.setattr(bm, "get_bm25_index", lambda: _Idx())
    monkeypatch.setattr(bm, "hybrid_search", _hybrid)
    nodes = [FakeNode(node_id=str(i)) for i in range(20)]
    out = qk._bm25_hybrid(nodes, "转专业")
    assert out == [nodes[-1]]
    assert recorded == {"top_k": 15, "weight": 0.3, "query": "转专业"}


# ── Rerank 池 → 截断 → ACL 复筛 ────────────────────────────────────


class _ReverseReranker:
    def rerank(self, query, nodes, top_k):
        self.seen_pool = list(nodes)
        return list(reversed(nodes))[:top_k]


def test_rerank_pool_orders_trim_and_refilters(monkeypatch):
    rr = _ReverseReranker()
    monkeypatch.setattr(qk, "_build_reranker", lambda: rr)
    nodes = [FakeNode(node_id=str(i)) for i in range(10)]
    nodes[9].scope = "users/other"      # 混入跨 scope 节点（rerank 排首位）
    out, pool = qk._rerank_pool("q", nodes, top_k=5,
                                allowed_scopes=["global", "users/self"])
    # reranker 看到全部输入节点（返回池上限 pool_size=min(8, len)=8）
    assert len(rr.seen_pool) == 10
    # 倒序取 8 → [9..2]，截 top_k=5 → [9,8,7,6,5]；users/other(9) 被末端 ACL 复筛剔除
    assert [n.node_id for n in out] == ["8", "7", "6", "5"]
    # 检索池同步过滤（供 context stitching 扩展邻居，无跨 scope 节点）
    assert all(n.scope != "users/other" for n in pool)
    assert len(pool) == 9


def test_rerank_pool_reranker_failure_falls_back(monkeypatch):
    def _boom():
        raise RuntimeError("rerank svc down")
    monkeypatch.setattr(qk, "_build_reranker", _boom)
    nodes = [FakeNode(node_id=str(i)) for i in range(3)]
    out, pool = qk._rerank_pool("q", nodes, top_k=2, allowed_scopes=["global"])
    assert [n.node_id for n in out] == ["0", "1"]   # 未重排，仅截断
    assert len(pool) == 3


def test_build_passages_safe_failure_keeps_nodes(monkeypatch):
    import knowledge_base.retrieval.context_stitcher as cs
    monkeypatch.setattr(cs, "build_passages",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    nodes = [FakeNode(node_id="a")]
    assert qk._build_passages_safe(nodes, nodes) is nodes


# ── _retrieve 守卫（离线环境 Chroma 未就绪 → 早退文案）──────────────


def test_retrieve_returns_error_string_when_kb_not_ready():
    from knowledge_base.repository.chroma_repository import ChromaRepository
    assert ChromaRepository.is_ready() is False   # 离线套件前提
    out = qk._retrieve("转专业", top_k=5)
    assert out == "知识库暂时不可用，请稍后再试。如持续异常请联系 IT 支持。"


# ── handle_knowledge_search：校验 → 中继 → 审计 → 格式化 ───────────


def test_handle_validation_failure_short_circuits(monkeypatch):
    monkeypatch.setattr(qk, "_retrieve", lambda *a, **kw: pytest.fail("不应进入检索"))
    out = qk.handle_knowledge_search({"query": "  "})
    assert out.startswith("参数错误")


def test_handle_relays_retrieve_error_string(monkeypatch):
    monkeypatch.setattr(qk, "_retrieve", lambda q, top_k: "知识库检索出错，请稍后再试。")
    assert qk.handle_knowledge_search({"query": "转专业"}) == "知识库检索出错，请稍后再试。"


def test_handle_empty_and_formats(monkeypatch):
    monkeypatch.setattr(qk, "_retrieve", lambda q, top_k: [])
    assert qk.handle_knowledge_search({"query": "转专业"}) == \
        "暂未收录该内容，请联系教务办公室。"

    nodes = [
        FakeNode(node_id="a", source_file="办法.pdf", section_title="第二章",
                 page_start=3, content="正文一"),
        FakeNode(node_id="b", source_file="名单.xlsx", sheet_name="Sheet1",
                 row_start=2, row_end=5, content="学号列表"),
        FakeNode(node_id="c", source_file="通知.pdf", content="详情见 来源: https://jwc.xjtu.edu.cn/n.doc"),
    ]
    monkeypatch.setattr(qk, "_retrieve", lambda q, top_k: nodes)
    out = qk.handle_knowledge_search({"query": "转专业", "top_k": 3})
    lines = out.split("\n\n---\n\n")
    assert lines[0] == "参考1: [办法.pdf] - 第二章 - 第3页\n正文一"
    assert lines[1] == "参考2: [名单.xlsx] - Sheet=Sheet1 行2-5\n学号列表"
    assert lines[2] == ("参考3: [通知.pdf] - https://jwc.xjtu.edu.cn/n.doc\n"
                        "详情见 来源: https://jwc.xjtu.edu.cn/n.doc")


def test_log_search_audit_records_event(monkeypatch):
    from knowledge_base.core.query_context import QueryContext, inject_context
    from knowledge_base.retrieval import response_verifier

    inject_context(QueryContext(user_id="u9", platform="wecom"))

    events = []

    class _FakeAudit:
        def log_event(self, **kw):
            events.append(kw)

    monkeypatch.setattr(response_verifier, "_audit", _FakeAudit())
    nodes = [FakeNode(node_id="a", source_file="办法.pdf", source_path="global/x")]
    qk._log_search_audit("转专业", nodes, top_k=5, latency_ms=42)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "search" and ev["user_id"] == "u9"
    assert ev["query"] == "转专业" and ev["latency_ms"] == 42
    assert ev["result_count"] == 1 and ev["scopes_hit"] == ["global/x"]
    assert ev["result_sources"] == ["办法.pdf"]
    # _audit 缺失（未初始化）时静默跳过
    monkeypatch.setattr(response_verifier, "_audit", None)
    qk._log_search_audit("转专业", nodes, top_k=5, latency_ms=1)
    assert len(events) == 1
