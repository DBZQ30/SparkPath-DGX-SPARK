"""培养方案 docx 解析测试（真实样本，缺失则跳过）。"""
import os
import pytest

from academicwarning.parsers import parse_plan

PLAN_DIR = "academicwarning/docs"
PLANS = [
    "2023版工商管理专业培养方案.docx",
    "2023版大数据管理与应用专业培养方案.docx",
    "2023版工业工程专业培养方案.docx",
    "2023版会计学（ACCA）专业培养方案.docx",
]


@pytest.mark.parametrize("fname", PLANS)
def test_parse_plan_basic(fname):
    path = os.path.join(PLAN_DIR, fname)
    if not os.path.exists(path):
        pytest.skip(f"样本缺失: {path}")
    meta, courses, sem_courses, _bad_semesters = parse_plan(path)
    assert meta["major"]  # 专业名非空
    assert len(courses) > 50  # 课程总表 60+ 门（工商 83 行含小计）
    assert len(sem_courses) > 0
    # 每门课必有学分、开课学期、必修/选修
    for c in courses:
        assert c.credit > 0
        assert c.semester
        assert c.required_flag in ("必修", "选修")
    # 小计/总计行已剔除
    assert not any("小计" in c.course_name or "总计" in c.course_name for c in courses)
    assert not any("合计" in c.course_name for c in courses)


def test_parse_plan_major_detection():
    path = os.path.join(PLAN_DIR, PLANS[0])
    if not os.path.exists(path):
        pytest.skip("样本缺失")
    meta, _, _, _ = parse_plan(path)
    assert "工商管理" in meta["major"]


def test_plan_course_subname_for_group_courses():
    """课程组名修复：思政组课程名应为子名（学生成绩单记录的课程名），
    而非组名"思想政治理论"（2026-08-27 全员误报根因回归）。"""
    path = os.path.join(PLAN_DIR, PLANS[0])
    if not os.path.exists(path):
        pytest.skip("样本缺失")
    _meta, courses, _, _ = parse_plan(path)
    sz = [c for c in courses if c.course_code.startswith("MLMD")]
    assert sz, "未解析到思政组课程"
    assert all(c.course_name != "思想政治理论" for c in sz)
    assert any("思想道德与法治" in c.course_name for c in sz)


def test_plan_course_subname_all_majors():
    """大数据方案中文课程名称合并 4 列（其余专业 2 列）——全部专业思政课程须为子名。"""
    for fname in PLANS:
        path = os.path.join(PLAN_DIR, fname)
        if not os.path.exists(path):
            pytest.skip("样本缺失")
        meta, courses, _, _ = parse_plan(path)
        sz = [c for c in courses if c.course_code.startswith("MLMD")]
        assert sz, f"{meta['major']} 未解析到思政组课程"
        assert all(c.course_name != "思想政治理论" for c in sz), f"{meta['major']} 存在组名残留"
