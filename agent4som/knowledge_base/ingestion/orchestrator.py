"""
IngestionOrchestrator — end-to-end file ingestion pipeline.

Ties together:
- ``ExtractorRouter``      → file type detection
- ``QuotaManager``          → size / daily / count limits
- ``VersionManager``        → content dedup & versioning
- ``KnowledgeBaseRepository`` → persistent storage (ChromaDB)

Usage::

    orch = IngestionOrchestrator(repo, quota, version, router)
    result = orch.ingest_file(user_id, file_path)
    # result.status tells you what happened
    # result.notification is ready for the gateway hook
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from enum import Enum
from knowledge_base.models.schemas import make_chunk_id


from knowledge_base.core.exceptions import (
    DailyLimitExceededError,
    FileTooLargeError,
    MaxFilesExceededError,
    RateLimitExceededError,
)
from knowledge_base.core.quota_manager import QuotaManager
from knowledge_base.core.version_manager import IngestAction, VersionManager
from knowledge_base.ingestion.semantic_splitter import chunk_document
from knowledge_base.models.schemas import RawIndexNode
from knowledge_base.pipeline.router import ExtractorRouter, FileType
from knowledge_base.repository.interfaces import KnowledgeBaseRepository
from knowledge_base.auth.role_store import resolve_role, ROLE_OWNER, ROLE_ADMIN
from knowledge_base.ingestion.parsers import read_file
import contextlib

# Lazy-import LlamaIndex modules at module level (avoid re-import on every parse call)
try:
    from knowledge_base.llamaindex.readers import create_simple_reader, StructuredExcelReader
    from knowledge_base.llamaindex.ingestion_pipeline import create_ingestion_pipeline
    from knowledge_base.llamaindex.chroma_vector_store import node_to_raw_index_node
    _LLAMAINDEX_READY = True
except ImportError:
    _LLAMAINDEX_READY = False

logger = logging.getLogger(__name__)


class IngestionStatus(str, Enum):
    """Outcome of an ingestion attempt."""

    INGESTED = "ingested"
    SKIPPED = "skipped"  # duplicate, content unchanged
    REPLACED = "replaced"  # old content replaced by new
    QUOTA_EXCEEDED = "quota_exceeded"
    UNSUPPORTED_TYPE = "unsupported_type"
    FILE_NOT_FOUND = "file_not_found"
    ERROR = "error"


@dataclass
class IngestResult:
    """Result returned by ``IngestionOrchestrator.ingest_file``."""

    status: IngestionStatus
    filename: str
    file_size_bytes: int = 0
    file_type: str = ""
    node_count: int = 0
    warning: str | None = None
    notification: str | None = None
    error_detail: str | None = None


# Chunk size for splitting document content into searchable nodes
_DEFAULT_CHUNK_SIZE = 512
_CHUNK_OVERLAP = 32


class IngestionOrchestrator:
    """Orchestrates the end-to-end file ingestion pipeline.

    Each call to ``ingest_file`` runs: route → quota → version → parse → store.
    """

    def __init__(
        self,
        repo: KnowledgeBaseRepository,
        quota_manager: QuotaManager,
        version_manager: VersionManager,
        router: ExtractorRouter,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
    ):
        self._repo = repo
        self._quota = quota_manager
        self._version = version_manager
        self._router = router
        self._chunk_size = chunk_size

    # Path safety: sensitive system directories that ingest_file must never read from.
    _FORBIDDEN_PATH_PREFIXES = ("/proc/", "/sys/", "/dev/")

    # Allowed base directories for file ingestion (colon-separated, from env).
    # When set, only files under these dirs are accepted.  When empty (default
    # for CLI/admin usage), any regular file outside _FORBIDDEN_PATH_PREFIXES
    # is allowed.
    _ALLOWED_DIRS: tuple[str, ...] = tuple(
        d for d in os.environ.get("INGEST_ALLOWED_DIRS", "").split(":")
        if d
    )

    # ── Public API ────────────────────────────────────────────────────

    def ingest_file(self, user_id: str, file_path: str, scope: str | None = None,
                    platform: str = "wecom", source: str = "",
                    count_quota: bool = True) -> IngestResult:
        """Ingest a single file for a user.

        Args:
            user_id: The user performing the ingestion.
            file_path: Absolute path to the file.
            scope: Storage scope.  Defaults to ``"users/{user_id}"`` for user uploads.
                   Callers providing admin / sync-kb paths should pass ``"global"``
                   or ``"assistants/{id}"`` explicitly.
            count_quota: When False, skips the quota check.  Used by
                   multi-scope ingest callers (knowledge_ingest) that
                   pre-check quota once for the whole call.
            platform: Platform identifier for role resolution (default ``"wecom"``).

        Returns an ``IngestResult`` describing what happened.  Never raises —
        errors are encoded in ``result.status``.
        """
        # 0. Validate file path (realpath + regular-file check + directory allowlist).
        safe_path, path_error = IngestionOrchestrator._validate_file_path(file_path)
        if path_error:
            raw_filename = os.path.basename(file_path)
            return IngestResult(
                status=IngestionStatus.FILE_NOT_FOUND,
                filename=raw_filename,
                notification=path_error,
            )
        file_path = safe_path
        raw_filename = os.path.basename(file_path)
        # Strip WeCom cache prefix (doc_{uuid}_) for dedup with original file
        import re as _re
        filename = _re.sub(r"^doc_[a-f0-9]{12}_", "", raw_filename)

        # 1. Check file exists
        if not os.path.isfile(file_path):
            return IngestResult(
                status=IngestionStatus.FILE_NOT_FOUND,
                filename=filename,
                notification=f"File not found: {filename}",
            )

        file_size = os.path.getsize(file_path)

        # 2. Route file type (pass full path so content-based detection can
        #    read magic bytes when the extension is unknown, e.g. WeCom .bin).
        file_type = self._router.route(file_path)
        if file_type == FileType.UNKNOWN:
            return IngestResult(
                status=IngestionStatus.UNSUPPORTED_TYPE,
                filename=filename,
                file_size_bytes=file_size,
                notification=f"Unsupported file type: {filename}",
            )

        # 2b. Fix mislabeled extensions before downstream processors see them.
        #     WeCom caches images as .bin (URL path wins over magic bytes in
        #     the adapter) — MinerU produces ~9000 chunks for .bin vs ~500
        #     for the identical .jpg.  Rename to the detected extension so
        #     MinerU, LlamaIndex, etc. see the real format.
        file_path, filename, _made_copy = self._ensure_image_extension(file_path, filename)

        def _cleanup_copy():
            if _made_copy:
                with contextlib.suppress(OSError):
                    os.unlink(file_path)

        # 3. Quota check — admin/owner bypass daily and file-count limits
        #    count_quota=False skips this for multi-scope ingest calls where
        #    the caller has already pre-checked quota once.
        quota_result = self._quota_error_result(platform, user_id, file_size, filename, count_quota)
        if quota_result is not None:
            _cleanup_copy()
            return quota_result

        # 3b. Compute raw file hash (bypasses MinerU instability)
        effective_scope = scope or f"users/{user_id}"
        file_hash = IngestionOrchestrator._hash_file_bytes(file_path)

        # 3c. Early dedup: if exact same bytes already ingested *in this scope*, skip.
        #     Same file in a different scope (e.g. global vs users/XiongWei)
        #     is a legitimate separate ingest.
        dup_result = self._early_dedup_result(user_id, filename, file_hash, effective_scope, file_size)
        if dup_result is not None:
            _cleanup_copy()
            return dup_result

        # 4. Read content and compute hash
        content = read_file(file_path)
        if not content or not content.strip():
            _cleanup_copy()
            return IngestResult(
                status=IngestionStatus.ERROR,
                filename=filename,
                file_size_bytes=file_size,
                notification=f"Cannot read content from {filename}",
                error_detail="File produced no readable text",
            )
        content_hash = self._hash_content(content)

        # 5. Version check (dedup) — try normalized name first, then raw cache name
        action, skip_result = self._resolve_ingest_action(
            user_id, filename, raw_filename, content_hash, effective_scope, file_hash, file_size,
        )
        if skip_result is not None:
            _cleanup_copy()
            return skip_result

        # 6. Parse into nodes
        warning = "Structured tables (XLSX/CSV) may need admin review for full accuracy." \
            if file_type == FileType.STRUCTURED_TABLE else None

        # 6. Choose parser: LlamaIndex (default) or legacy
        #    PARSING RUNS FIRST so REPLACE won't lose data if parsing fails (C2)
        use_legacy = os.environ.get("KB_USE_LEGACY_PARSER", "").lower() == "true"

        if use_legacy:
            nodes = self._split_into_nodes(content, filename, effective_scope, file_size, content_hash=content_hash, source=source)
        else:
            nodes = self._parse_with_llamaindex(file_path, filename, effective_scope, content, content_hash, source=source)

        # 7. Enrich with extracted metadata (date, category)
        self._apply_doc_metadata(nodes, filename, content)

        # 8. Track metadata FIRST and REQUIRE success.
        #    若元数据写入被静默吞掉、节点照写，会产生「有向量无元数据」的文件：
        #    检索得到、知识库管理列表却看不到，且同步脚本会误报成功、不再重试
        #    （2026-09-21 的 jxtz_10472~10474 即此故障）。
        try:
            self._track_metadata(user_id, filename, content_hash, effective_scope, file_hash=file_hash)
        except Exception:
            logger.error("Failed to track metadata for %s (user=%s scope=%s)",
                         filename, user_id, effective_scope, exc_info=True)
            _cleanup_copy()
            return IngestResult(
                status=IngestionStatus.ERROR,
                filename=filename,
                file_size_bytes=file_size,
                file_type=file_type.value,
                error_detail="知识库元数据写入失败（数据库忙），本次未入库，请重试。",
            )

        # 8a. Delete old nodes AFTER metadata ok — 元数据失败时不破坏旧数据
        if action == IngestAction.REPLACE:
            self._delete_superseded_nodes(effective_scope, filename)

        # 9. Store nodes (metadata already persisted above — if this fails,
        #    re-run will detect orphan metadata and re-ingest cleanly).
        try:
            self._repo.store_nodes(effective_scope, nodes)
        except Exception:
            logger.error("ChromaDB store_nodes failed for %s (user=%s scope=%s)",
                         filename, user_id, effective_scope, exc_info=True)
            _cleanup_copy()
            return IngestResult(
                status=IngestionStatus.ERROR,
                filename=filename,
                file_size_bytes=file_size,
                file_type=file_type.value,
                error_detail="ChromaDB write failed — please retry or contact IT support.",
            )

        _cleanup_copy()

        status = IngestionStatus.REPLACED if action == IngestAction.REPLACE else IngestionStatus.INGESTED
        notification = (
            f"[{filename} indexed ({len(nodes)} chunks). You can now ask questions about it.]"
        )

        return IngestResult(
            status=status,
            filename=filename,
            file_size_bytes=file_size,
            file_type=file_type.value,
            node_count=len(nodes),
            warning=warning,
            notification=notification,
        )

    # ── Internal helpers ──────────────────────────────────────────────

    def _ensure_image_extension(self, file_path: str, filename: str) -> tuple[str, str, bool]:
        """Fix mislabeled extensions (WeCom .bin image caches) before parsing.

        Returns ``(file_path, filename, made_copy)`` — *made_copy* 为 True 时
        摄入完毕须删除临时副本（见 ``_cleanup_copy``）。
        """
        orig_ext = os.path.splitext(file_path)[1].lower()
        detected_ext = self._router.detect_image_extension(file_path)
        image_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif")
        if not (detected_ext and orig_ext not in image_exts):
            return file_path, filename, False
        new_path = file_path + detected_ext
        made_copy = False
        if not os.path.exists(new_path):
            import shutil as _shutil
            _shutil.copy2(file_path, new_path)
            made_copy = True
        new_filename = os.path.basename(new_path) if filename == os.path.basename(file_path) else filename
        return new_path, new_filename, made_copy

    def _early_dedup_result(self, user_id: str, filename: str, file_hash: str,
                            scope: str, file_size: int) -> IngestResult | None:
        """3c. 按原始字节哈希去重（仅同 scope）。

        返回 ``None`` 表示继续摄入（无重复，或孤儿元数据已清理待重摄）；
        返回 ``IngestResult`` 表示可跳过。
        """
        if not self._repo.file_hash_exists(file_hash, scope=scope):
            return None
        # Verify nodes still exist — if metadata was written but node write
        # failed mid-way (GPU crash), the file_hash is an orphan.  Clean it
        # and continue so the file gets a fresh ingest.
        node_count = self._repo.count_nodes(scope, source_file=filename)
        if node_count == 0:
            logger.warning(
                "Orphan file_hash for %s (user=%s scope=%s) — metadata exists "
                "but no nodes.  Cleaning metadata and re-ingesting.",
                filename, user_id, scope,
            )
            # Delete only this file's metadata, not all user data
            self._repo.delete_file_metadata(user_id, filename, scope)
            return None
        return IngestResult(
            status=IngestionStatus.SKIPPED,
            filename=filename,
            file_size_bytes=file_size,
            node_count=node_count,
            notification=f"[{filename} already indexed — skipping]",
        )

    def _resolve_ingest_action(self, user_id: str, filename: str, raw_filename: str,
                               content_hash: str, scope: str, file_hash: str,
                               file_size: int) -> tuple[IngestAction, IngestResult | None]:
        """5. 版本判定（先归一化名，再原始缓存名）。

        Returns ``(action, skip_result)`` — ``skip_result`` 为 ``None`` 表示
        继续摄入（NEW/REPLACE，或孤儿元数据已清理转 NEW）。
        """
        action = self._version.determine_ingest_action(user_id, filename, content_hash, scope)
        if action == IngestAction.NEW and raw_filename != filename:
            action = self._version.determine_ingest_action(user_id, raw_filename, content_hash, scope)

        if action != IngestAction.SKIP:
            return action, None

        # Verify nodes exist — metadata may be orphaned after a mid-write crash.
        node_count = self._repo.count_nodes(scope, source_file=filename)
        if node_count == 0:
            logger.warning(
                "Orphan metadata for %s (user=%s scope=%s) — "
                "metadata exists but no nodes.  Cleaning and re-ingesting.",
                filename, user_id, scope,
            )
            # Delete only this file's metadata, not all user data
            self._repo.delete_file_metadata(user_id, filename, scope)
            return IngestAction.NEW, None  # fall through to ingestion

        # Backfill file_hash for old records (pre-migration) so future
        # early-file-hash checks can skip even without MinerU stability.
        if file_hash:
            existing = self._repo.get_file_metadata(user_id, filename, scope)
            if existing and not existing.get("file_hash"):
                self._repo.set_file_metadata(user_id, filename, {
                    "content_hash": existing.get("content_hash", ""),
                    "scope": scope, "file_hash": file_hash,
                })
        return action, IngestResult(
            status=IngestionStatus.SKIPPED,
            filename=filename,
            file_size_bytes=file_size,
            node_count=0,
            notification=f"[{filename} already indexed — skipping]",
        )

    def _quota_error_result(self, platform: str, user_id: str, file_size: int,
                            filename: str, count_quota: bool) -> IngestResult | None:
        """3. 配额检查；返回 ``None`` 表示通过。

        admin/owner 绕过每日与文件数限额；``count_quota=False`` 供多 scope
        摄入调用方（knowledge_ingest）整体预检后跳过。
        """
        is_privileged = resolve_role(platform, user_id) in (ROLE_OWNER, ROLE_ADMIN)
        try:
            if count_quota:
                self._quota.check_quota(user_id, file_size, is_admin_or_owner=is_privileged)
        except (FileTooLargeError, DailyLimitExceededError, MaxFilesExceededError, RateLimitExceededError) as exc:
            return IngestResult(
                status=IngestionStatus.QUOTA_EXCEEDED,
                filename=filename,
                file_size_bytes=file_size,
                notification=f"Cannot ingest: {exc}",
                error_detail=str(exc),
            )
        return None

    @staticmethod
    def _apply_doc_metadata(nodes: list, filename: str, content: str) -> None:
        """7. 用抽取出的文档元数据（年份/类别）就地增强节点。"""
        meta_extra = _extract_document_metadata(filename, content)
        for node in nodes:
            if meta_extra.get("doc_year"):
                node.doc_version = str(meta_extra["doc_year"])
            if meta_extra.get("category"):
                current_sf = node.source_file or ""
                if meta_extra["category"] not in current_sf:
                    node.source_file = f"[{meta_extra['category']}] {current_sf}"

    def _delete_superseded_nodes(self, scope: str, filename: str) -> None:
        """6a. REPLACE 时删除旧节点。

        需同时匹配原始文件名与去扩展名版本——LlamaIndex 管线会剥扩展名，
        存量节点按剥后名字入库，单纯文件名匹配会漏删。
        """
        self._repo.delete_nodes(scope, source_file=filename)
        import re as _re_replace
        stripped = _re_replace.sub(r'\.(txt|pdf|docx|doc|pptx|xlsx)$', '', filename)
        if stripped != filename:
            self._repo.delete_nodes(scope, source_file=stripped)


    @staticmethod
    def _validate_file_path(path: str) -> tuple[str, str]:
        """Validate and sanitise *path* before ingestion.

        Returns ``(safe_path, "")`` on success, or ``("", error_message)``
        on failure.  The returned *safe_path* is the resolved realpath.

        Checks (in order):
        1. Resolve symlinks with ``os.path.realpath()``.
        2. Reject non-regular files (devices, FIFOs, sockets).
        3. Reject paths under ``/proc``, ``/sys``, ``/dev``.
        4. If ``INGEST_ALLOWED_DIRS`` is configured, reject paths outside
           the allowed directories.
        """
        try:
            real = os.path.realpath(path)
        except (OSError, ValueError) as exc:
            return "", f"无法解析文件路径: {exc}"

        # Must be a regular file (not a device, FIFO, socket, etc.)
        try:
            st = os.stat(real)
        except OSError as exc:
            return "", f"无法访问文件: {exc}"
        import stat as _stat
        if not _stat.S_ISREG(st.st_mode):
            return "", f"不支持的文件类型: {os.path.basename(real)}"

        # Block sensitive system paths
        for prefix in IngestionOrchestrator._FORBIDDEN_PATH_PREFIXES:
            if real.startswith(prefix):
                return "", f"不允许从系统目录读取文件: {os.path.basename(real)}"

        # If allowed-dirs configured, enforce directory allowlist
        if IngestionOrchestrator._ALLOWED_DIRS:
            allowed = any(real.startswith(d) for d in IngestionOrchestrator._ALLOWED_DIRS)
            if not allowed:
                return "", f"文件路径不在允许的目录范围内: {os.path.basename(real)}"

        return real, ""

    @staticmethod
    def _hash_content(content: str) -> str:
        """SHA-256 of the extracted text content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_file_bytes(path: str) -> str:
        """SHA-256 of the raw file bytes (streaming, no full read into memory)."""
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _split_into_nodes(
        self, content: str, filename: str, scope: str, file_size: int,
        content_hash: str, source: str = "",
    ) -> list[RawIndexNode]:
        """Split content into structure-aware chunks using heading detection.

        Falls back to line-based splitting when a single section exceeds
        *chunk_size* lines.
        """
        raw = chunk_document(
            content=content,
            filename=filename,
            scope=scope,
            source_hash=content_hash,
            max_chunk_lines=max(self._chunk_size, 1),
            source=source,
        )
        # Generate deterministic node_ids from content_hash + chunk_index.
        # Same document always produces the same set of IDs, enabling
        # idempotent upsert() — no more orphaned chunks on REPLACE.
        node_ids = [make_chunk_id(content_hash, i, len(raw), scope=scope) for i in range(len(raw))]
        nodes = []
        for i, r in enumerate(raw):
            prev_id = node_ids[i - 1] if i > 0 else None
            next_id = node_ids[i + 1] if i < len(node_ids) - 1 else None
            nodes.append(RawIndexNode(
                node_id=node_ids[i],
                prev_node_id=prev_id,
                next_node_id=next_id,
                **r,
            ))

        # Normalise source_file to basename only (keep extension for dedup match).
        # jxtz prefix (record_id) is preserved — different URLs with same title
        # must produce distinct source_files to prevent collision.
        for node in nodes:
            if node.source_file:
                node.source_file = os.path.basename(node.source_file)

        return nodes

    def _legacy_nodes(self, content: str, file_path: str, filename: str,
                      scope: str, content_hash: str, source: str) -> list[RawIndexNode]:
        """回退路径：用 legacy 切分器处理预抽取的 *content*。"""
        return self._split_into_nodes(
            content, filename, scope,
            os.path.getsize(file_path),
            content_hash=content_hash, source=source,
        )

    def _maybe_rechunk(self, raw_nodes: list[RawIndexNode], filename: str, scope: str,
                       file_path: str, content_hash: str, source: str) -> list[RawIndexNode]:
        """文章感知重切分：LlamaIndex 不识别「第X条」边界，输出会被合并进超大块。

        仅当重切产生显著更多块（>1.2×）时采用，两个切分器一致的文档保持原样。
        """
        llama_raw = "\n\n".join(n.content for n in raw_nodes)
        re_chunked = self._split_into_nodes(
            llama_raw, filename, scope,
            os.path.getsize(file_path),
            content_hash=content_hash, source=source,
        )
        if not len(re_chunked) > len(raw_nodes) * 1.2:
            return raw_nodes
        logger.info(
            "Re-chunked %s: %d LlamaIndex nodes → %d article-aware chunks",
            filename, len(raw_nodes), len(re_chunked),
        )
        # Preserve LlamaIndex metadata (page numbers, confidence) on
        # re-chunked nodes by matching against original content
        for rc in re_chunked:
            rc.parse_confidence = 1.0
            rc.parser_version = "llamaindex-v1+semantic-rechunk"
        return re_chunked

    def _resolve_quality_gate(self, raw_nodes: list[RawIndexNode], content: str,
                              file_path: str, filename: str, scope: str,
                              content_hash: str, source: str) -> list[RawIndexNode]:
        """内容长度质量门：LlamaIndex 与 _read_file() 相差 >3× 时取文本更多的一方。

        A. _read_file() >> LlamaIndex：扫描 PDF（MinerU OCR > LlamaIndex 封面文本）
        B. LlamaIndex >> _read_file()：二进制 raw read 回退（原始字节 > 抽取文本）
        """
        llama_total = sum(len(n.content) for n in raw_nodes)
        if llama_total == 0 or not (
            len(content) > llama_total * 3 or llama_total > len(content) * 3
        ):
            return raw_nodes
        winner = "MinerU/_read_file" if len(content) > llama_total else "LlamaIndex"
        logger.info(
            "Quality gate triggered for %s: _read_file=%d chars, LlamaIndex=%d chars "
            "(ratio %.1f) — using %s output",
            filename, len(content), llama_total,
            max(len(content), llama_total) / max(min(len(content), llama_total), 1),
            winner,
        )
        if len(content) > llama_total:
            return self._legacy_nodes(content, file_path, filename, scope, content_hash, source)
        # LlamaIndex produced more.  Content is clean text but LlamaIndex output
        # might still be binary garbage (e.g. reader package missing and
        # SimpleDirectoryReader fell back to raw file read) — check both sides.
        if not _is_binary_garbage(content):
            llama_text = "\n".join(n.content[:200] for n in raw_nodes[:5])
            if _is_binary_garbage(llama_text):
                logger.info(
                    "LlamaIndex output for %s appears to be binary (reader "
                    "package missing?), using _read_file output instead",
                    filename,
                )
                return self._legacy_nodes(content, file_path, filename, scope, content_hash, source)
            return raw_nodes  # LlamaIndex genuinely found more content, keep it
        # _read_file() output is binary garbage → use LlamaIndex output instead
        logger.info(
            "LlamaIndex output for %s selected: _read_file() produced binary garbage "
            "(%d null bytes) — using LlamaIndex output instead",
            filename, content.count('\x00'),
        )
        return raw_nodes

    def _parse_with_llamaindex(
        self, file_path: str, filename: str, scope: str,
        content: str, content_hash: str, source: str = "",
    ) -> list[RawIndexNode]:
        """Parse *file_path* with LlamaIndex pipeline.

        Falls back to legacy splitter on any error.
        """
        if not _LLAMAINDEX_READY:
            raise ImportError("LlamaIndex modules not available")

        # Suppress pypdf "wrong pointing object" noise from malformed PDFs
        _pypdf_logger = logging.getLogger("pypdf._reader")
        _pypdf_level = _pypdf_logger.level
        _pypdf_logger.setLevel(logging.ERROR)
        try:
            ext = os.path.splitext(file_path)[1].lower()
            pipeline = create_ingestion_pipeline(chunk_size=self._chunk_size)

            if ext == ".doc":
                # Old-format Word — LlamaIndex DocxReader only handles .docx.
                # Use pre-extracted content from _read_file() (antiword) directly.
                return self._legacy_nodes(content, file_path, filename, scope, content_hash, source)
            if ext in (".xlsx", ".xls"):
                reader = StructuredExcelReader()
                documents = reader.load_data(file=file_path)
            else:
                reader = create_simple_reader(file_path)
                documents = reader.load_data()

            if not documents:
                raise ValueError("LlamaIndex produced no documents")

            llama_nodes = pipeline.run(documents=documents)

            raw_nodes = _assemble_index_nodes(llama_nodes, scope, content_hash, source)

            if not raw_nodes:
                # LlamaIndex produced nodes but all had empty text (e.g. image-based PDF
                # that the LlamaIndex reader couldn't OCR).  Fall back to the pre-extracted
                # *content* which already includes OCR output from _read_file().
                logger.info(
                    "LlamaIndex produced 0 text-bearing nodes for %s, "
                    "falling back to legacy chunker with pre-extracted content",
                    filename,
                )
                return self._legacy_nodes(content, file_path, filename, scope, content_hash, source)

            raw_nodes = self._maybe_rechunk(raw_nodes, filename, scope, file_path, content_hash, source)
            return self._resolve_quality_gate(
                raw_nodes, content, file_path, filename, scope, content_hash, source,
            )
        except Exception as exc:
            logger.warning(
                "LlamaIndex parse failed for %s (%s), falling back to legacy parser",
                filename, exc,
            )
            return self._legacy_nodes(content, file_path, filename, scope, content_hash, source)
        finally:
            _pypdf_logger.setLevel(_pypdf_level)

    def _track_metadata(self, user_id: str, filename: str, content_hash: str, scope: str = "", file_hash: str = "") -> None:
        self._repo.set_file_metadata(user_id, filename, {
            "content_hash": content_hash, "scope": scope, "file_hash": file_hash,
        })


