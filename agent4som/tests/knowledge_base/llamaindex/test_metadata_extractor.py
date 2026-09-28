"""llamaindex/metadata_extractor.py 元数据归一化测试（123 行，此前零覆盖）。

离线：只用内存 TextNode，不读任何真实文档。
"""

from __future__ import annotations

import pytest
from llama_index.core.schema import TextNode

from knowledge_base.llamaindex.metadata_extractor import RawIndexNodeMetadataExtractor


@pytest.fixture
def extractor() -> RawIndexNodeMetadataExtractor:
    return RawIndexNodeMetadataExtractor(parser_version="test-v1")


def _run(extractor, meta: dict, text: str = "正文内容") -> dict:
    node = TextNode(text=text, metadata=dict(meta))
    extractor.extract([node])
    return node.metadata


def test_pdf_style_metadata_normalised(extractor):
    """PDFReader 风格（page_label）→ RawIndexNode 字段。"""
    meta = _run(extractor, {
        "file_name": "培养方案.pdf",
        "page_label": "3",
        "page_end": 5,
    })
    assert meta["source_file"] == "培养方案.pdf"
    assert meta["page_start"] == 3
    assert meta["page_end"] == 5
    assert meta["parser_version"] == "test-v1"
    # 锚点：正文截断 + 定位串
    assert meta["anchor_text"] == "正文内容"
    assert meta["anchor_locator"] == "页码:3-5"


def test_docx_heading_and_excel_sheet_metadata(extractor):
    meta = _run(extractor, {
        "heading": "第三章 课程设置",
        "sheet_name": "2023级 plan",
        "row_start": 7, "row_end": 9, "col_start": 1, "col_end": 4,
    })
    assert meta["section_title"] == "第三章 课程设置"
    # section_path 默认回落到 section_title
    assert meta["section_path"] == "第三章 课程设置"
    assert meta["sheet_name"] == "2023级 plan"
    assert meta["row_start"] == 7 and meta["row_end"] == 9
    assert meta["col_end"] == 4
    assert "章节:第三章 课程设置" in meta["anchor_locator"]
    assert "Sheet:2023级 plan" in meta["anchor_locator"]
    assert "行7-9" in meta["anchor_locator"]


def test_missing_metadata_fills_defaults(extractor):
    meta = _run(extractor, {})
    assert meta["source_file"] == ""
    assert meta["section_title"] is None
    assert meta["page_start"] is None and meta["page_end"] is None
    # 无任何定位信息 → 兜底锚点
    assert meta["anchor_locator"] == "L1-L1"


def test_to_int_rejects_garbage(extractor):
    assert extractor._to_int("12") == 12
    assert extractor._to_int(None) is None
    assert extractor._to_int("not-a-number") is None
    assert extractor._to_int([1, 2]) is None


def test_anchor_text_truncated_to_200(extractor):
    meta = _run(extractor, {}, text="x" * 500)
    assert len(meta["anchor_text"]) == 200


def test_aextract_delegates_to_extract(extractor):
    import asyncio
    node = TextNode(text="t", metadata={"page_label": "2"})
    out = asyncio.run(extractor.aextract([node]))
    assert out[0].metadata["page_start"] == 2


def test_extract_returns_same_nodes(extractor):
    node = TextNode(text="t", metadata={})
    assert extractor.extract([node]) == [node]
