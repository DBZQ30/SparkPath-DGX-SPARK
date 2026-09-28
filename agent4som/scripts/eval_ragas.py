#!/usr/bin/env python3
"""RAGAS evaluation against benchmark.md questions.

Prerequisites: pip install ragas langchain-community langchain-openai
Uses deepseek-chat for evaluation (same LLM as the Gateway).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Patch RAGAS import (vertexai not available) ──────────────────────
class _FakeChatVertexAI:
    pass
import types as _types
_stub = _types.ModuleType("langchain_community.chat_models.vertexai")
_stub.ChatVertexAI = _FakeChatVertexAI
sys.modules["langchain_community.chat_models.vertexai"] = _stub

# ── Load env ──────────────────────────────────────────────────────────
from knowledge_base.bootstrap import load_dotenv, build_embedding_function
load_dotenv()

# Clear SOCKS proxy env vars (set by QODER jump proxy) so requests/httpx
# connect directly to local ChromaDB and external APIs (DeepSeek, etc.)
for _key in ("ALL_PROXY", "QODER_JUMP_PROXY", "all_proxy", "qoder_jump_proxy"):
    os.environ.pop(_key, None)

# ── Parse benchmark.md ────────────────────────────────────────────────
def parse_benchmark(path: str) -> list[dict]:
    """Extract (question, ground_truth) pairs from benchmark.md (TSV format)."""
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("序号\t"):
                continue
            cols = line.split("\t")
            if len(cols) >= 3 and cols[0].isdigit():
                rows.append({"question": cols[1].strip(), "ground_truth": cols[2].strip()})
    return rows

# ── Production pipeline components ────────────────────────────────────

# Query expansion synonym map: when a query term matches the LHS,
# append the RHS expansion to improve recall for domain-specific jargon.
_QUERY_EXPANSIONS = {
    "分流": "专业选择 志愿填报",
    "大类招生": "专业选择 专业分流 主修专业预选择",
    "专业分流": "专业选择",
    "调课": "课程调整 调停课",
    "停课": "调停课 课程调整",
    "审批流程": "申请表 签字 审批",
    "补考": "补考 重修 结业换毕业",
    "选修课": "通识选修课 通识课",
    "预科班": "少数民族预科班 内地班",
    "转专业": "转专业 跨学院转专业 学籍异动",
    "缓考": "缓考 缓考申请 考试冲突",
    "休学": "休学 复学 保留学籍 退学",
    "学分认定": "学分认定 课程置换 成绩替代",
    "结业": "结业 换发毕业证 延长学习时间",
    "保研": "推免 推免加分 B类 A类 竞赛加分",
    "竞赛": "推免加分 B类 A类",
    "大赛": "竞赛 B类 A类 推免加分",
    "国际学生": "汉语 中国概况 必修课 思政替代 军事课程 国防教育",
}

_STEP_BACK_URL = os.environ.get("STEP_BACK_MODEL_URL", os.environ.get("QWEN_CHAT_URL", ""))
_STEP_BACK_MODEL = os.environ.get("STEP_BACK_MODEL_NAME", "")
_STEP_BACK_ENABLED = os.environ.get("STEP_BACK_ENABLED", "true").lower() == "true"
_RERANK_URL = os.environ.get("BGE_RERANKER_URL", "")

_STEP_BACK_PROMPT = (
    "你是一个查询改写助手。给定一个用户提出的关于西安交通大学管理学院教务处的本科生教务管理的问题，"
    "请生成一个更通用、更抽象的回退查询（step-back query），"
    "用于检索相关的政策文件全文。\n\n"
    "规则：\n"
    "1. 回退查询应该比原始问题更宽泛，不要过于具体\n"
    "2. 保留原始问题中的关键实体（如课程名、文件名、文号）\n"
    "3. 用陈述句或短语形式输出，不要用问句\n"
    "4. 只输出回退查询本身，不要加任何解释、前缀或后缀"
)


def _expand_query(query: str) -> str:
    """Apply domain-specific query expansion using synonym map."""
    expanded = query
    for term, expansion in _QUERY_EXPANSIONS.items():
        if term in query and expansion not in expanded:
            expanded = expanded + " " + expansion
    return expanded


def _step_back_rewrite(query: str) -> str | None:
    """Generate a broader step-back query for improved recall."""
    if not _STEP_BACK_ENABLED or not _STEP_BACK_MODEL or not _STEP_BACK_URL:
        return None
    try:
        import requests as _r
        resp = _r.post(
            f"{_STEP_BACK_URL}/chat/completions",
            headers={"Authorization": f"Bearer {os.environ.get('STEP_BACK_API_KEY', os.environ.get('DEEPSEEK_API_KEY', ''))}"},
            json={
                "model": _STEP_BACK_MODEL,
                "messages": [
                    {"role": "system", "content": _STEP_BACK_PROMPT},
                    {"role": "user", "content": f"原始问题：{query}"},
                ],
                "max_tokens": 128, "temperature": 0.1,
            },
            timeout=15,
        )
        resp.raise_for_status()
        rewritten = resp.json()["choices"][0]["message"]["content"].strip()
        return rewritten if len(rewritten) >= 3 and rewritten != query else None
    except Exception:
        return None


def _bge_rerank(query: str, documents: list[str], top_k: int = 5) -> list[str]:
    """Re-rank documents using BGE Reranker API."""
    if not _RERANK_URL or len(documents) <= top_k:
        return documents[:top_k]
    try:
        import requests as _r
        resp = _r.post(
            _RERANK_URL,
            headers={"Authorization": f"Bearer {os.environ.get('QWEN_API_KEY', '')}"},
            json={"query": query, "documents": documents, "top_n": top_k},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        reranked = [documents[r["index"]] for r in sorted(results, key=lambda x: x.get("index", 0))]
        return reranked[:top_k]
    except Exception:
        return documents[:top_k]


def _mmr_diversify(documents: list[str], metadatas: list[dict], top_k: int = 5,
                   diversity_weight: float = 0.3) -> list[str]:
    """MMR diversification: avoid returning all chunks from the same document."""
    if len(documents) <= top_k:
        return documents
    result = []
    remaining = list(range(len(documents)))
    # First pick: top-ranked document
    result.append(remaining.pop(0))
    while len(result) < top_k and remaining:
        best_idx = None
        best_score = -float('inf')
        for i in remaining:
            # Relevance: keep high (index-based proxy)
            relevance = 1.0 / (i + 1)
            # Diversity penalty: already have chunks from same source_file?
            sf_i = metadatas[i].get('source_file', '')
            penalty = max(
                (1.0 for j in result if metadatas[j].get('source_file', '') == sf_i),
                default=0.0,
            )
            score = (1 - diversity_weight) * relevance - diversity_weight * penalty
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx is not None:
            result.append(best_idx)
            remaining.remove(best_idx)
        else:
            result.append(remaining.pop(0))
    return [documents[i] for i in result[:top_k]]


# ── Run retrieval (simulate knowledge_search with full pipeline) ───────
def _tokenize(text: str) -> list[str]:
    """Tokenize Chinese+English text using jieba for Chinese words."""
    import re as _re
    import jieba
    # Extract Chinese segments and English words, tokenize each
    tokens = []
    for segment in _re.split(r'([a-zA-Z0-9]+)', text.lower()):
        if _re.match(r'^[a-zA-Z0-9]+$', segment):
            tokens.append(segment)
        elif segment.strip():
            tokens.extend(jieba.lcut(segment))
    return [t.strip() for t in tokens if t.strip()]


def _build_bm25_index(documents: list[str]) -> object:
    """Build a BM25 index over *documents* using rank_bm25."""
    print("  Tokenizing corpus (jieba)...")
    tokenized = [_tokenize(d) for d in documents]
    try:
        from rank_bm25 import BM25Okapi
        return BM25Okapi(tokenized), tokenized
    except ImportError:
        return None, tokenized


def _bm25_search(index, tokenized_corpus: list[list[str]], query: str, top_k: int = 20):
    """Return (indices, scores) for top_k BM25 matches."""
    import numpy as np
    if index is None:
        return [], []
    q_tokens = _tokenize(query)
    scores = index.get_scores(q_tokens)
    top_indices = np.argsort(scores)[::-1][:top_k]
    return list(top_indices), [float(scores[i]) for i in top_indices]


def _weighted_fusion(dense_indices, dense_scores, bm25_indices, bm25_scores,
                     bm25_weight=0.3, top_k=20):
    """Min-max normalisation + weighted fusion (industry standard)."""
    scores = {}
    if dense_scores:
        d_min, d_max = min(dense_scores), max(dense_scores)
        d_range = d_max - d_min if d_max > d_min else 1.0
        for idx, s in zip(dense_indices, dense_scores, strict=False):
            scores[idx] = ((s - d_min) / d_range) * (1 - bm25_weight)
    if bm25_scores:
        b_min, b_max = min(bm25_scores), max(bm25_scores)
        b_range = b_max - b_min if b_max > b_min else 1.0
        for idx, s in zip(bm25_indices, bm25_scores, strict=False):
            scores[idx] = scores.get(idx, 0) + ((s - b_min) / b_range) * bm25_weight
    return [idx for idx, _ in sorted(scores.items(), key=lambda x: -x[1])[:top_k]]


def _build_retriever():
    """Initialize retriever state: client, embedding function, BM25 index."""
    from knowledge_base.retrieval.acl_filter import ACLFilter
    from knowledge_base.repository.chroma_repository import get_chroma_client, ChromaRepository
    from knowledge_base.core.sqlite_store import SqliteStore

    client = get_chroma_client(os.environ.get("CHROMA_DB_PATH", "data/chroma"))
    ef = build_embedding_function()
    coll = client.get_collection("raw_nodes")
    acl = ACLFilter(current_user_id="XiongWei", current_role="owner")
    scopes = acl.get_allowed_scopes()
    where = {"scope": {"$in": scopes}} if len(scopes) > 1 else {"scope": scopes[0]}

    # Build BM25 index over all documents (one-time cost)
    all_data = coll.get(where=where, include=['documents', 'metadatas'], limit=50000)
    corpus = all_data.get('documents', [])
    all_metas = all_data.get('metadatas', [])
    print(f"  BM25 corpus: {len(corpus)} documents")
    bm25_index, tokenized_corpus = _build_bm25_index(corpus)

    # Init singleton so _retrieve() in query_kb.py can call ChromaRepository.instance()
    sqlite_path = os.path.join(os.path.dirname(os.environ.get("CHROMA_DB_PATH", "data/chroma")), "quota.db")
    ChromaRepository.init_instance(client, embedding_function=ef, sqlite_store=SqliteStore(sqlite_path))

    return {
        'client': client, 'coll': coll, 'ef': ef, 'where': where,
        'bm25_index': bm25_index, 'tokenized_corpus': tokenized_corpus,
        'corpus': corpus, 'all_metas': all_metas,
    }


def retrieve_for_question(question: str, state: dict, top_k: int = 5) -> list[str]:
    """Call the actual production retrieval pipeline (_retrieve from query_kb.py)."""
    import sys as _sys
    _sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))
    from tools.query_kb import _retrieve
    from knowledge_base.core.query_context import inject_context, QueryContext

    # Inject eval context so _retrieve gets proper ACL scopes
    inject_context(QueryContext(user_id="XiongWei", role="owner", platform="wecom"))

    nodes = _retrieve(question, top_k=top_k)
    return [n.content for n in nodes]

# ── Main ──────────────────────────────────────────────────────────────
def _retrieve_all_contexts(state: dict, questions: list[str], num_rows: int):
    """逐题走生产检索管线，返回 (contexts_list, top_sources_list)。

    top_sources 为展示用的 dense-only top-1 元数据（非检索结果本身）。"""
    contexts_list = []
    top_sources_list = []
    coll = state['coll']
    ef = state['ef']
    where = state['where']
    for i, q in enumerate(questions):
        ctx = retrieve_for_question(q, state, top_k=5)
        contexts_list.append(ctx)
        # Get source file for display (dense-only top-1 for metadata lookup)
        emb = ef([q])[0]
        if hasattr(emb, 'tolist'):
            emb = emb.tolist()
        meta_result = coll.query(query_embeddings=[emb], n_results=1, where=where)
        metas = meta_result.get("metadatas", [[]])[0]
        sf = metas[0].get("source_file", "?") if metas else "?"
        top_sources_list.append([sf])
        print(f"  [{i+1:2d}/{num_rows}] {q[:50]}... → {len(ctx)} ctx, top: {sf[:50]}")
    return contexts_list, top_sources_list


def _save_detail(rows, questions, ground_truths, contexts_list, top_sources_list) -> str:
    """逐题明细落盘 data/ragas_detail.json，返回路径。"""
    detail_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "ragas_detail.json",
    )
    detail = []
    for i in range(len(rows)):
        detail.append({
            "index": i + 1,
            "question": questions[i],
            "ground_truth": ground_truths[i],
            "contexts": contexts_list[i],
            "top_sources": top_sources_list[i] if i < len(top_sources_list) else [],
        })
    os.makedirs(os.path.dirname(detail_path), exist_ok=True)
    with open(detail_path, "w", encoding="utf-8") as fh:
        json.dump(detail, fh, ensure_ascii=False, indent=2)
    return detail_path


def _average_scores(all_cp: list, n_runs: int, num_questions: int) -> list:
    """跨 run 逐题平均（降低 LLM-judge 波动）。"""
    return [
        sum(run[i] for run in all_cp) / n_runs
        for i in range(num_questions)
    ]


def _run_ragas_eval(questions, contexts_list, ground_truths, detail_path) -> None:
    """RAGAS context_precision 评测（LLM 判官可用时）。

    失败（缺依赖 / LLM 不可达）打印告警并保留原始检索明细，不向上抛。"""
    try:
        import ragas
        from ragas.metrics import context_precision
        from langchain_openai import ChatOpenAI
        from datasets import Dataset

        eval_llm = ChatOpenAI(
            model=os.environ.get("STEP_BACK_MODEL_NAME", "deepseek-v4-flash"),
            openai_api_key=os.environ.get("STEP_BACK_API_KEY", os.environ.get("DEEPSEEK_API_KEY", "")),
            openai_api_base=os.environ.get("STEP_BACK_MODEL_URL", "https://api.deepseek.com/v1"),
            temperature=0,
        )

        dataset = Dataset.from_dict({
            "question": questions,
            "contexts": contexts_list,
            "ground_truth": ground_truths,
        })

        # ── Run 3 times and average to reduce LLM-judge variance ──
        N_RUNS = 3
        all_cp = []
        print(f"\n  Running evaluation {N_RUNS} times (LLM-judge averaging)...")
        for run_i in range(N_RUNS):
            scores = ragas.evaluate(
                dataset,
                metrics=[context_precision],
                llm=eval_llm,
            )
            cp = scores["context_precision"]
            all_cp.append(cp)
            avg = sum(cp) / len(cp)
            print(f"    Run {run_i+1}: {avg:.4f}")

        # Average across runs
        cp_values = _average_scores(all_cp, N_RUNS, len(questions))
        cp_avg = sum(cp_values) / len(cp_values) if cp_values else 0

        print(f"\n{'='*50}")
        print(f"RAGAS Context Precision (avg of {N_RUNS} runs): {cp_avg:.4f}")
        print(f"  各题分数: {[round(v, 3) for v in cp_values]}")
        print("  (衡量检索到的上下文与问题的相关性，>0.70 为良好)")

        # Compute variance stats
        import numpy as _np
        stds = [_np.std([run[i] for run in all_cp]) for i in range(len(questions))]
        high_var = [(i+1, stds[i]) for i in range(len(questions)) if stds[i] > 0.15]
        if high_var:
            print(f"  高波动题(σ>0.15): {[(q,f'{s:.2f}') for q,s in high_var]}")

        # Persist scores
        scores_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "ragas_scores.json",
        )
        score_data = {
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "context_precision_avg": cp_avg,
            "context_precision_per_question": cp_values,
            "per_run": all_cp,
            "std_per_question": [round(s, 4) for s in stds],
            "num_questions": len(questions),
            "num_full_score": sum(1 for v in cp_values if v >= 0.999),
            "num_zero_score": sum(1 for v in cp_values if v < 0.001),
        }
        with open(scores_path, "w", encoding="utf-8") as fh:
            json.dump(score_data, fh, ensure_ascii=False, indent=2)
        print(f"\n  Scores saved to {scores_path}")
    except Exception as exc:
        import traceback
        print(f"\n⚠️  RAGAS LLM evaluation failed: {exc}")
        traceback.print_exc()
        print(f"   Raw retrieval results saved to {detail_path}")


def main():
    benchmark_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "benchmark.md",
    )
    rows = parse_benchmark(benchmark_path)
    print(f"Loaded {len(rows)} questions from benchmark.md")

    # Build dataset for RAGAS
    questions = [r["question"] for r in rows]
    ground_truths = [r["ground_truth"] for r in rows]

    print("Building retriever (BM25 index + embedding function)...")
    state = _build_retriever()

    print("Retrieving contexts (dense + BM25 hybrid)...")
    contexts_list, top_sources_list = _retrieve_all_contexts(state, questions, len(rows))

    detail_path = _save_detail(rows, questions, ground_truths,
                               contexts_list, top_sources_list)

    _run_ragas_eval(questions, contexts_list, ground_truths, detail_path)

    print(f"\nDone. Details saved to {detail_path}")


if __name__ == "__main__":
    main()
