"""
knowledge_ingest — 文件入库工具

Self-registers with Hermes tool registry on import.
LLM can call this when a user wants to ingest a file into the knowledge base.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

KNOWLEDGE_INGEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "filename": {
            "type": "string",
            "description": "要入库的文件。可以是消息中显示的文件名（如 西安交通大学课程调整申请表.doc）或 [The user sent a document: ...] 后给出的完整缓存路径（如 /home/.../cache/documents/.../doc_xxx_文件名.doc）。工具会自动从完整路径中提取文件名。"
        },
        "scope": {
            "anyOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}}
            ],
            "description": "入库范围。支持单个(字符串)或多个(数组)。可选值：global(公共知识库)/teachers(教师知识库)/users/{user_id}(个人知识库)。不填则根据角色自动选择。",
            "default": ""
        },
    },
    "required": ["filename"]
}


def _normalize_for_match(name: str) -> str:
    """Normalize filename for fuzzy matching.

    WeCom mangles Chinese punctuation in text messages.  Instead of
    exhaustively listing every punctuation variant that might differ
    between the original filename and the WeCom text representation,
    extract only the meaningful core: Chinese characters, ASCII
    letters and digits, and the file extension.
    """
    import re
    name = re.sub(r"^doc_[a-f0-9]{12}_", "", name)
    # Keep only: Chinese chars (U+4E00–U+9FFF), ASCII letters, digits, dot
    core = re.sub(r"[^一-鿿㐀-䶿a-zA-Z0-9.]", "", name)
    return core.lower()


def _find_file_in_cache(filename: str, user_id: str = "") -> Optional[str]:
    """Search for *filename* in the user's own WeCom cache directory.

    Fail-closed: when *user_id* is empty or ``"unknown"`` the call returns
    ``None`` immediately.  There is no fallback that scans other users'
    directories — the platform adapter MUST inject the correct sender
    identity via ``inject_context()`` for this function to work.
    """
    if not user_id or user_id == "unknown":
        logger.warning("knowledge_ingest: empty user_id, refusing to search cache")
        return None

    # Mirror the sanitization from cache_document_from_bytes (base.py:1260-1262).
    # The guard above already ensures user_id is non-empty, so no fallback needed.
    safe_user = Path(user_id).name
    safe_user = safe_user.replace("\x00", "").strip().replace("/", "_").replace("\\", "_")
    if safe_user in {".", "..", ""}:
        return None

    # Directories to search, in priority order.
    # Images are stored per-user since 2026-07-10 (L0 isolation, mirroring
    # cache_document_from_bytes).
    _cache_roots = [
        Path.home() / ".hermes" / "cache" / "documents" / safe_user,
        Path.home() / ".hermes" / "image_cache" / safe_user,
        Path.home() / ".hermes" / "cache" / "images" / safe_user,
    ]

    query_core = _normalize_for_match(filename)
    for cache_dir in _cache_roots:
        if not cache_dir.is_dir():
            continue
        for fpath in cache_dir.rglob("*"):
            if not fpath.is_file():
                continue
            cache_core = _normalize_for_match(fpath.name)
            if query_core in cache_core or cache_core in query_core:
                return str(fpath)
            if fpath.name.endswith(filename) or filename in fpath.name:
                return str(fpath)
    return None


def _derive_scope(role: str, user_id: str, requested_scope: str) -> str:
    """Derive the correct scope based on role and request.

    Delegates to shared ACL logic for default scope; user-requested scope
    is validated later by check_write_permission().
    """
    if requested_scope and requested_scope.strip():
        return requested_scope.strip()
    from knowledge_base.retrieval.acl_filter import derive_default_scope
    return derive_default_scope(role, user_id)


def _normalize_filename(fname: str) -> str:
    """Strip WeCom cache prefix (``doc_{uuid}_``) for dedup with original file."""
    import re
    return re.sub(r"^doc_[a-f0-9]{12}_", "", fname)


def _normalize_scopes(raw_scope):
    """Normalize the scope argument to a list of scope strings.

    Supports string (single), array (multiple), or empty (auto-derive
    from role inside the handler loop).
    """
    if isinstance(raw_scope, list):
        scopes = [s.strip() for s in raw_scope if isinstance(s, str) and s.strip()]
        return scopes if scopes else [""]
    if isinstance(raw_scope, str) and raw_scope.strip():
        return [raw_scope.strip()]
    return [""]  # empty → _derive_scope auto-derives


def _ingest_one(orch, user_id, file_path, filename, scope, role, count_quota: bool = True):
    """Ingest a file into a single scope.  Returns a result string."""
    from knowledge_base.retrieval.acl_filter import check_write_permission
    from knowledge_base.ingestion.orchestrator import IngestionStatus

    scope = _derive_scope(role, user_id, scope)
    try:
        check_write_permission(role, scope, user_id)
    except Exception as e:
        return f"[{scope}] 入库失败：权限不足。{e}"

    result = orch.ingest_file(user_id, file_path, scope=scope, source=f"wecom:{user_id}",
                              count_quota=count_quota)

    # Unified audit (ingested / replaced / skipped / failed).
    try:
        from knowledge_base.retrieval.response_verifier import _audit
        if _audit:
            _audit.log_event(
                event_type=f"ingest_{result.status.value}",
                user_id=user_id,
                role=role,
                filename=_normalize_filename(os.path.basename(file_path)),
                scope=scope,
                node_count=result.node_count,
                file_size_bytes=result.file_size_bytes,
                detail={
                    "notification": result.notification,
                    "error_detail": result.error_detail,
                },
            )
    except Exception:
        logger.error("AuditLogger: failed write for user=%s file=%s", user_id, filename, exc_info=True)

    if result.status == IngestionStatus.SKIPPED:
        return (
            f"[INGEST_RESULT: SKIPPED] "
            f"文件《{filename}》已存在于{scope}，无需重复入库。"
            f"该文件已有 {result.node_count} 个检索节点可用。"
        )
    if result.status in (IngestionStatus.INGESTED, IngestionStatus.REPLACED):
        action = "入库" if result.status == IngestionStatus.INGESTED else "更新"
        return (
            f"[INGEST_RESULT: SUCCESS] "
            f"文件《{filename}》已成功{action}到 {scope}。"
            f"生成 {result.node_count} 个检索节点。"
        )
    return (
        f"[INGEST_RESULT: FAILED] "
        f"入库到 {scope} 失败（{result.status.value}）：{result.error_detail or result.notification}"
    )


def _auto_detect_file(user_id: str) -> str | None:
    """Return basename of most recently modified file in user's cache dir.

    Fallback for when the LLM fails to pass a filename — the user just sent
    a file via WeCom, so the latest file in their cache is almost certainly
    the right one.
    """
    if not user_id or user_id == "unknown":
        return None
    safe_user = Path(user_id).name
    safe_user = safe_user.replace("\x00", "").strip().replace("/", "_").replace("\\", "_")
    if safe_user in {".", "..", ""}:
        return None
    user_doc_dir = Path.home() / ".hermes" / "cache" / "documents" / safe_user
    image_dirs = [
        Path.home() / ".hermes" / "image_cache" / safe_user,
        Path.home() / ".hermes" / "cache" / "images" / safe_user,
    ]

    files = []
    for d in [user_doc_dir, *image_dirs]:
        if d.is_dir():
            for f in d.rglob("*"):
                if f.is_file():
                    files.append(f)
    if not files:
        return None
    latest = max(files, key=lambda f: f.stat().st_mtime)
    return latest.name


# Guard: per-user counter for repeated empty-filename calls.
# Reset on any successful call (filename provided or auto-filled).
_EMPTY_FILENAME_COUNTS: dict[str, int] = {}


def _guard_empty_filename(filename: str, user_id: str, platform: str) -> Optional[str]:
    """Circuit breaker for repeated empty-filename calls.

    Phase 1 (counter 1-2): give LLM a chance to self-correct.
    Phase 2 (counter ≥3): skip guard, return None → fall through to auto-fill.
    A successful call (filename provided) resets the counter.
    """
    key = f"{user_id}:{platform}"
    if not filename:
        count = _EMPTY_FILENAME_COUNTS.get(key, 0) + 1
        _EMPTY_FILENAME_COUNTS[key] = count

        if count == 1:
            return "请提供要入库的文件名。"
        if count == 2:
            return (
                "[TOOL_GUARD] 你已连续 2 次调用 knowledge_ingest 但未提供 filename 参数。\n"
                "这不是 tool schema 问题——工具完全正常，只是需要你传入 filename。\n"
                "请从对话消息中的 [FILE_CONTEXT] 标记或文件标记里提取文件名，重新调用。"
            )
        return None  # count >= 3: guard gives up, fall through to auto-fill

    _EMPTY_FILENAME_COUNTS.pop(key, None)
    return None


def _wait_for_cached_file(filename: str, user_id: str) -> tuple[Optional[str], Optional[str]]:
    """Find *filename* in the user's cache, retrying while COS decrypts.

    Returns ``(file_path, None)`` on success, ``(None, error_message)`` on the
    ownership security check failure, ``(None, None)`` when not found after
    all retries (caller renders the not-found message).
    """
    import time as _time
    for _attempt in range(4):
        file_path = _find_file_in_cache(filename, user_id)
        if file_path:
            # Defense-in-depth: verify the file is in the requesting user's directory.
            _parts = file_path.replace("/documents/", "\0").split("\0")
            if len(_parts) >= 2:
                path_user_id = _parts[1].split("/")[0]
                safe_user = (
                    Path(user_id).name
                    .replace("\x00", "").strip()
                    .replace("/", "_").replace("\\", "_")
                )
                if path_user_id != safe_user:
                    logger.error(
                        "knowledge_ingest: user_id mismatch — ctx=%s (sanitized=%s) path=%s file=%s",
                        user_id, safe_user, path_user_id, file_path,
                    )
                    return None, "安全校验失败：文件不属于当前用户。请重新发送文件。"
            return file_path, None
        if _attempt < 3:
            _time.sleep(2.0)  # COS download + AES decrypt takes ~4s
    return None, None


def _check_quota_once(orch, user_id: str, file_path: str, role: str) -> Optional[str]:
    """Pre-check quota once for the whole call (multi-scope = one upload).

    Owner/admin bypass. Returns the failure message, or None on pass.
    The per-scope ingests later run with count_quota=False.
    """
    from knowledge_base.auth.role_store import ROLE_OWNER, ROLE_ADMIN
    import os as _os
    if role in (ROLE_OWNER, ROLE_ADMIN):
        return None
    try:
        orch._quota.check_quota(user_id, _os.path.getsize(file_path),
                                is_admin_or_owner=False)
    except Exception as exc:
        return f"[INGEST_RESULT: FAILED] 配额检查未通过：{exc}"
    return None


def handle_knowledge_ingest(args: dict, **kw) -> str:
    """Ingest a file into the knowledge base.

    Supports multi-scope ingestion: pass ``scope`` as a list of scope
    strings to ingest the same file into multiple scopes in one call.
    """
    from knowledge_base.core.query_context import current_context

    filename = args.get("filename", "").strip()
    raw_scope = args.get("scope", "")

    # Resolve user_id early — needed by auto-fill fallback below.
    ctx = current_context()
    user_id = ctx.user_id or "unknown"

    # ── Guard: circuit breaker for repeated empty-filename calls ──
    guard_msg = _guard_empty_filename(filename, user_id, ctx.platform)
    if guard_msg:
        return guard_msg

    # ── Auto-fill fallback (guard phase 2 or LLM never provided filename) ──
    if not filename:
        filename = _auto_detect_file(user_id) or ""
        if not filename:
            return "请提供要入库的文件名。"
        logger.info("knowledge_ingest: auto-filled filename=%s for user=%s", filename, user_id)

    # If LLM passed a full path (e.g. the agent_path from the context note),
    # extract the basename so _find_file_in_cache can fuzzy-match it.
    # LLMs like deepseek-v4-flash consistently fail to extract just the
    # filename from natural-language messages, but can copy-paste a path.
    if "/" in filename:
        filename = os.path.basename(filename)

    scopes = _normalize_scopes(raw_scope)

    # Monitoring: if ContextVar is empty something is wrong upstream
    if not ctx.user_id or ctx.user_id == "unknown":
        logger.error(
            "knowledge_ingest: empty user_id in QueryContext — inject_context may have failed"
        )

    # 1. Find the file in cache (once, not per scope)
    file_path, path_error = _wait_for_cached_file(filename, user_id)
    if path_error:
        return path_error
    if not file_path:
        return (
            f"未在缓存中找到文件 '{filename}'。"
            f"请重新发送文件后再试。"
        )

    # Resolve role once — QueryContext may not have been injected
    from knowledge_base.auth.role_store import resolve_role as _resolve_role
    role = _resolve_role(ctx.platform or "wecom", user_id)

    # 2. Ingest to each scope (single orch instance, per-scope permission check)
    from knowledge_base.bootstrap import create_ingestion_orchestrator
    orch = create_ingestion_orchestrator()

    quota_error = _check_quota_once(orch, user_id, file_path, role)
    if quota_error:
        return quota_error

    results = [_ingest_one(orch, user_id, file_path, filename, s, role,
                           count_quota=False) for s in scopes]
    return "\n".join(results)


# ── Self-register with Hermes ─────────────────────────────────────────

def _check_knowledge_ingest() -> bool:
    from knowledge_base.repository.chroma_repository import ChromaRepository
    return ChromaRepository.is_ready()


from tools.registry import registry

registry.register(
    name="knowledge_ingest",
    toolset="rag",
    schema=KNOWLEDGE_INGEST_SCHEMA,
    handler=handle_knowledge_ingest,
    check_fn=_check_knowledge_ingest,
    emoji="📥",
    description="⚠️ 文件入库专用，禁止用于检索！当用户明确要求将文件存入知识库时使用。参数：filename（文件名），可选 scope。检索问题请用 knowledge_search。",
)