def _assemble_index_nodes(llama_nodes: list, scope: str, content_hash: str,
                          source: str) -> list[RawIndexNode]:
    """LlamaIndex 节点 → RawIndexNode：确定性 chunk ID + 前后链接 + 文件名规整。"""
    raw_nodes = [
        node_to_raw_index_node(n, scope, source=source)
        for n in llama_nodes
        if n.text
    ]
    # Assign deterministic chunk IDs (replaces random UUID from
    # node_to_raw_index_node) so upsert() is idempotent.
    for i, node in enumerate(raw_nodes):
        node.node_id = make_chunk_id(content_hash, i, len(raw_nodes), scope=scope)
    # Link consecutive nodes + clean source_file display name.
    # Keep full basename (including jxtz prefix + extension) so source_file
    # matches VersionManager's filename key.
    for i, node in enumerate(raw_nodes):
        if i > 0:
            node.prev_node_id = raw_nodes[i - 1].node_id
        if i < len(raw_nodes) - 1:
            node.next_node_id = raw_nodes[i + 1].node_id
        if node.source_file:
            node.source_file = os.path.basename(node.source_file)
    return raw_nodes


def _is_binary_garbage(content: str, threshold: float = 0.05) -> bool:
    """Return True if *content* looks like binary data read as text.

    Checks:
      - Null-byte ratio — binary data produces \x00 when decoded with errors=replace
      - ZIP magic header — raw .docx/.pptx read as text starts with PK
    """
    if not content:
        return False
    null_ratio = content.count('\x00') / max(len(content), 1)
    has_zip_header = content[:10].startswith('PK')
    return null_ratio > threshold or has_zip_header


