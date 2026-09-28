"""Unified metadata extractor — maps LlamaIndex document metadata to RawIndexNode fields.

Each LlamaIndex Reader produces format-specific metadata keys (e.g.
``page_label`` from PDFReader, ``heading`` from DocxReader).  This
extractor normalises them into the 22-field RawIndexNode schema so
every node carries consistent provenance information regardless of
source format.
"""

from __future__ import annotations

from typing import Any

from llama_index.core.extractors import BaseExtractor
from llama_index.core.schema import BaseNode


class RawIndexNodeMetadataExtractor(BaseExtractor):
    """Normalise format-native metadata → RawIndexNode fields."""

    def __init__(self, parser_version: str = "llamaindex-v1"):
        super().__init__()
        self._parser_version = parser_version

    async def aextract(self, nodes: list[BaseNode]) -> list[BaseNode]:
        return self.extract(nodes)

    def extract(self, nodes: list[BaseNode]) -> list[BaseNode]:
        for node in nodes:
            meta = node.metadata
            self._normalise(node, meta)
        return nodes

    # ── internal ─────────────────────────────────────────────────────

    def _normalise(self, node: BaseNode, meta: dict[str, Any]) -> None:
        """In-place metadata normalisation."""
        source_file = meta.get("file_name") or meta.get("source_file") or ""

        # ── section / heading ──
        section_title: str | None = (
            meta.get("heading")
            or meta.get("section_title")
            or meta.get("slide_title")
            or meta.get("sheet_name")
        )
        section_path: str | None = (
            meta.get("heading_path") or meta.get("section_path") or section_title
        )

        # ── page ──
        page_start: int | None = self._to_int(
            meta.get("page_label") or meta.get("page") or meta.get("page_start")
        )
        page_end: int | None = self._to_int(
            meta.get("page_end")
        )

        # ── table ──
        sheet_name: str | None = meta.get("sheet_name")
        row_start: int | None = self._to_int(meta.get("row_start"))
        row_end: int | None = self._to_int(meta.get("row_end"))
        col_start: int | None = self._to_int(meta.get("col_start"))
        col_end: int | None = self._to_int(meta.get("col_end"))

        # ── anchor ──
        anchor_text: str = (node.text or "")[:200]
        anchor_locator: str = self._build_locator(
            section_title, page_start, page_end,
            sheet_name, row_start, row_end,
        )

        # ── write back ──
        meta["source_file"] = source_file
        meta["section_title"] = section_title
        meta["section_path"] = section_path
        meta["page_start"] = page_start
        meta["page_end"] = page_end
        meta["sheet_name"] = sheet_name
        meta["row_start"] = row_start
        meta["row_end"] = row_end
        meta["col_start"] = col_start
        meta["col_end"] = col_end
        meta["anchor_text"] = anchor_text
        meta["anchor_locator"] = anchor_locator
        meta["parser_version"] = self._parser_version

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _build_locator(
        section_title: str | None,
        page_start: int | None,
        page_end: int | None,
        sheet_name: str | None,
        row_start: int | None,
        row_end: int | None,
    ) -> str:
        parts: list[str] = []
        if section_title and section_title.strip():
            parts.append(f"章节:{section_title.strip()}")
        if page_start is not None:
            loc = f"页码:{page_start}"
            if page_end and page_end != page_start:
                loc += f"-{page_end}"
            parts.append(loc)
        if sheet_name and sheet_name.strip():
            parts.append(f"Sheet:{sheet_name.strip()}")
        if row_start is not None:
            loc = f"行{row_start}"
            if row_end and row_end != row_start:
                loc += f"-{row_end}"
            parts.append(loc)
        return " | ".join(parts) if parts else "L1-L1"
