from __future__ import annotations
import json
import logging
import re
from typing import TYPE_CHECKING, ClassVar
from knowledge_base.core.sqlite_store import SqliteStore
from knowledge_base.repository.interfaces import KnowledgeBaseRepository

if TYPE_CHECKING:
    from chromadb import EmbeddingFunction
from knowledge_base.models.schemas import RawIndexNode, BaseFact, FactStatus

logger = logging.getLogger(__name__)


NODES_COLLECTION = "raw_nodes"
FACTS_COLLECTION = "normalized_facts"

# Valid scope patterns — used to guard against prompt injection in where clauses.
_VALID_SCOPE_RE = re.compile(r"^(global|teachers|users/[a-zA-Z0-9_.-]+)$")

# Scope-isolated collections: when True, each scope gets its own ChromaDB
# collection instead of sharing a single collection with metadata filtering.
# This provides storage-level isolation at the cost of more collections.
_USE_SCOPE_COLLECTIONS = False
_SCOPE_COLLECTION_PREFIX = "scope_"


def get_chroma_client(path: str = ""):
    """Return a ChromaDB client, preferring HTTP service mode when available.

    Set ``CHROMA_HOST`` / ``CHROMA_PORT`` to use the standalone chroma-server
    (concurrent reads, no SQLite write locks).  If the HTTP server is
    unreachable, auto-starts chroma-server and retries.

    When ``CHROMA_HOST`` / ``CHROMA_PORT`` are explicitly configured (production
    mode), this function will **never** fall back to embedded PersistentClient —
    it raises ``RuntimeError`` instead.  Fallback to embedded only happens when
    no HTTP env vars are set (local development).

    Token authentication is supported via ``CHROMA_AUTH_TOKEN`` env var.
    """
    import chromadb
    import os
    import logging
    host = os.environ.get("CHROMA_HOST", "")
    port = os.environ.get("CHROMA_PORT", "")
    http_configured = bool(host and port)

    if not path:
        path = os.environ.get("CHROMA_DB_PATH", "data/chroma")

    # Build ChromaDB Settings for token auth (if configured)
    def _http_client():
        token = os.environ.get("CHROMA_AUTH_TOKEN", "")
        if token:
            settings = chromadb.Settings(
                chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
                chroma_client_auth_credentials=token,
            )
            return chromadb.HttpClient(host=host, port=int(port), settings=settings)
        return chromadb.HttpClient(host=host, port=int(port))

    if http_configured:
        # ── Production path: HTTP required ────────────────────────────
        try:
            client = _http_client()
            client.heartbeat()
            logging.getLogger(__name__).info("ChromaDB HTTP mode: %s:%s", host, port)
            return client
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "ChromaDB HTTP %s:%s unreachable (%s), attempting auto-start",
                host, port, exc,
            )
            try:
                from knowledge_base.utils.service_manager import ensure_chroma_server
                if ensure_chroma_server():
                    client = _http_client()
                    client.heartbeat()
                    logging.getLogger(__name__).info(
                        "ChromaDB HTTP mode: %s:%s (auto-started)", host, port
                    )
                    return client
            except Exception as auto_exc:
                logging.getLogger(__name__).warning(
                    "Auto-start chroma-server failed: %s", auto_exc
                )
                # HTTP was explicitly configured — never fall back to embedded.
                # Falling back would create a PersistentClient that competes with
                # chroma-server (which may be starting in the background).
                raise RuntimeError(
                    f"ChromaDB HTTP {host}:{port} 不可用且自动启动失败。"
                    f"请手动执行: sudo systemctl start chroma-server"
                ) from auto_exc
            # HTTP was explicitly configured — never fall back to embedded.
            # Falling back would create a PersistentClient that competes with
            # chroma-server (which may be starting in the background).
            raise RuntimeError(
                f"ChromaDB HTTP {host}:{port} 不可用（已尝试自动启动未确认成功）。"
                f"请手动执行: sudo systemctl start chroma-server"
            ) from exc

    # ── Local dev path: no HTTP configured, use embedded ──────────────
    return chromadb.PersistentClient(path=path)


