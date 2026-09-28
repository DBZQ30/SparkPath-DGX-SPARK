"""Bridge between LlamaIndex nodes and the existing ChromaRepository.

This is NOT a LlamaIndex VectorStore implementation — that would require
duplicating ChromaDB client management.  Instead it's a thin converter:
LlamaIndex ``BaseNode`` → ``RawIndexNode`` → ``ChromaRepository.store_nodes()``.
"""

from __future__ import annotations
from typing import List

import hashlib
import os
import re

from llama_index.core.schema import BaseNode

from knowledge_base.models.schemas import RawIndexNode
from knowledge_base.ingestion.semantic_splitter import _tier_from_scope
from knowledge_base.repository.interfaces import KnowledgeBaseRepository


def _strip_cache_prefix(filename: str) -> str:
    """Remove the WeCom cache prefix ``doc_{uuid12}_`` from *filename*."""
    return re.sub(r"^doc_[a-f0-9]{12}_", "", filename)


def node_to_raw_index_node(llama_node: BaseNode, scope: str, source: str = "") -> RawIndexNode:
    """Convert a LlamaIndex ``BaseNode`` to a ``RawIndexNode`` ready for storage.

    Metadata keys set by ``RawIndexNodeMetadataExtractor`` are read
    directly; missing keys fall back to sensible defaults.
    """
    meta = llama_node.metadata
    raw_text = llama_node.text or ""
    # Contextual prefix: clean filename (strip suffixes and internal IDs)
    doc_name = _strip_cache_prefix(meta.get("source_file", ""))
    import re as _re
    # Remove internal prefixes like "jxtz_10331_" and extensions like ".txt"
    clean_name = _re.sub(r'^.*jxtz_\d+_', '', os.path.basename(doc_name))
    clean_name = _re.sub(r'\.(txt|pdf|docx|doc|pptx|xlsx)$', '', clean_name)
    content = f"[文档: {clean_name}]\n{raw_text}" if clean_name else raw_text
    # Add section path to prefix when available (Contextual Retrieval pattern)
    section_path = meta.get("section_path") or meta.get("section_title")
    if section_path and clean_name:
        content = f"[文档: {clean_name} | {section_path}]\n{raw_text}"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    return RawIndexNode(
        node_id="",  # overwritten by caller (orchestrator._parse_with_llamaindex)
        source_tier=_tier_from_scope(scope),
        source=source,
        source_file=_strip_cache_prefix(meta.get("source_file", "")),
        source_path=meta.get("source_path", scope),
        doc_version=meta.get("doc_version"),
        visibility_tag=meta.get("visibility_tag", "public"),
        content=content,
        section_title=meta.get("section_title"),
        section_path=meta.get("section_path"),
        page_start=meta.get("page_start"),
        page_end=meta.get("page_end"),
        sheet_name=meta.get("sheet_name"),
        row_start=meta.get("row_start"),
        row_end=meta.get("row_end"),
        col_start=meta.get("col_start"),
        col_end=meta.get("col_end"),
        anchor_text=meta.get("anchor_text", content[:200]),
        anchor_locator=meta.get("anchor_locator", "L1-L1"),
        source_hash=content_hash,
        parser_version=meta.get("parser_version", "llamaindex-v1"),
        prev_node_id=None,
        next_node_id=None,
        parse_confidence=1.0,
    )


def store_nodes(
    repo: KnowledgeBaseRepository,
    llama_nodes: List[BaseNode],
    scope: str,
) -> int:
    """Convert and store LlamaIndex nodes via *repo*.

    Returns the number of nodes stored.
    """
    raw_nodes = [node_to_raw_index_node(n, scope) for n in llama_nodes if n.text]
    # Link consecutive nodes so Context Stitcher can find adjacent chunks
    for i, node in enumerate(raw_nodes):
        if i > 0:
            node.prev_node_id = raw_nodes[i - 1].node_id
        if i < len(raw_nodes) - 1:
            node.next_node_id = raw_nodes[i + 1].node_id
    if raw_nodes:
        repo.store_nodes(scope, raw_nodes)
    return len(raw_nodes)
