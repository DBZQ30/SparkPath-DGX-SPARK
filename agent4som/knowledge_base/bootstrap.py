"""One-shot KB component factory. Eliminates init boilerplate across scripts.

Usage::

    from knowledge_base.bootstrap import (
        load_dotenv,
        build_embedding_function,
        create_chroma_repository,
        create_ingestion_orchestrator,
    )

    # One-liner for CLI / batch scripts
    orch = create_ingestion_orchestrator()
    result = orch.ingest_file("admin", "/path/to/file.pdf", scope="global")

    # Just the repo (for eval / search-only scripts)
    repo = create_chroma_repository()
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# Module-level flag so load_dotenv() is idempotent
_dotenv_loaded = False


# ── .env loading ────────────────────────────────────────────────────────

def load_dotenv(env_path: str | None = None) -> None:
    """Load ``.env`` from *env_path* (or auto-detect from project root).

    Idempotent — calling multiple times is safe.  Uses ``os.environ.setdefault``
    so existing env vars are never overwritten.

    Auto-detection walks up from this file's directory to find ``.env``.
    """
    global _dotenv_loaded
    if _dotenv_loaded:
        return

    if env_path is None:
        # Walk up from knowledge_base/ to project root
        candidate = Path(__file__).resolve().parent.parent / ".env"
        if candidate.exists():
            env_path = str(candidate)

    if env_path is None or not os.path.isfile(env_path):
        _dotenv_loaded = True
        return

    # Configure logging at INFO level on first .env load so log messages
    # are visible in production (Python defaults to WARNING).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        force=False,  # don't override if already configured
    )

    with open(env_path) as fh:
        for line in fh:
            line = line.strip()
            # Handle "export FOO=bar" syntax from secrets management tools
            if line.startswith("export "):
                line = line[len("export "):]
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("\"'")
            os.environ.setdefault(key, val)

    _dotenv_loaded = True
    logger.debug(".env loaded from %s", env_path)


# ── embedding function ──────────────────────────────────────────────────

def build_embedding_function():
    """Construct an ``OpenAICompatibleEmbeddingFunction`` from env vars.

    Reads ``QWEN_EMBEDDING_URL``, ``QWEN_API_KEY``, ``QWEN_EMBEDDING_MODEL``.
    Does NOT include the Gateway-only ``FallbackEmbeddingFunction`` wrapper —
    callers that need fallback behaviour should wrap the result themselves.
    """
    from knowledge_base.repository.embedding_providers import OpenAICompatibleEmbeddingFunction

    api_key = os.environ.get("QWEN_API_KEY", "")
    model = os.environ.get("QWEN_EMBEDDING_MODEL", "qwen3-embedding")
    base_url = os.environ.get("QWEN_EMBEDDING_URL", "")

    if not api_key:
        raise RuntimeError("QWEN_API_KEY not set — check your .env file or environment")
    if not base_url:
        raise RuntimeError("QWEN_EMBEDDING_URL not set — check your .env file or environment")

    return OpenAICompatibleEmbeddingFunction(
        api_key=api_key,
        model=model,
        base_url=base_url,
    )


# ── repository ──────────────────────────────────────────────────────────

def create_chroma_repository(chroma_path: str | None = None):
    """Create a fully wired ``ChromaRepository`` with ``SqliteStore``.

    Args:
        chroma_path: Path to ChromaDB data directory.  Defaults to
            ``CHROMA_DB_PATH`` env var or ``"data/chroma"``.

    Returns a ``ChromaRepository`` ready for use (NOT the singleton —
    callers that need ``ChromaRepository.instance()`` should use
    ``init_chroma_repository_singleton()`` instead).
    """
    from knowledge_base.repository.chroma_repository import ChromaRepository, get_chroma_client
    from knowledge_base.core.sqlite_store import SqliteStore

    load_dotenv()

    if chroma_path is None:
        chroma_path = os.environ.get("CHROMA_DB_PATH", "data/chroma")

    embed_fn = build_embedding_function()
    client = get_chroma_client(chroma_path)

    sqlite_path = os.path.join(os.path.dirname(chroma_path), "quota.db")
    store = SqliteStore(sqlite_path)

    return ChromaRepository(client, embedding_function=embed_fn, sqlite_store=store)


_singleton_lock = threading.Lock()


def init_chroma_repository_singleton(chroma_path: str | None = None) -> None:
    """Initialise the global ``ChromaRepository`` singleton.

    Must be called once before any code that uses
    ``ChromaRepository.instance()`` (e.g. the ``query_kb.py`` Hermes tool).
    Thread-safe — second call is a no-op if already initialised.
    """
    from knowledge_base.repository.chroma_repository import ChromaRepository

    if ChromaRepository.is_ready():
        return

    with _singleton_lock:
        if ChromaRepository.is_ready():  # double-check under lock
            return

        repo = create_chroma_repository(chroma_path=chroma_path)
        # The factory already created the instance — we need to set the singleton.
        ChromaRepository.init_instance(
            repo._client,
            embedding_function=repo._embedding_function,
            sqlite_store=repo._sqlite_store,
        )


# ── orchestrator ────────────────────────────────────────────────────────

def create_ingestion_orchestrator(repo=None):
    """Create a fully wired ``IngestionOrchestrator``.

    Args:
        repo: An existing ``ChromaRepository``.  If ``None``, one is created
            automatically via :func:`create_chroma_repository`.

    Returns an ``IngestionOrchestrator`` with ``QuotaManager``,
    ``VersionManager``, and ``ExtractorRouter`` already injected.
    """
    from knowledge_base.ingestion.orchestrator import IngestionOrchestrator
    from knowledge_base.core.quota_manager import QuotaManager
    from knowledge_base.core.version_manager import VersionManager
    from knowledge_base.pipeline.router import ExtractorRouter

    if repo is None:
        repo = create_chroma_repository()

    return IngestionOrchestrator(
        repo,
        QuotaManager(repo),
        VersionManager(repo),
        ExtractorRouter(),
    )
