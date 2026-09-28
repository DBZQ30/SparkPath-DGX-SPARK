"""llamaindex/readers.py StructuredExcelReader 测试（168 行，此前零覆盖）。

离线：用 openpyxl 在 tmp_path 现造工作簿（合并单元格 / 表头样式 / 空行），
读回 Document 断言规则单元文本与位置元数据。
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

from knowledge_base.llamaindex.readers import SUPPORTED_EXTS, StructuredExcelReader


def _make_xlsx(tmp_path: Path) -> Path:
    """一行合并标题 + 加粗表头 + 2 行数据 + 1 空行。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "培养方案"
    # 标题区（合并单元格）
    ws.merge_cells("A1:B1")
    ws["A1"] = "2023级工商管理"
    # 表头（加粗触发 _detect_regions）
    for col, name in enumerate(("课程名称", "学分"), start=1):
        cell = ws.cell(row=2, column=col, value=name)
        cell.font = openpyxl.styles.Font(bold=True)
    # 数据区
    ws.append(["管理学原理", 3])
    ws.append(["微观经济学", 4])
    ws.append([None, None])          # 空行应被跳过
    ws.append(["会计学", 2])
    out = tmp_path / "plan.xlsx"
    wb.save(out)
    return out


def test_load_data_produces_row_rule_units(tmp_path):
    docs = StructuredExcelReader().load_data(_make_xlsx(tmp_path))
    # 空行被跳过 → 3 条规则单元
    assert len(docs) == 3
    first = docs[0]
    assert first.text == "课程名称:管理学原理 | 学分:3"
    assert first.metadata["sheet_name"] == "培养方案"
    assert first.metadata["source_file"] == "plan.xlsx"
    assert first.metadata["row_start"] == 3       # 数据从表头下一行开始
    assert first.metadata["content_type"] == "structured_table"
    # 标题区（合并单元格 A1:B1）不进数据流
    assert "2023级工商管理" not in [d.text for d in docs]


def test_load_data_skips_empty_rows(tmp_path):
    docs = StructuredExcelReader().load_data(_make_xlsx(tmp_path))
    texts = [d.text for d in docs]
    # 空行不产 Document，最后一条是第 6 行会计学
    assert texts[-1] == "课程名称:会计学 | 学分:2"
    assert docs[-1].metadata["row_start"] == 6


def test_load_data_respects_max_data_rows(tmp_path):
    docs = StructuredExcelReader(max_data_rows=1).load_data(_make_xlsx(tmp_path))
    assert len(docs) == 1


def test_merged_cell_value_expanded(tmp_path):
    """合并单元格跨列时，规则单元各列都取到合并值。"""
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.merge_cells("A2:C2")
    ws2["A2"] = "同一值"
    merged = StructuredExcelReader._get_merged_ranges(ws2)
    assert merged[(2, 1)] == "同一值" and merged[(2, 3)] == "同一值"
    assert StructuredExcelReader._get_cell_value(ws2, 2, 2, merged) == "同一值"
    assert StructuredExcelReader._get_cell_value(ws2, 5, 1, merged) is None


def test_detect_regions_fallback_to_row1():
    """无加粗/填充时回退：第 1 行当表头，第 2 行起是数据。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["列A"])
    header_row, data_start = StructuredExcelReader._detect_regions(ws, {})
    assert (header_row, data_start) == (1, 2)


def test_supported_exts_maps_known_formats():
    assert SUPPORTED_EXTS[".pdf"] == "PDFReader"
    assert SUPPORTED_EXTS[".docx"] == "DocxReader"
    assert SUPPORTED_EXTS[".txt"] == "default"
    # .doc 仅用于文件名匹配，实际走 antiword（readers 模块注释）
    assert ".doc" in SUPPORTED_EXTS
