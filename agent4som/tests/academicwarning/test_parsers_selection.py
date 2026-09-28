"""选课结果 xlsx 解析测试。"""
import os
import pytest

from academicwarning.parsers import parse_selection

PATH = "academicwarning/docs/2023级26-27学年第一学期的选课结果.xlsx"

pytestmark = pytest.mark.samples


def test_parse_selection():
    if not os.path.exists(PATH):
        pytest.skip("样本缺失")
    meta, rows, status_note = parse_selection(PATH)
    assert meta["semester_label"] == "2026-2027学年 第一学期"
    assert meta["semester_code"] == "4-1"   # 2023级 → 第四学年第一学期
    assert "2023级" in meta["grades"]
    assert len(rows) > 400
    # 学号/课程号/类别齐全
    for r in rows[:10]:
        assert r["student_id"] and r["course_code"] and r["category"]
    # 状态分布回显（样本全"选中"）
    assert "选中" in status_note
