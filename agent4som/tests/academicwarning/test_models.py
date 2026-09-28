"""学业预警模型单元测试。"""
from academicwarning.models import (
    PlanCourse, Selection,
)


def test_plan_course_fields():
    c = PlanCourse(
        plan_id=1, course_code="MLMD196614", course_name="马克思主义基本原理",
        credit=3.0, course_type="公共课程", required_flag="必修",
        semester="2-2", provider="马克思主义学院",
    )
    assert c.course_code == "MLMD196614"
    assert c.credit == 3.0
    assert c.required_flag == "必修"


def test_selection_status_default():
    s = Selection(
        student_id="9000000040", semester_label="2026-2027学年 第一学期",
        semester_code="4-1", course_code="MLMD196614", course_name="马克思主义基本原理",
        credit=3.0, nature="必修", category="公共课程", status="选中",
        retake="初修", source_file_id=1,
    )
    assert s.status == "选中"
