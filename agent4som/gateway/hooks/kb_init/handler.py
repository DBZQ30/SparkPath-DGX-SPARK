"""kb-init hook: initialize ChromaDB singleton on gateway startup.

File ingestion is LLM-driven via the knowledge_ingest tool — no
automatic background ingestion is performed by this hook.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict



# Add agent4som repo to sys.path so knowledge_base is importable.
# NOTE: __file__ 指向软链路径而非 realpath，parents[3] 不可靠；
#   故改由环境变量 AGENT4SOM_HOME（仓库根绝对路径）显式注入。
_AGENT4SOM_HOME = os.getenv("AGENT4SOM_HOME", "")
if not _AGENT4SOM_HOME or not os.path.isdir(_AGENT4SOM_HOME):
    raise RuntimeError("AGENT4SOM_HOME must be set to an existing directory")
sys.path.insert(0, _AGENT4SOM_HOME)

from knowledge_base.repository.chroma_repository import ChromaRepository
from knowledge_base.repository.embedding_providers import (
    OpenAICompatibleEmbeddingFunction,
    BuiltinEmbeddingFunction,
    FallbackEmbeddingFunction,
)

logger = logging.getLogger(__name__)

_CHROMA_PATH = os.getenv("CHROMA_DB_PATH", "")
_EMBEDDING_URL = os.getenv("QWEN_EMBEDDING_URL", "")
if not _EMBEDDING_URL:
    raise RuntimeError("QWEN_EMBEDDING_URL not set — check your .env file or environment")
_EMBEDDING_KEY = os.getenv("QWEN_API_KEY", "")
_EMBEDDING_MODEL = os.getenv("QWEN_EMBEDDING_MODEL", "qwen3-embedding")

def build_embedding_function():
    primary = OpenAICompatibleEmbeddingFunction(
        api_key=_EMBEDDING_KEY,
        model=_EMBEDDING_MODEL,
        base_url=_EMBEDDING_URL,
    )
    fallback = BuiltinEmbeddingFunction()
    return FallbackEmbeddingFunction(primary=primary, fallback=fallback)


async def handle(event_type: str, _context: Dict[str, Any]) -> None:
    # _context：钩子框架签名约定参数，本钩子不消费（vulture 死代码审计项）
    if event_type != "gateway:startup":
        return

    chroma_path = os.path.expanduser(_CHROMA_PATH)
    os.makedirs(chroma_path, exist_ok=True)
    logger.info("kb-init: initializing ChromaDB at %s", chroma_path)

    embed_fn = build_embedding_function()
    # Use get_chroma_client() to respect CHROMA_HOST/CHROMA_PORT env vars.
    # Falls back to PersistentClient when HTTP mode is not configured.
    from knowledge_base.repository.chroma_repository import get_chroma_client
    client = get_chroma_client(chroma_path)

    from knowledge_base.core.sqlite_store import SqliteStore
    sqlite_path = os.path.join(os.path.dirname(chroma_path), "quota.db")
    store = SqliteStore(sqlite_path)

    ChromaRepository.init_instance(client, embedding_function=embed_fn, sqlite_store=store)
    logger.info("kb-init: ChromaRepository singleton ready")

    # Initialize audit logger (shared by response_verifier + knowledge_ingest)
    from knowledge_base.retrieval.response_verifier import init_audit_logger
    audit_db = os.path.join(os.path.dirname(chroma_path), "audit.db")
    audit = init_audit_logger(audit_db)
    logger.info("kb-init: audit logger initialized at %s", audit_db)

    # Wire role-store audit so set_role/unset_role write to the same audit log
    from knowledge_base.auth.role_store import init_role_audit
    init_role_audit(audit)

    # Preload the BM25 keyword index at startup so the FIRST user query doesn't
    # pay for it. get_bm25_index() lazily builds once per process from ChromaDB
    # (it logs "BM25 index lazy-loaded with N nodes"); calling it here moves that
    # one-time cost off the request path. Do NOT fetch/re-index a second time —
    # that duplicated the full bulk read and reindex on every boot.
    #
    # IMPORTANT: the build is synchronous, CPU/network-bound, and can take tens
    # of seconds on a cold ChromaDB. Run it in a worker thread. Awaiting it
    # directly would block the event loop while the adapters are already
    # listening, which shows up as connection backlogs / 502s during startup.
    try:
        from knowledge_base.retrieval.bm25_search import preload_bm25_index

        indexed = await preload_bm25_index()
        logger.info("kb-init: BM25 index preloaded (%s nodes)", indexed)
    except Exception:
        logger.warning(
            "kb-init: BM25 preload failed — keyword search will lazy-load on first query",
            exc_info=True,
        )

    logger.info("kb-init: knowledge_search tool auto-registered via discovery (tools/query_kb.py)")
