import logging
import sys

# NOTE: This pysqlite3 monkey-patch runs at module import time so it takes
# effect before any other module imports sqlite3.  It is intentionally placed
# in this early-loaded module (embedding_providers is imported by kb_init hook
# at Gateway startup, before ChromaDB or SqliteStore).  If the patch is ever
# moved, ensure it still runs before any sqlite3 consumer imports.
try:
    import pysqlite3
    sys.modules["sqlite3"] = pysqlite3
except ImportError:
    pass

from chromadb import EmbeddingFunction, Documents, Embeddings
import contextlib
try:
    from chromadb.api.types import DefaultEmbeddingFunction
except ImportError:
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

logger = logging.getLogger(__name__)


class OpenAICompatibleEmbeddingFunction(EmbeddingFunction):
    """Calls an OpenAI-compatible embedding API (Zhipu, DeepSeek, etc.).

    Falls back to built-in on network/HTTP errors when wrapped in FallbackEmbeddingFunction.
    """

    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        import requests as _r
        self._session = _r.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })
        # Connection pooling: reuse TCP connections across calls
        adapter = _r.adapters.HTTPAdapter(
            pool_connections=4,
            pool_maxsize=16,
            max_retries=1,
        )
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def close(self):
        """Release the underlying connection pool (P2-6 fix)."""
        if hasattr(self, '_session'):
            self._session.close()

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()

    def __call__(self, input: Documents) -> Embeddings:
        if not input:
            return []
        import os as _os
        timeout = int(_os.environ.get("EMBEDDING_TIMEOUT", "120"))
        resp = self._session.post(
            f"{self.base_url}/embeddings",
            json={"model": self.model, "input": input},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in data["data"]]


class BuiltinEmbeddingFunction(EmbeddingFunction):
    """Wraps ChromaDB's default ONNX embedding (all-MiniLM-L6-v2)."""

    def __init__(self, embedding_fn: EmbeddingFunction | None = None):
        self._fn = embedding_fn or DefaultEmbeddingFunction()

    def __call__(self, input: Documents) -> Embeddings:
        return self._fn(input)


class FallbackEmbeddingFunction(EmbeddingFunction):
    """Tries primary embedding function first, falls back to fallback on any exception.

    Validates output dimension consistency on every call to prevent silent
    data corruption when primary and fallback models have different dimensions.
    """

    # Counters for monitoring fallback health
    fallback_count: int = 0
    _last_fallback_at: float = 0.0

    def __init__(self, primary: EmbeddingFunction, fallback: EmbeddingFunction):
        self._primary = primary
        self._fallback = fallback
        self._primary_dim: int | None = None
        self._fallback_dim: int | None = None

    def __call__(self, input: Documents) -> Embeddings:
        import time as _time
        try:
            result = self._primary(input)
            if result:
                dim = len(result[0])
                if self._primary_dim is None:
                    self._primary_dim = dim
                if self._fallback_dim is not None and dim != self._fallback_dim:
                    raise RuntimeError(
                        f"Primary embedding dimension {dim} != fallback dimension {self._fallback_dim}. "
                        "Refusing to use dimension-inconsistent fallback."
                    )
            return result
        except Exception as exc:
            FallbackEmbeddingFunction.fallback_count += 1
            FallbackEmbeddingFunction._last_fallback_at = _time.time()
            logger.warning(
                "Primary embedding failed (%s), falling back to built-in "
                "(fallback #%d since startup)", exc, FallbackEmbeddingFunction.fallback_count)
            fallback_result = self._fallback(input)
            if fallback_result:
                dim = len(fallback_result[0])
                if self._fallback_dim is None:
                    self._fallback_dim = dim
                if self._primary_dim is not None and dim != self._primary_dim:
                    raise RuntimeError(
                        f"Fallback embedding dimension {dim} != primary dimension {self._primary_dim}. "
                        "Refusing to use dimension-inconsistent fallback."
                    ) from exc
            return fallback_result
