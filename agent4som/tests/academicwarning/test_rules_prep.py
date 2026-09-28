"""规则前置推导测试（构造数据，不依赖文件）。"""

from academicwarning.models import Student, Selection, Grade, PlanCourse
from academicwarning.rules import Prep


def _students():
    return [Student(student_id="S1", name="甲", grade="2023级",
                    major="0824工商管理", effective_major="")]


def test_gained_and_selected_credits():
    grades = [
        Grade(student_id="S1", course_name="高等数学I-2",
              course_name_clean="高等数学I-2", credit=6.5,
              grade_raw="80", pass_flag=1),
        Grade(student_id="S1", course_name="大学物理III",
              course_name_clean="大学物理III", credit=4.0,
              grade_raw="42", pass_flag=0),   # 挂科不计已获
    ]
    sels = [Selection(student_id="S1", semester_label="2026-2027学年 第一学期",
                      semester_code="4-1", course_code="MAGT420808",
                      course_name="企业战略管理", credit=2.0, nature="必修",
                      category="专业核心课程", status="选中", retake="初修")]
    courses = [
        PlanCourse(plan_id=1, course_code="MATH298207", course_name="高等数学I-2",
                   credit=6.5, course_type="数学和基础科学类课程",
                   required_flag="必修", semester="1-1"),
        PlanCourse(plan_id=1, course_code="PHYS111111", course_name="大学物理III",
                   credit=4.0, course_type="数学和基础科学类课程",
                   required_flag="必修", semester="2-1"),
    ]
    prep = Prep(_students(), sels, grades, {"工商管理": courses}, {}, [], "4-1")
    gained = prep.gained_credits_by_category("S1")
    assert gained["数学和基础科学类课程"] == 6.5   # 挂科的 4.0 不计
    selected = prep.selected_credits_by_category("S1")
    assert selected["专业核心课程"] == 2.0
    assert "大学物理III" not in prep.passed_courses("S1")
    assert "大学物理III" in prep.failed_courses("S1")
