"""学业预警数据模型（Pydantic v2），与 data/warning.db 的 10 张表一一对应。"""
from typing import Any
from pydantic import BaseModel, Field


class SourceFile(BaseModel):
    """文件档案（source_file 表）——新鲜度判断依据。"""
    id: int | None = None
    file_type: str  # plan/selection/grade/roster/status_change
    file_name: str
    file_hash: str
    file_path: str
    upload_time: str
    uploader: str
    parsed_status: str = "done"  # done/failed
    in_file_meta: dict[str, Any] = Field(default_factory=dict)
    grade: str = ""      # v6：文件所属年级（"2023级"），上传时确定


class TrainingPlan(BaseModel):
    """培养方案版本（training_plan 表），is_active=1 为当前生效版。"""
    id: int | None = None
    major_name: str
    version_label: str = ""
    file_name: str = ""
    upload_time: str = ""
    is_active: int = 0
    entry_year: str = ""  # 预留（M1），本期可空
    created_at: str = ""
    grade: str = ""      # v6：方案所属年级（年级×专业 分区）


class PlanCourse(BaseModel):
    """方案课程总表（plan_course 表，Table 1）——学分唯一来源。"""
    id: int | None = None
    plan_id: int
    course_code: str
    course_name: str
    credit: float
    course_type: str
    required_flag: str  # 必修/选修
    semester: str       # 开课学期，如 "2-2"
    provider: str = ""


class PlanSemesterCourse(BaseModel):
    """分学期推荐课表（plan_semester_course 表，Tables 2-5）——规则 5/6 用。"""
    id: int | None = None
    plan_id: int
    semester: str
    course_code: str
    course_name: str
    credit: float


class Student(BaseModel):
    """检查对象学生（v6.5 名单 = 学籍 roster：在籍在校学生，见 roster 表）。"""
    id: int | None = None
    student_id: str
    name: str = ""
    grade: str = ""         # 如 "2023级"
    major: str = ""         # 如 "工商管理"（roster 标准专业名）
    class_name: str = ""
    enrolled_status: str = "在籍"   # roster 名单已过滤在籍在校，恒"在籍"
    effective_major: str = ""       # 当前专业（无转专业叠加）
    selected_count: float = 0
    selected_credits: float = 0


class Selection(BaseModel):
    """选课结果逐行（selection 表）。"""
    id: int | None = None
    student_id: str
    name: str = ""           # 2026-08-31：选课文件姓名（名单来源）
    major: str = ""          # 2026-08-31：选课文件专业名称（名单专业来源）
    semester_label: str
    semester_code: str
    course_code: str
    course_name: str
    credit: float = 0.0          # 从培养方案 plan_course 关联
    nature: str                  # 必修/选修
    category: str                # 课程类别（映射表键）
    status: str = "选中"         # 仅"选中"参与计算（M4）
    retake: str = "初修"         # 初修/重修
    source_file_id: int = 0
    grade: str = ""      # v6：选课文件主年级


class Grade(BaseModel):
    """成绩单逐条（grade 表）。"""
    id: int | None = None
    student_id: str
    student_name: str = ""   # 姓名（OCR 提取，供名单交叉校正）
    term_label: str = ""
    course_name: str
    course_name_clean: str = ""  # 清洗 ◆▲△◇* 后的课程名
    credit: float = 0.0
    grade_raw: str
    pass_flag: int = 1           # 1 及格 / 0 挂科（§5.0 换算表）
    marker: str = ""             # ▲重修 △补考 ◇核心课 ◆选修课 / 空
    source_file_id: int = 0


class SelectionCheckRow(BaseModel):
    """选课合理性检查结果（selection_check 表，2026-08-31 方向重构）。

    只存触发学生（任一课程类别差额 > 0 学分，或缺修必修课 ≥1 门——
    2026-09-07 D1）；expected/gained/selected/gap 为差额最大类别的数据，
    category 标注该类别名（2026-08-31 展示修复）。"""
    id: int | None = None
    source_file_id: int
    student_id: str
    name: str = ""
    major: str = ""            # 检查时点专业（Prep._major_for，含转专业叠加）
    class_name: str = ""
    category: str = ""         # 触发类别（差额最大类别，如"专业核心课程"）
    expected_credit: float = 0.0   # 触发类别应累计（方案 ≤ 当前学期，剔除全员无成绩）
    gained_credit: float = 0.0     # 触发类别已修（2026-09-07 及格制：同课最高分代表行及格）
    selected_credit: float = 0.0   # 触发类别已选（本轮选课结果）
    gap: float = 0.0               # 触发类别差额
    message: str = ""              # 提醒原因/建议（列出全部不足类别）
    details: str = "{}"            # v7：结构化触发明细 JSON（缺修课含 code+reason + 类别差额逐条）
    checked_at: str = ""
