"""scripts/eval_ragas.py 纯逻辑测试（此前零覆盖，离线）。

不建检索器、不调 LLM 判官：只测基准解析、查询扩展、MMR 多样性、
分数融合、跨 run 平均与失败兜底（ragas 栈不可用时告警不抛）。
"""
import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "scripts" / "eval_ragas.py"
_spec = importlib.util.spec_from_file_location("eval_ragas_under_test", _SRC)
er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(er)


# ── parse_benchmark：TSV 基准解析 ──────────────────────────────────


def test_parse_benchmark_filters(tmp_path):
    p = tmp_path / "benchmark.md"
    p.write_text(
        "序号\t问题\t标准答案\n"
        "\n"
        "1\t转专业条件?\t须满足成绩要求\n"
        "2\t补考规定?\t次学期初报名\n"
        "x\t无序号行\t跳过\n"
        "3\t列不足\n",
        encoding="utf-8")
    rows = er.parse_benchmark(str(p))
    assert rows == [
        {"question": "转专业条件?", "ground_truth": "须满足成绩要求"},
        {"question": "补考规定?", "ground_truth": "次学期初报名"},
    ]


# ── 查询扩展 ───────────────────────────────────────────────────────


def test_expand_query_synonyms():
    assert er._expand_query("分流") == "分流 专业选择 志愿填报"
    assert er._expand_query("宿舍") == "宿舍"
    out = er._expand_query("保研 与 竞赛")
    assert "推免加分 B类 A类 竞赛加分" in out


# ── MMR 多样性 ─────────────────────────────────────────────────────


def test_mmr_diversify_prefers_different_source():
    docs = [f"chunk{i}" for i in range(6)]
    metas = [{"source_file": "A.doc"}, {"source_file": "A.doc"},
             {"source_file": "B.doc"}, {"source_file": "C.doc"},
             {"source_file": "A.doc"}, {"source_file": "D.doc"}]
    out = er._mmr_diversify(docs, metas, top_k=3, diversity_weight=0.3)
    # 首选永远是最前排 chunk0；随后避免同源连续堆叠
    assert out[0] == "chunk0"
    sources = []
    for d in out:
        i = docs.index(d)
        sources.append(metas[i]["source_file"])
    assert sources[1] != "A.doc"   # 同源惩罚生效，第二选来自其他文档

    # 不超过 top_k 时原样返回
    assert er._mmr_diversify(docs[:2], metas[:2], top_k=5) == docs[:2]


def test_mmr_diversify_boundary():
    docs = ["a", "b"]
    metas = [{"source_file": "A"}, {"source_file": "A"}]
    assert er._mmr_diversify(docs, metas, top_k=2) == ["a", "b"]


# ── 分数融合 ───────────────────────────────────────────────────────


def test_weighted_fusion_normalizes_and_merges():
    # dense 仅一个（range 退化为 1.0）；bm25 两档归一后加权
    fused = er._weighted_fusion(
        dense_indices=[0], dense_scores=[0.4],
        bm25_indices=[0, 1], bm25_scores=[2.0, 4.0],
        bm25_weight=0.3, top_k=2)
    assert fused[0] == 1   # idx0 = 0.7*1.0 + 0.3*0.0 = 0.7；idx1 = 0.3*1.0 = 0.3
    assert fused[1] == 0
    # 单路缺失时另一路独立排序
    fused = er._weighted_fusion([], [], [2, 3], [1.0, 5.0], top_k=2)
    assert fused == [3, 2]


# ── 跨 run 平均 ────────────────────────────────────────────────────


def test_average_scores_per_question():
    all_cp = [[1.0, 0.0], [0.5, 0.5], [0.0, 1.0]]
    assert er._average_scores(all_cp, 3, 2) == [0.5, 0.5]


# ── 关闭态的外部服务调用 ───────────────────────────────────────────


def test_step_back_and_rerank_disabled_passthrough(monkeypatch):
    # 配置缺失（URL/模型名为空）→ 不发请求：step-back None、rerank 原样截断
    monkeypatch.setattr(er, "_STEP_BACK_ENABLED", False)
    assert er._step_back_rewrite("查询") is None
    monkeypatch.setattr(er, "_RERANK_URL", "")
    docs = ["a", "b", "c"]
    assert er._bge_rerank("q", docs, top_k=2) == ["a", "b"]


# ── RAGAS 评测失败兜底 ─────────────────────────────────────────────


def test_run_ragas_eval_swallow_failure(capsys, monkeypatch):
    """依赖栈 import 失败 → 告警 + traceback，不上抛。"""
    import builtins

    real_import = builtins.__import__

    def _no_ragas(name, *a, **kw):
        if name == "ragas":
            raise ImportError("ragas not installed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_ragas)
    er._run_ragas_eval(["q"], [["ctx"]], ["gt"], "detail.json")
    out = capsys.readouterr().out
    assert "RAGAS LLM evaluation failed" in out
    assert "Raw retrieval results saved to detail.json" in out
