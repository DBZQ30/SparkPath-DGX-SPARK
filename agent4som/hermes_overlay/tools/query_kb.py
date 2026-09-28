"""
knowledge_search — RAG知识库检索工具

Self-registers with Hermes tool registry on import.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from knowledge_base.models.schemas import RawIndexNode



logger = logging.getLogger(__name__)

# 模型服务默认地址一律由 GPU_HOST 推导，避免把某一台机器的 IP 写死在代码里。
# GPU_HOST 在 acc-helper-vm(.env) = <GPU_HOST_IP>（独立 GPU 主机）；
# 在 dgx(.env) = 127.0.0.1（模型与后端同机）。未设置时回落到本机。
_GPU_HOST = os.getenv("GPU_HOST", "127.0.0.1")

_EMBEDDING_BASE_URL = os.getenv("QWEN_EMBEDDING_URL") or f"http://{_GPU_HOST}:8001/v1"
_EMBEDDING_API_KEY = os.getenv("QWEN_API_KEY", "")
_EMBEDDING_MODEL = os.getenv("QWEN_EMBEDDING_MODEL", "qwen3-embedding")

_RERANK_BASE_URL = os.getenv("BGE_RERANKER_URL") or f"http://{_GPU_HOST}:8002/v1/rerank"
_RERANK_API_KEY = os.getenv("QWEN_API_KEY", "")

# Step-Back query rewriting config
_STEP_BACK_URL = os.getenv("STEP_BACK_MODEL_URL") or f"http://{_GPU_HOST}:8000/v1"
_STEP_BACK_MODEL = os.getenv("STEP_BACK_MODEL_NAME", "")
_STEP_BACK_ENABLED = os.getenv("STEP_BACK_ENABLED", "true").lower() == "true"
_STEP_BACK_API_KEY = os.getenv("STEP_BACK_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))

_STEP_BACK_SYSTEM_PROMPT = (
    "你是一个查询改写助手。给定一个用户关于大学教务管理的问题，"
    "请生成一个更通用、更抽象的回退查询（step-back query），"
    "用于检索相关的政策文件全文。\n\n"
    "规则：\n"
    "1. 回退查询应该比原始问题更宽泛，不要过于具体\n"
    "2. 保留原始问题中的关键实体（如课程名、文件名、文号）\n"
    "3. 用陈述句或短语形式输出，不要用问句\n"
    "4. 只输出回退查询本身，不要加任何解释、前缀或后缀\n\n"
    "示例：\n"
    "原始问题：申请转专业需要满足哪些条件\n"
    "回退查询：西安交通大学本科生转专业实施细则 申请条件\n\n"
    "原始问题：课程考核有哪些规定\n"
    "回退查询：西安交通大学本科课程考核管理办法 考试规则 监考规范\n\n"
    "原始问题：免试攻读研究生需要什么条件\n"
    "回退查询：西安交通大学推荐优秀本科毕业生免试攻读研究生工作管理办法\n"
)

def _step_back_rewrite(query: str) -> str | None:
    """Generate a broader step-back query for improved recall.

    Returns None if rewriting is disabled, the model is unavailable,
    or the rewrite fails — callers should fall back to the original query.
    """
    if not _STEP_BACK_ENABLED or not _STEP_BACK_MODEL:
        return None

    payload = {
        "model": _STEP_BACK_MODEL,
        "messages": [
            {"role": "system", "content": _STEP_BACK_SYSTEM_PROMPT},
            {"role": "user", "content": f"原始问题：{query}"},
        ],
        "max_tokens": 128,
        "temperature": 0.1,
        # 关掉思考：本调用只做查询改写，不需要推理。step-back 默认指向带
        # reasoning-parser 的 qwen3 系模型（STEP_BACK_MODEL_NAME）；不关时 128 个
        # token 会被 reasoning 全部吃掉 → finish_reason=length、content=None，
        # 既改写失败（旧代码在此 None.strip() 崩溃）又白等 15–22s。
        # vLLM/Qwen3 用 chat_template_kwargs.enable_thinking 关闭；非 vLLM 端点
        # 若因此返回 400/422，下面会去掉该参数重试一次。
        "chat_template_kwargs": {"enable_thinking": False},
    }

    try:
        import requests as _requests

        def _post(body: dict):
            return _requests.post(
                f"{_STEP_BACK_URL}/chat/completions",
                headers={"Authorization": f"Bearer {_STEP_BACK_API_KEY}"},
                json=body,
                timeout=15,
            )

        resp = _post(payload)
        if resp.status_code in (400, 422):
            # 端点不认识 chat_template_kwargs：退回不带该参数的请求
            payload.pop("chat_template_kwargs", None)
            resp = _post(payload)
        resp.raise_for_status()
        # content 可能为 None（如 token 被 reasoning 吃光）——必须容错，
        # 历史上这里直接 .strip() 导致 27 次改写全部失败（26 次 AttributeError）。
        rewritten = (resp.json()["choices"][0]["message"].get("content") or "").strip()
        # Sanity check: don't return if too short or identical
        if len(rewritten) < 3 or rewritten == query:
            return None
        logger.info("Step-back rewrite: \"%s\" → \"%s\"", query[:60], rewritten[:100])
        return rewritten
    except Exception as exc:
        logger.warning("Step-back rewrite failed for \"%s\": %s", query[:60], exc)
        return None

KNOWLEDGE_SEARCH_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": (
                "搜索查询，从用户消息中提取关键实体和意图。"
                "必须是非空字符串。"
                "示例：'转专业申请条件'、'期末考试安排通知'、'西交教〔2025〕27号'"
            ),
        },
        "top_k": {
            "type": "integer",
            "description": "返回的文档片段数量",
            "default": 5
        }
    },
    "required": ["query"]
}


def _validate_knowledge_search_args(args: dict) -> str | None:
    """Validate knowledge_search arguments before calling the handler.

    Returns an error message string if validation fails, or None if OK.
    This is a defense-in-depth check — the JSON Schema minLength also
    declares the constraint, but not all LLM gateways enforce it.
    """
    query = (args.get("query", "") or "").strip()
    if not query:
        return (
            "参数错误：query 不能为空。"
            "请从用户消息中提取搜索关键词填入 query 参数后重新调用。"
            "例如：query='转专业申请条件'"
        )
    if len(query) < 2:
        return (
            "参数错误：query 过短（至少2个字符）。"
            "请提供更具体的搜索词，例如：query='本科生转专业实施办法'"
        )
    return None


def _get_embedding(query: str) -> List[float]:
    from knowledge_base.repository.embedding_providers import OpenAICompatibleEmbeddingFunction
    fn = OpenAICompatibleEmbeddingFunction(
        api_key=_EMBEDDING_API_KEY,
        model=_EMBEDDING_MODEL,
        base_url=_EMBEDDING_BASE_URL,
    )
    result = fn([query])
    if not result:
        return []
    emb = result[0]
    # ChromaDB may wrap in numpy array; convert to plain list
    if hasattr(emb, 'tolist'):
        return emb.tolist()
    return list(emb) if emb else []


def _build_reranker():
    from knowledge_base.retrieval.qwen3_reranker import Qwen3Reranker
    return Qwen3Reranker(
        base_url=_RERANK_BASE_URL,
        api_key=_RERANK_API_KEY,
        top_k=3,
    )



# ── Retrieved-query pipeline stages (used by _retrieve) ─────────────

# Query Expansion: domain-specific synonym mapping
_QE_MAP = {
    "分流": "专业选择 志愿填报",
    "大类招生": "专业选择 专业分流",
    "调课": "课程调整 调停课",
    "停课": "调停课 课程调整",
    "审批流程": "申请表 签字 审批",
    "导师制": "本科生导师 导师管理办法",
    "补考": "补考 重修 结业换毕业",
    "选修课": "通识选修课 通识课",
    "预科班": "少数民族预科班 内地班",
    "保研": "推免 推免加分 B类 A类 竞赛加分",
    "竞赛": "推免加分 B类 A类",
    "大赛": "竞赛 B类 A类 推免加分",
    "国际学生": "汉语 中国概况 必修课 思政替代 军事课程 国防教育",
}


def _expand_query(query: str) -> str:
    """QE 同义扩展：命中词尾追加映射词（不改动原查询文本）。"""
    expanded = query
    for term, expansion in _QE_MAP.items():
        if term in query and expansion not in expanded:
            expanded = expanded + " " + expansion
    return expanded


def _resolve_scopes(repo, user_id: str, role) -> list:
    """ACL scope 白名单（admin/owner 跨全体 user scope 审计口径）。"""
    from knowledge_base.retrieval.acl_filter import ACLFilter

    acl = ACLFilter(current_user_id=user_id, current_role=role)
    all_user_scopes = repo.get_all_user_scopes() if acl.can_access_users_scope() else None
    return acl.get_allowed_scopes(all_user_scopes=all_user_scopes)


def _search_with_stepback(repo, allowed_scopes, search_query: str, top_k: int):
    """Dense 检索 + Step-Back 双查询融合（节点去重合并）。

    embedding 失败返回 None（调用方转『无法向量化查询』文案）；
    repo 异常向上抛（调用方统一兜底『检索出错』）。"""
    step_back_query = _step_back_rewrite(search_query)
    query_embedding = _get_embedding(search_query)
    if query_embedding is None or len(query_embedding) == 0:
        return None
    nodes = repo.search_nodes(
        scopes=allowed_scopes, query_embedding=query_embedding,
        top_k=max(top_k, 20), include_distances=True)
    if step_back_query:
        try:
            sb_embedding = _get_embedding(step_back_query)
            if sb_embedding and len(sb_embedding) > 0:
                sb_nodes = repo.search_nodes(
                    scopes=allowed_scopes, query_embedding=sb_embedding,
                    top_k=max(top_k, 20), include_distances=True,
                )
                seen = {n.node_id for n in nodes}
                for n in sb_nodes:
                    if n.node_id not in seen:
                        nodes.append(n)
                        seen.add(n.node_id)
        except Exception as exc:
            logger.warning("Step-back search failed, using original results: %s", exc)
    return nodes


def _empty_result_message(allowed_scopes) -> str:
    """无命中时的提示（含已检索 scope 的中文标签）。"""
    scope_labels = {
        "global": "公共知识库", "teachers": "教师知识库", "users": "个人知识库",
    }
    scope_desc = "、".join(
        scope_labels.get(s.split("/")[0], s) for s in allowed_scopes
    )
    return f"暂未收录该内容（已检索 {scope_desc}），请联系教务办公室。"


def _bm25_hybrid(nodes, query: str):
    """BM25 关键词混排（索引未建 → 原样返回；异常由调用方告警回退）。"""
    from knowledge_base.retrieval.bm25_search import get_bm25_index, hybrid_search

    bm25_idx = get_bm25_index()
    if bm25_idx._total_docs > 0:
        return hybrid_search(bm25_idx, nodes, query,
                             top_k=min(15, len(nodes)), bm25_weight=0.3)
    return nodes


def _rerank_pool(query: str, nodes, top_k: int, allowed_scopes) -> tuple:
    """Rerank 全池（至多 8 篇文档）→ 截 top_k → 末端 ACL 复筛。

    Rerank 在多样性截断之前，让 cross-encoder 看到全部 chunk、逐文档挑最优
    （修 Q19 类问题：导语/样板 chunk 排在实质政策内容之前）。
    ACL 复筛放在 Reranker 之后（而非之前），BM25/重排无法静默引入跨 scope 节点。
    返回 (精选节点, 同步过滤后的检索池)。检索池供 context stitching 扩展邻居。"""
    search_pool = list(nodes)
    try:
        reranker = _build_reranker()
        pool_size = min(8, len(nodes))
        nodes = reranker.rerank(query, nodes, top_k=pool_size)
    except Exception as exc:
        logger.warning("Reranker failed, using unranked results: %s", exc)
    nodes = nodes[:min(top_k, len(nodes))]
    nodes = [n for n in nodes if n.scope in allowed_scopes]
    search_pool = [n for n in search_pool if n.scope in allowed_scopes]
    return nodes, search_pool


def _build_passages_safe(nodes, search_pool):
    """Context stitching：±1 邻居扩展 + 同文档重叠窗口合并（chunk 级检索、
    passage 级返回；失败保留原节点）。"""
    try:
        from knowledge_base.retrieval.context_stitcher import build_passages
        return build_passages(nodes, search_pool, window=1)
    except Exception as exc:
        logger.warning("Passage building failed: %s", exc)
        return nodes


def _retrieve(query: str, top_k: int = 20) -> List["RawIndexNode"]:
    """Production retrieval pipeline: QE → Step-Back → Dense → BM25 → Reranker → stitch.

    Returns raw nodes.  Called by both handle_knowledge_search (WeCom) and
    eval_ragas.py (offline evaluation), ensuring both use the exact same code.
    """
    from knowledge_base.core.query_context import current_context
    from knowledge_base.repository.chroma_repository import ChromaRepository
    from knowledge_base.auth.role_store import resolve_role as _resolve_role

    # ── ① ACL: resolve role and scope whitelist ──
    ctx = current_context()
    user_id = ctx.user_id
    role = _resolve_role(ctx.platform or "wecom", user_id)

    if not ChromaRepository.is_ready():
        return "知识库暂时不可用，请稍后再试。如持续异常请联系 IT 支持。"

    try:
        repo = ChromaRepository.instance()
    except RuntimeError:
        return "知识库暂时不可用，请稍后再试。如持续异常请联系 IT 支持。"

    allowed_scopes = _resolve_scopes(repo, user_id, role)

    # ── ②③ QE → Step-Back → Dense（双查询融合）──
    search_query = _expand_query(query)
    try:
        nodes = _search_with_stepback(repo, allowed_scopes, search_query, top_k)
        if nodes is None:
            return "知识库暂时不可用（无法向量化查询），请稍后再试。"
    except Exception as exc:
        logger.error("knowledge_search failed: %s", exc)
        return "知识库检索出错，请稍后再试。"

    if not nodes:
        return _empty_result_message(allowed_scopes)

    # ── ④ BM25 keyword hybrid search ──
    try:
        nodes = _bm25_hybrid(nodes, query)
    except Exception as exc:
        logger.warning("BM25 hybrid search failed, using vector-only results: %s", exc)

    # ── ⑤ Rerank → trim → 端 ACL 复筛 ── ⑥ passage stitching ──
    nodes, search_pool = _rerank_pool(query, nodes, top_k, allowed_scopes)
    return _build_passages_safe(nodes, search_pool)



def _log_search_audit(query: str, nodes, top_k: int, latency_ms: int) -> None:
    """读取型审计（含 count=0 的失败检索）。静默失败——审计不阻塞主链路。"""
    try:
        from knowledge_base.core.query_context import current_context
        from knowledge_base.auth.role_store import resolve_role as _resolve_role
        from knowledge_base.retrieval.acl_filter import ACLFilter
        from knowledge_base.retrieval.response_verifier import _audit

        if _audit:
            ctx = current_context()
            uid = ctx.user_id or "unknown"
            role = _resolve_role(ctx.platform or "wecom", uid)
            acl = ACLFilter(current_user_id=uid, current_role=role)
            # Include all user scopes for admin/owner so the audit log
            # reflects the true search range (cross-user reads included).
            _all_user_scopes = None
            if acl.can_access_users_scope():
                try:
                    from knowledge_base.repository.chroma_repository import ChromaRepository
                    _all_user_scopes = ChromaRepository.instance().get_all_user_scopes()
                except Exception:
                    _all_user_scopes = None
            allowed = acl.get_allowed_scopes(all_user_scopes=_all_user_scopes)
            _audit.log_event(
                event_type="search",
                user_id=uid,
                role=role,
                query=query,
                scopes_allowed=allowed,
                scopes_hit=list({getattr(n, 'source_path', '') for n in nodes if hasattr(n, 'source_path')}) if nodes else [],
                top_k=top_k,
                result_count=len(nodes),
                result_sources=list({getattr(n, 'source_file', '') for n in nodes if hasattr(n, 'source_file')})[:10] if nodes else [],
                latency_ms=latency_ms,
            )
    except Exception:
        pass


def _format_references(nodes) -> str:
    """节点 → 带出处编号的参考片段文本（文件名/章节/页码或 Sheet 行/来源 URL）。"""
    parts = []
    for i, node in enumerate(nodes, 1):
        ref = f"[{node.source_file}]"
        if node.section_title:
            ref += f" - {node.section_title}"
        if node.page_start is not None:
            ref += f" - 第{node.page_start}页"
        elif node.sheet_name:
            ref += f" - Sheet={node.sheet_name}"
            if node.row_start:
                ref += f" 行{node.row_start}-{node.row_end or node.row_start}"
        # Append source URL for web notices (embedded in content as "来源: https://...")
        if node.content:
            import re as _re
            url_m = _re.search(r'来源:\s*(https?://\S+)', node.content)
            if url_m:
                ref += f" - {url_m.group(1)}"
        parts.append(f"参考{i}: {ref}\n{node.content}")

    return "\n\n---\n\n".join(parts)


def handle_knowledge_search(args: dict, **kw) -> str:
    """Search the SOM knowledge base and return formatted document chunks."""
    import time as _time
    _start = _time.monotonic()

    # ── Validate args before anything else (defense-in-depth) ──
    validation_error = _validate_knowledge_search_args(args)
    if validation_error:
        logger.warning("knowledge_search validation failed, args=%s", args)
        return validation_error

    query = (args.get("query", "") or "").strip()
    top_k = args.get("top_k", 5)

    nodes = _retrieve(query, top_k=top_k)
    # _retrieve returns str for errors/empty results — relay directly to LLM
    if isinstance(nodes, str):
        return nodes
    _latency = int((_time.monotonic() - _start) * 1000)

    _log_search_audit(query, nodes, top_k, _latency)

    if not nodes:
        return "暂未收录该内容，请联系教务办公室。"

    return _format_references(nodes)


# ── Self-register with Hermes ─────────────────────────────────────────

def _check_knowledge_search() -> bool:
    from knowledge_base.repository.chroma_repository import ChromaRepository
    try:
        # instance() auto-initialises if kb_init hook didn't fire
        return ChromaRepository.instance() is not None
    except RuntimeError:
        return False

# registry.register() must be at module top-level so Hermes discovery finds it
from tools.registry import registry

registry.register(
    name="knowledge_search",
    toolset="rag",
    schema=KNOWLEDGE_SEARCH_SCHEMA,
    handler=handle_knowledge_search,
    check_fn=_check_knowledge_search,
    emoji="📚",
    description="""搜索西安交通大学教务知识库，收录全校各学院（含管理学院、经济与金融学院等）的政策文件、培养方案、转专业、推免、课程、学分、考试安排等正式通知和规定。
                **必须调用此工具的场景**：用户询问任何学院/任何主题的教务相关问题——即使学院名称不在管理学院范围内，知识库也可能已收录。
                **不要调用此工具的场景**：闲聊、问候、或明确要求将文件入库（此时用 knowledge_ingest）。
                输入：用户的问题或搜索关键词。
                返回：带出处（文件名）的文档片段。""",
)
