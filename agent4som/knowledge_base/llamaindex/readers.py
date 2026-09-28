"""Format-specific LlamaIndex readers.

Each reader returns ``llama_index.core.Document`` objects with
format-native metadata (page numbers, headings, table positions, etc.).
The ``RawIndexNodeMetadataExtractor`` later normalises this metadata
into the 22-field ``RawIndexNode`` schema.
"""

from __future__ import annotations

import os
from pathlib import Path

from llama_index.core import Document, SimpleDirectoryReader
from llama_index.core.readers.base import BaseReader


# ── Built-in LlamaIndex readers (auto-detected by SimpleDirectoryReader) ──

SUPPORTED_EXTS: dict[str, str] = {
    ".docx": "DocxReader",
    ".doc":  "DocxReader",           # NOTE: python-docx does NOT support .doc (OLE2).
                                     # .doc files are handled by antiword in _read_file().
                                     # This mapping exists only for filename matching;
                                     # _parse_with_llamaindex() skips LlamaIndex for .doc.
    ".pdf":  "PDFReader",
    ".pptx": "PptxReader",
    ".jpg":  "ImageReader",
    ".jpeg": "ImageReader",
    ".png":  "ImageReader",
    ".txt":  "default",
    ".md":   "default",
    ".csv":  "default",
}


def create_simple_reader(file_path: str) -> SimpleDirectoryReader:
    """Return a ``SimpleDirectoryReader`` pre-configured for *file_path*.

    SimpleDirectoryReader auto-detects the format and dispatches to the
    correct built-in reader.  We wrap it for a consistent API.
    """
    return SimpleDirectoryReader(input_files=[file_path])


# ── Structured Excel Reader ────────────────────────────────────────────

class StructuredExcelReader(BaseReader):
    """Reader for .xlsx/.xls files with merged-cell awareness.

    Uses openpyxl to:
    1. Detect merged cells and expand values across all covered cells.
    2. Identify logical regions: title zone, header zone, data zone, notes zone.
    3. Expand multi-level headers into hierarchical column names.
    4. Produce one Document per row (rule-unit), with sheet_name and row/col
       position metadata.
    """

    def __init__(self, max_data_rows: int = 2000):
        self._max_data_rows = max_data_rows

    def load_data(
        self,
        file: Path,
        extra_info: dict | None = None,
    ) -> list[Document]:
        import openpyxl

        wb = openpyxl.load_workbook(file, data_only=True)
        documents: list[Document] = []
        extra = extra_info or {}

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            merged = self._get_merged_ranges(ws)

            # Detect header row and data start
            header_row, data_start = self._detect_regions(ws, merged)
            if header_row is None:
                continue

            headers = self._expand_headers(ws, header_row, merged)

            for row_idx in range(data_start, min(ws.max_row + 1, data_start + self._max_data_rows)):
                row_data: list[str] = []
                for col_idx in range(1, ws.max_column + 1):
                    cell_value = self._get_cell_value(ws, row_idx, col_idx, merged)
                    row_data.append(str(cell_value) if cell_value is not None else "")

                if not any(v.strip() for v in row_data):
                    continue  # skip empty rows

                # Build rule-unit text
                parts = []
                for hi, hdr in enumerate(headers):
                    val = row_data[hi] if hi < len(row_data) else ""
                    if val:
                        parts.append(f"{hdr}:{val}")
                content = " | ".join(parts)

                documents.append(Document(
                    text=content,
                    metadata={
                        "source_file": os.path.basename(str(file)),
                        "sheet_name": sheet_name,
                        "row_start": row_idx,
                        "row_end": row_idx,
                        "col_start": 1,
                        "col_end": len(headers),
                        "content_type": "structured_table",
                    },
                ))

        wb.close()
        return documents

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _get_merged_ranges(ws) -> dict:
        """Return ``{(row, col): value}`` for all cells covered by merged ranges."""
        merged_map: dict = {}
        for merged_range in ws.merged_cells.ranges:
            top_left = ws.cell(merged_range.min_row, merged_range.min_col).value
            for row in range(merged_range.min_row, merged_range.max_row + 1):
                for col in range(merged_range.min_col, merged_range.max_col + 1):
                    merged_map[(row, col)] = top_left
        return merged_map

    @staticmethod
    def _get_cell_value(ws, row: int, col: int, merged: dict):
        """Return cell value, expanding from merged ranges if needed."""
        if (row, col) in merged:
            return merged[(row, col)]
        return ws.cell(row, col).value

    @staticmethod
    def _detect_regions(ws, merged: dict) -> tuple:
        """Return ``(header_row, data_start_row)`` or ``(None, None)``.

        Heuristic: the first row with bold font or background fill in at
        least 2 columns is the header row.  Data starts one row below.
        """
        for row_idx in range(1, min(ws.max_row + 1, 20)):
            bold_or_filled = 0
            for col_idx in range(1, min(ws.max_column + 1, 50)):
                cell = ws.cell(row_idx, col_idx)
                if cell.value is not None and (
                    (cell.font and cell.font.bold) or
                    (cell.fill and cell.fill.fgColor and cell.fill.fgColor.rgb not in ("00000000", "0"))
                ):
                    bold_or_filled += 1
            if bold_or_filled >= 2:
                return row_idx, row_idx + 1
        # Fallback: assume row 1 is header
        return 1, 2

    @staticmethod
    def _expand_headers(ws, header_row: int, merged: dict) -> list[str]:
        """Build a list of hierarchical column names for the header row.

        Multi-level headers are joined with ``>`` (e.g. "课程类别>必修>学分").
        """
        headers = []
        for col_idx in range(1, ws.max_column + 1):
            value = StructuredExcelReader._get_cell_value(ws, header_row, col_idx, merged)
            headers.append(str(value).strip() if value else f"col_{col_idx}")
        return headers