def _extract_document_metadata(filename: str, content: str = "") -> dict:
    """Extract year, category, and doc_type from filename heuristics.

    Returns dict with keys: doc_year (int|None), category (str|None).
    """
    import re as _re_meta
    result: dict = {}

    # Extract year: find 4-digit years like 2024, 2025 in filename
    years = _re_meta.findall(r'(?:20\d{2})(?:\s*[年届级])?', filename)
    if years:
        try:
            result["doc_year"] = int(_re_meta.search(r'\d{4}', years[0]).group())
        except (ValueError, AttributeError):
            logger.debug("Failed to extract year from %r in filename %s", years[0] if years else "", filename)

    # Extract category from bracket tags: 【学籍管理】【培养管理】etc.
    cats = _re_meta.findall(r'【(.+?)】', filename)
    if cats:
        result["category"] = cats[0]  # primary category

    # Detect common doc types
    fn_lower = filename.lower()
    if any(kw in filename for kw in ['表', '申请', '审批']):
        result["doc_type"] = "form"
    elif any(kw in filename for kw in ['通知', '公告']):
        result["doc_type"] = "notice"
    elif any(kw in filename for kw in ['办法', '规定', '细则', '条例']):
        result["doc_type"] = "regulation"
    elif any(kw in filename for kw in ['方案', '计划']):
        result["doc_type"] = "plan"

    return result