class ChromaRepository(KnowledgeBaseRepository):
    """ChromaDB-backed repository for vector storage and retrieval.

    Supports two operational modes:
    - **HTTP mode** (production): Connects to standalone chroma-server via
      ``CHROMA_HOST``/``CHROMA_PORT``.  Token auth via ``CHROMA_AUTH_TOKEN``.
    - **Embedded mode** (local dev): Falls back to ``PersistentClient`` when
      no HTTP env vars are configured.

    Singleton pattern via :meth:`init_instance` / :meth:`instance`.

    Collections:
    - ``raw_nodes`` — document chunks (vector search + metadata filtering)
    - ``normalized_facts`` — extracted knowledge facts (key-value lookup)
    """

    _instance: ClassVar[ChromaRepository | None] = None

    def __init__(self, client, embedding_function: EmbeddingFunction | None = None, sqlite_store: "SqliteStore" | None = None):
        """Internal constructor — use :meth:`init_instance` or factory instead."""
        self._client = client
        self._embedding_function = embedding_function
        self._sqlite_store = sqlite_store

    # ── singleton ───────────────────────────────────────────────────────

    @classmethod
    def init_instance(cls, client, embedding_function: EmbeddingFunction | None = None, sqlite_store: "SqliteStore" | None = None) -> ChromaRepository:
        """Initialize the global singleton (called once at gateway startup).

        Args:
            client: ChromaDB ``HttpClient`` or ``PersistentClient``.
            embedding_function: Embedding function for vectorization.
            sqlite_store: ``SqliteStore`` for quota and version metadata.

        Raises:
            RuntimeError: If the singleton is already initialized.
        """
        if cls._instance is not None:
            raise RuntimeError("ChromaRepository already initialized")
        cls._instance = cls(client, embedding_function, sqlite_store=sqlite_store)
        return cls._instance

    @classmethod
    def instance(cls) -> ChromaRepository:
        """Return the global singleton.

        Auto-initialises via :func:`~knowledge_base.bootstrap.init_chroma_repository_singleton`
        if the singleton has not been initialised yet (e.g. the kb_init hook
        did not fire).  This eliminates the single point of failure where a
        missed ``gateway:startup`` event leaves all RAG tools unavailable.
        """
        if cls._instance is None:
            from knowledge_base.bootstrap import init_chroma_repository_singleton
            init_chroma_repository_singleton()
        if cls._instance is None:
            raise RuntimeError(
                "ChromaRepository not initialized — auto-init failed. "
                "Check ChromaDB connectivity and CHROMA_AUTH_TOKEN."
            )
        return cls._instance

    @classmethod
    def is_ready(cls) -> bool:
        """Return ``True`` if the singleton has been initialized."""
        return cls._instance is not None

    def enable_scope_collections(self) -> None:
        """Enable per-scope ChromaDB collections for storage-level isolation.

        When enabled, each scope (global, assistants/{id}, users/{id}) gets
        its own collection instead of sharing ``raw_nodes`` with metadata
        filtering.  Disabled by default — single-collection with metadata
        ``where`` clauses is the standard mode.
        """
        # Use module-level flag
        import knowledge_base.repository.chroma_repository as mod
        mod._USE_SCOPE_COLLECTIONS = True

    # ── helpers ────────────────────────────────────────────────────────

    def _collection(self, name: str):
        kwargs = {}
        if self._embedding_function is not None:
            kwargs["embedding_function"] = self._embedding_function
        return self._client.get_or_create_collection(name, **kwargs)

    @staticmethod
    def _validate_scopes(scopes: list[str]) -> None:
        for s in scopes:
            if not _VALID_SCOPE_RE.match(s):
                raise ValueError(f"Invalid scope token: {s!r}")

    @staticmethod
    def _scope_to_collection_name(scope: str) -> str:
        safe = scope.replace("/", "_").replace("-", "_").replace(".", "_")
        return f"{_SCOPE_COLLECTION_PREFIX}{safe}"

    def _scope_collection(self, scope: str):
        """Get or create a scope-isolated collection for the given scope."""
        col_name = self._scope_to_collection_name(scope)
        return self._collection(col_name)

    @staticmethod
    def _scope_filter(scope: str) -> dict:
        return {"scope": scope}

    @staticmethod
    def _scope_and_source_filter(scope: str, source_file: str) -> dict:
        return {"$and": [{"scope": scope}, {"source_file": source_file}]}

    # ── nodes ──────────────────────────────────────────────────────────

    def store_nodes(self, scope: str, nodes: list[RawIndexNode]) -> None:
        """Persist document chunks into ChromaDB with batch upsert.

        Uses ``upsert()`` for idempotent writes — re-ingesting the same
        document with deterministic chunk IDs naturally overwrites existing
        chunks instead of creating orphans.

        Batched at ``CHROMA_BATCH_SIZE`` (default 50) to avoid overwhelming
        the embedding API.

        Side effects: embeds each node's content via the configured embedding
        function, stores vectors + metadata in the collection.
        """
        if not nodes:
            return
        self._validate_scopes([scope])
        self._invalidate_user_scopes_cache()

        # Batch storage — large files (e.g. 50MB PDF → 200+ chunks) would
        # overwhelm the embedding API in a single request.  Split into
        # smaller batches to keep each call under the timeout.
        import os as _os
        batch_size = int(_os.environ.get("CHROMA_BATCH_SIZE", "50"))
        for batch_start in range(0, len(nodes), batch_size):
            batch = nodes[batch_start:batch_start + batch_size]

            if _USE_SCOPE_COLLECTIONS:
                col = self._scope_collection(scope)
                legacy = self._collection(NODES_COLLECTION)
                legacy.upsert(
                    ids=[n.node_id for n in batch],
                    documents=[n.content for n in batch],
                    metadatas=[{**n.to_chroma_metadata(), "scope": scope} for n in batch],
                )
            else:
                col = self._collection(NODES_COLLECTION)
            # Use upsert() for idempotent writes — with deterministic chunk IDs
            # (see schemas.make_chunk_id), re-ingesting the same document
            # naturally overwrites existing chunks instead of creating orphans.
            col.upsert(
                ids=[n.node_id for n in batch],
                documents=[n.content for n in batch],
                metadatas=[
                    {**n.to_chroma_metadata(), "scope": scope}
                    for n in batch
                ],
            )

    def search_nodes(
        self, scopes: list[str], query_embedding: list[float], top_k: int = 5,
        include_distances: bool = False,
    ) -> list[RawIndexNode]:
        """Vector search across allowed scopes, returning top-k document chunks.

        When ``include_distances=True``, each returned node has its
        ``distance`` field set to the cosine distance from the query.

        Scope filtering is done via ChromaDB ``where`` clause — scopes are
        validated against ``_VALID_SCOPE_RE`` before the query.
        """
        self._validate_scopes(scopes)
        include = ["documents", "metadatas"]
        if include_distances:
            include.append("distances")
        if _USE_SCOPE_COLLECTIONS:
            all_results: list[RawIndexNode] = []
            all_dists: list[float] = []
            for scope in scopes:
                try:
                    col = self._scope_collection(scope)
                    result = col.query(
                        query_embeddings=[query_embedding],
                        n_results=top_k, include=include,
                    )
                    nodes = self._deserialize_nodes(result)
                    all_results.extend(nodes)
                    if include_distances:
                        dists = result.get("distances", [[]])[0]
                        if dists: all_dists.extend(dists)
                except Exception:
                    logger.warning("scope search failed for %s", scope, exc_info=True)
            seen: set[str] = set()
            deduped: list[RawIndexNode] = []
            deduped_dists: list[float] = []
            for i, node in enumerate(all_results):
                if node.node_id not in seen:
                    seen.add(node.node_id)
                    deduped.append(node)
                    if i < len(all_dists):
                        deduped_dists.append(all_dists[i])
            if include_distances and deduped_dists:
                for node, d in zip(deduped, deduped_dists, strict=False):
                    node.distance = d  # store cosine distance in dedicated field
            return deduped[:top_k]
        col = self._collection(NODES_COLLECTION)
        where = {"scope": {"$in": scopes}} if len(scopes) > 1 else self._scope_filter(scopes[0])
        result = col.query(
            query_embeddings=[query_embedding],
            n_results=top_k, where=where, include=include,
        )
        nodes = self._deserialize_nodes(result)
        if include_distances:
            dists = result.get("distances", [[]])[0]
            if dists:
                for node, d in zip(nodes, dists, strict=False):
                    node.distance = d  # store cosine distance in dedicated field
        return nodes

    @staticmethod
    def _deserialize_nodes(result: dict) -> list[RawIndexNode]:
        ids = result.get("ids", [])
        if not ids or not ids[0]:
            return []
        metadatas = result.get("metadatas")
        documents = result.get("documents")
        nodes = []
        for i, node_id in enumerate(ids[0]):
            meta = {}
            if metadatas and len(metadatas[0]) > i:
                meta = metadatas[0][i] if isinstance(metadatas[0], list) else {}
            doc = ""
            if documents and len(documents[0]) > i:
                doc = documents[0][i] if isinstance(documents[0], list) else ""
            nodes.append(RawIndexNode(
                node_id=node_id,
                scope=meta.get("scope", ""),
                source_tier=meta.get("source_tier", ""),
                source_file=meta.get("source_file", ""),
                source_path=meta.get("source_path", ""),
                doc_version=meta.get("doc_version"),
                visibility_tag=meta.get("visibility_tag", "public"),
                content=doc,
                section_title=meta.get("section_title"),
                section_path=meta.get("section_path"),
                page_start=meta.get("page_start"),
                page_end=meta.get("page_end"),
                sheet_name=meta.get("sheet_name"),
                row_start=meta.get("row_start"),
                row_end=meta.get("row_end"),
                col_start=meta.get("col_start"),
                col_end=meta.get("col_end"),
                anchor_text=meta.get("anchor_text", ""),
                anchor_locator=meta.get("anchor_locator", ""),
                source_hash=meta.get("source_hash", ""),
                parser_version=meta.get("parser_version", ""),
                prev_node_id=meta.get("prev_node_id"),
                next_node_id=meta.get("next_node_id"),
                parse_confidence=float(meta.get("parse_confidence", 1.0)),
            ))
        return nodes

    def delete_nodes(self, scope: str, source_file: str | None = None) -> bool:
        """Delete nodes from *scope*, optionally filtered by *source_file*.

        When *source_file* is ``None``, deletes **all** nodes in the scope.
        Always returns ``True`` (errors are logged, not raised).
        """
        self._validate_scopes([scope])
        self._invalidate_user_scopes_cache()
        if _USE_SCOPE_COLLECTIONS:
            col = self._scope_collection(scope)
            if source_file:
                col.delete(where={"source_file": source_file})
            else:
                # Delete the entire collection and recreate
                try:
                    self._client.delete_collection(self._scope_to_collection_name(scope))
                except Exception:
                    logger.warning("delete_collection failed for scope %s", scope, exc_info=True)
                self._scope_collection(scope)  # recreate empty
        else:
            try:
                col = self._collection(NODES_COLLECTION)
                where = (
                    self._scope_and_source_filter(scope, source_file)
                    if source_file
                    else self._scope_filter(scope)
                )
                col.delete(where=where)
            except Exception:
                logger.warning("delete_nodes failed for scope %s", scope, exc_info=True)
        return True

    def count_nodes(self, scope: str, source_file: str | None = None,
                    raise_on_error: bool = False) -> int:
        """Return the number of nodes in *scope*, optionally filtered by *source_file*.

        Limited to 5000 results per query — adequate for current collection
        sizes.  Returns 0 on error (errors are logged, not raised) unless
        *raise_on_error* is set — the delete path needs a strict count, since
        a silent 0 there would look like a successful deletion.
        """
        self._validate_scopes([scope])
        col = self._collection(NODES_COLLECTION)
        where = (
            self._scope_and_source_filter(scope, source_file)
            if source_file
            else self._scope_filter(scope)
        )
        try:
            result = col.get(where=where, limit=5000, include=[])
            return len(result.get("ids", []))
        except Exception:
            logger.warning("count_nodes failed for scope %s", scope, exc_info=True)
            if raise_on_error:
                raise
            return 0

    def list_source_files(self, scope: str) -> dict[str, int]:
        """Return ``{source_file: node_count}`` for every file in *scope*.

        One ``col.get`` for the whole scope (the orphan scan must not become
        N+1, §4.5).  **Raises on failure** — a silent empty mapping would make
        every metadata row look like an orphan.
        """
        self._validate_scopes([scope])
        col = self._collection(NODES_COLLECTION)
        result = col.get(where=self._scope_filter(scope), include=["metadatas"])
        counts: dict[str, int] = {}
        for m in (result.get("metadatas") or []):
            source_file = m.get("source_file")
            if source_file:
                counts[source_file] = counts.get(source_file, 0) + 1
        return counts

    def count_collection_nodes(self) -> int:
        """Return the total number of nodes in the shared ``raw_nodes`` collection.

        Used as the orphan-scan guard: an empty collection means Chroma is
        misconfigured or wiped, not that every metadata row is an orphan
        (§4.5).  **Raises on failure** (no silent 0).
        """
        return self._collection(NODES_COLLECTION).count()

    def get_file_source(self, scope: str, source_file: str) -> str:
        """Return the ``source`` metadata tag of one node in *scope*.

        Used by the delete preview to tell auto-fetched notices from human
        uploads (§4.2).  Returns ``""`` when the file has no nodes.
        """
        self._validate_scopes([scope])
        col = self._collection(NODES_COLLECTION)
        result = col.get(
            where=self._scope_and_source_filter(scope, source_file),
            limit=1, include=["metadatas"],
        )
        metadatas = result.get("metadatas") or []
        return metadatas[0].get("source", "") if metadatas else ""

    def get_file_content_hash(self, scope: str, source_file: str) -> str:
        """Return the stored ``source_hash`` (content hash) of one node, or ""."""
        self._validate_scopes([scope])
        col = self._collection(NODES_COLLECTION)
        result = col.get(
            where=self._scope_and_source_filter(scope, source_file),
            limit=1, include=["metadatas"],
        )
        metadatas = result.get("metadatas") or []
        return (metadatas[0].get("source_hash") or "") if metadatas else ""

    def backfill_missing_metadata(self, scope: str, default_user: str = "admin") -> int:
        """为「有向量、无元数据」的文件补登记元数据（修复历史静默失败）。

        返回补登记条数；幂等（已有元数据的文件跳过）。content_hash 取自节点上的
        ``source_hash``，file_hash 历史不可考故留空。
        """
        if not self._sqlite_store:
            return 0
        counts = self.list_source_files(scope)
        existing = {r["filename"] for r in self._sqlite_store.list_file_metadata_by_scope(scope)}
        n = 0
        for filename in counts:
            if filename in existing:
                continue
            content_hash = self.get_file_content_hash(scope, filename)
            if not content_hash:
                continue
            self.set_file_metadata(default_user, filename,
                                   {"content_hash": content_hash, "scope": scope, "file_hash": ""})
            n += 1
        return n

    def get_file_documents(self, scope: str, source_file: str) -> list[str]:
        """Return the chunk texts of *source_file* in *scope*, ordered by node_id.

        Used by the read-only preview endpoint (§4.7.2) to reassemble the
        original text — the uploaded file itself is not kept.  **Raises on
        failure** (a silent empty list would look like a missing file).
        """
        self._validate_scopes([scope])
        col = self._collection(NODES_COLLECTION)
        result = col.get(
            where=self._scope_and_source_filter(scope, source_file),
            include=["documents"],
        )
        pairs = zip(result.get("ids") or [], result.get("documents") or [], strict=False)
        return [doc for _, doc in sorted(pairs, key=lambda pair: pair[0])]

    # ── facts ──────────────────────────────────────────────────────────

    def store_facts(self, scope: str, facts: list[BaseFact]) -> None:
        """Persist extracted knowledge facts into the ``normalized_facts`` collection.

        Each fact is stored as a JSON document with metadata tags
        (scope, fact_type, fact_key, status, assistant_id).
        """
        if not facts:
            return
        self._validate_scopes([scope])
        col = self._collection(FACTS_COLLECTION)
        col.upsert(
            ids=[f.fact_id for f in facts],
            documents=[f.model_dump_json() for f in facts],
            metadatas=[
                {
                    "scope": scope,
                    "fact_type": f.fact_type,
                    "fact_key": f.fact_key,
                    "status": f.status.value,
                    "assistant_id": f.assistant_id,
                }
                for f in facts
            ],
        )

    # ── facts retrieval ────────────────────────────────────────────────

    def get_facts_by_keys(
        self, scopes: list[str], fact_keys: list[str],
        include_pending_review: bool = False,
    ) -> list[BaseFact]:
        """Lookup facts by keys across allowed scopes.

        Args:
            scopes: Allowed scope values (e.g. ``["global", "teachers"]``).
            fact_keys: Fact keys to match.
            include_pending_review: If ``True``, include facts with
                ``PENDING_REVIEW`` status (hidden by default).
        """
        if not scopes or not fact_keys:
            return []
        self._validate_scopes(scopes)
        col = self._collection(FACTS_COLLECTION)
        result = col.get(
            where={
                "$and": [
                    {"scope": {"$in": scopes}},
                    {"fact_key": {"$in": fact_keys}},
                ]
            }
        )
        facts = self._deserialize_facts(result)
        if not include_pending_review:
            facts = [f for f in facts if f.status != FactStatus.PENDING_REVIEW]
        return facts

    @staticmethod
    def _deserialize_facts(result: dict) -> list[BaseFact]:
        ids = result.get("ids", [])
        if not ids:
            return []
        facts = []
        for _i, doc in enumerate(result.get("documents", [])):
            fact_data = json.loads(doc)
            facts.append(BaseFact.model_validate(fact_data))
        return facts

    # ── scope introspection ────────────────────────────────────────────

    # Cache of the last get_all_user_scopes() scan — invalidated on any
    # write (store_nodes / delete_nodes / purge).  Prevents a full-collection
    # metadata scan on every admin search.
    _user_scopes_cache: ClassVar[list[str] | None] = None

    def get_all_user_scopes(self) -> list[str]:
        """Return sorted list of distinct ``users/*`` scope values.

        Used by ``ACLFilter`` to grant admin/owner cross-user read access.
        Paginates through metadata in batches (configurable via
        ``SCOPE_SCAN_LIMIT``, default 10000).  Logs a warning when the
        total node count exceeds the scan limit — scopes beyond the limit
        are invisible to admin cross-user reads until the limit is raised.
        Returns empty list on error.

        Result is cached and invalidated on any node write (see
        ``_invalidate_user_scopes_cache``).
        """
        if ChromaRepository._user_scopes_cache is not None:
            return ChromaRepository._user_scopes_cache

        col = self._collection(NODES_COLLECTION)
        import os as _os
        batch = int(_os.environ.get("SCOPE_SCAN_LIMIT", "10000"))
        scopes: set[str] = set()
        offset = 0
        try:
            while True:
                result = col.get(include=["metadatas"], limit=batch, offset=offset)
                metadatas = result.get("metadatas", []) or []
                if not metadatas:
                    break
                for m in metadatas:
                    if m and "scope" in m and m["scope"].startswith("users/"):
                        scopes.add(m["scope"])
                if len(metadatas) < batch:
                    break
                offset += batch
                if offset >= batch * 10:  # safety valve: max 10 batches
                    logger.warning(
                        "get_all_user_scopes: reached %d nodes without exhausting "
                        "collection — stopping scan.  Raise SCOPE_SCAN_LIMIT if "
                        "more user scopes are expected.", offset,
                    )
                    break
        except Exception:
            logger.warning("get_all_user_scopes failed", exc_info=True)
            return []
        ChromaRepository._user_scopes_cache = sorted(scopes)
        return ChromaRepository._user_scopes_cache

    @staticmethod
    def _invalidate_user_scopes_cache() -> None:
        """Drop the cached scope list after any write that may add/remove users/*."""
        ChromaRepository._user_scopes_cache = None

    # ── purge ──────────────────────────────────────────────────────────

    def purge_scope(self, scope: str) -> None:
        """Delete all nodes and facts in *scope* from ChromaDB.

        In scope-collection mode, deletes the entire collection and recreates
        it empty.  Errors are logged, not raised — this is a best-effort
        destructive operation.
        """
        self._validate_scopes([scope])
        self._invalidate_user_scopes_cache()
        if _USE_SCOPE_COLLECTIONS:
            try:
                self._client.delete_collection(self._scope_to_collection_name(scope))
            except Exception:
                logger.warning("purge_scope delete_collection failed for %s", scope, exc_info=True)
            try:
                col = self._scope_collection(scope)
            except Exception:
                logger.warning("purge_scope recreate collection failed for %s", scope, exc_info=True)
            where = self._scope_filter(scope)  # defined for FACTS_COLLECTION delete below
        else:
            where = self._scope_filter(scope)
            self._collection(NODES_COLLECTION).delete(where=where)
        self._collection(FACTS_COLLECTION).delete(where=where)

    # ── quota & versioning (delegated to SqliteStore) ────────────────

    def get_user_file_count(self, user_id: str) -> int:
        """Return the total number of files ingested by *user_id*."""
        if self._sqlite_store:
            return self._sqlite_store.get_user_file_count(user_id)
        return 0

    def get_user_daily_upload_count(self, user_id: str) -> int:
        """Return the number of files *user_id* has uploaded today."""
        if self._sqlite_store:
            return self._sqlite_store.get_user_daily_upload_count(user_id)
        return 0

    def get_user_recent_upload_count(self, user_id: str, window_seconds: int = 60) -> int:
        """Return uploads by *user_id* in the last *window_seconds* (rate limit)."""
        if self._sqlite_store:
            return self._sqlite_store.get_user_recent_upload_count(user_id, window_seconds=window_seconds)
        return 0

    def record_upload(self, user_id: str) -> None:
        """Record an upload event for per-minute rate limiting."""
        if self._sqlite_store:
            self._sqlite_store.record_upload(user_id)

    def get_file_metadata(self, user_id: str, filename: str, scope: str = "") -> dict[str, str] | None:
        """Get version metadata for a file previously ingested by *user_id*.

        Returns ``{"content_hash": ...}`` or ``None`` if not found.
        When *scope* is empty, matches any scope.
        """
        if self._sqlite_store:
            return self._sqlite_store.get_file_metadata(user_id, filename, scope)
        return None

    def get_file_metadata_any_user(self, filename: str, scope: str) -> dict[str, str] | None:
        """Check if *filename* exists in *scope* for **any** user.

        Used for shared scopes (global, teachers) where cross-user dedup
        matters — prevents the same file from being ingested twice even when
        uploaded by different users.
        """
        if self._sqlite_store:
            return self._sqlite_store.get_file_metadata_any_user(filename, scope)
        return None

    def list_file_metadata_by_scope(self, scope: str, order: str = "asc") -> list[dict[str, str]]:
        """Return every metadata row in *scope* (list endpoint data source)."""
        if self._sqlite_store:
            return self._sqlite_store.list_file_metadata_by_scope(scope, order)
        return []

    def set_file_metadata(self, user_id: str, filename: str, metadata: dict[str, str]) -> None:
        """Record file metadata (content_hash, scope, file_hash) for dedup.

        Increments the daily upload counter for *user_id* on first insert.
        Re-ingests (same user + filename + scope) do not re-increment.
        """
        if self._sqlite_store:
            self._sqlite_store.set_file_metadata(user_id, filename, metadata)

    def file_hash_exists(self, file_hash: str, scope: str = "") -> bool:
        """Check if *file_hash* exists in *scope* (default: global lookup)."""
        if self._sqlite_store:
            return self._sqlite_store.get_file_metadata_by_hash(file_hash, scope=scope) is not None
        return False

    def delete_file_metadata(self, user_id: str, filename: str, scope: str) -> None:
        """Delete metadata for a single file (used by orchestrator orphan cleanup)."""
        if self._sqlite_store:
            self._sqlite_store.delete_file_metadata(user_id, filename, scope)

    def delete_user_data(self, user_id: str) -> None:
        """Remove all quota/version metadata for a user (cascade delete)."""
        if self._sqlite_store:
            self._sqlite_store.delete_user_data(user_id)
