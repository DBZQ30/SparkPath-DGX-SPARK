"""培养方案智能解读 —— 数据模型与常量。

与 academic-warning 完全独立：本包自建、自用 `data/training_plan.db`，
不回写/同步 `warning.db`（设计 §5）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# 4 个标准专业（与培养方案正文名一致）
STANDARD_MAJORS: list[str] = [
    "工商管理",
    "工业工程",
    "会计学（ACCA）",
    "大数据管理与应用",
]

# 专业名归一化（含常见简称/别名）
MAJOR_ALIASES: dict[str, str] = {
    "工商管理": "工商管理",
    "工业工程": "工业工程",
    "会计学（ACCA）": "会计学（ACCA）",
    "会计学(ACCA)": "会计学（ACCA）",
    "会计学": "会计学（ACCA）",
    "ACCA": "会计学（ACCA）",
    "大数据管理与应用": "大数据管理与应用",
    "大数据": "大数据管理与应用",
}

# parsed_status 状态机
STATUS_QUEUED = "queued"      # 已入队，等待 worker
STATUS_PARSING = "parsing"    # 正在解析
STATUS_DONE = "done"          # 解析完成
STATUS_FAILED = "failed"      # 解析失败（可重试）
STATUS_REJECTED = "rejected"  # 校验拒绝，不入队

VALID_STATUSES = {STATUS_QUEUED, STATUS_PARSING, STATUS_DONE, STATUS_FAILED, STATUS_REJECTED}

# 队列中"活跃"（占用位置）的状态
ACTIVE_QUEUE_STATUSES = {STATUS_QUEUED, STATUS_PARSING}


@dataclass
class PlanDocument:
    """培养方案版本档案（一条 = 某专业某次上传）。"""

    major: str
    entry_year: str                     # 主年级（从文件名解析），如 "2023级"
    version_label: str = ""
    file_name: str = ""
    file_hash: str = ""
    file_path: str = ""
    upload_time: str = ""
    uploader: str = ""
    parsed_status: str = STATUS_QUEUED
    queue_seq: int = 0
    enqueued_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    is_active: int = 1
    in_file_meta: dict[str, Any] = field(default_factory=dict)
    # 适用年级：["2023级"] / ["2023级","2024级"] / ["全部"]
    applies_to: list[str] = field(default_factory=list)
    id: Optional[int] = None

    @property
    def note(self) -> str:
        return str(self.in_file_meta.get("note", ""))

    @property
    def error(self) -> str:
        return str(self.in_file_meta.get("error", ""))


@dataclass
class CreditNode:
    """学分结构树节点（Table 0）。"""

    plan_id: int
    parent_path: str
    path: str
    name: str
    credit: str = ""          # 原文形态，如 "126" / "148+8" / "（8）"
    credit_note: str = ""     # 备注（路径替代/双创等）
    category: str = ""        # 顶层归类：课程教学 / 集中实践 / 课外实践 / 毕业要求
    sort: int = 0


@dataclass
class PrereqEdge:
    """先修关系边：from 是先修课，to 是后续课。"""

    plan_id: int
    from_course_name: str
    to_course_name: str
    from_course_code: str = ""
    to_course_code: str = ""
    source: str = "vl"            # vl / manual
    confidence: float = 0.0
    verified: int = 0             # 0 未校对 / 1 已校对
    verified_by: str = ""
    verified_at: str = ""
    note: str = ""


@dataclass
class GraduationReq:
    """毕业/授学位硬条件。"""

    plan_id: int
    req_type: str                 # 学分 / 学位 / 其他
    item: str
    value: str = ""
    unit: str = ""
    detail: str = ""
    sort: int = 0


@dataclass
class StudentBinding:
    """openid ↔ 学号 绑定（本人绑定，学号 + 姓名 经 `warning.db.roster` 校验）。

    设计 §6.7：只存绑定关系，**不存成绩**；未绑定时上游只提供方案级结论。
    """

    platform: str
    user_id: str
    student_id: str
    name: str = ""
    grade: str = ""
    major: str = ""
    status: str = "active"
    verified_at: str = ""
    created_at: str = ""
    id: Optional[int] = None


# ────────────────────── 培养模式（四路径规则，设计 005 §4） ──────────────────────

MODE_REGULAR = "常规型"
MODE_SCIENCE = "科学研究型"
MODE_CROSS = "交叉融合型"
MODE_INNOVATION = "创新创业型"
MODES: list[str] = [MODE_REGULAR, MODE_SCIENCE, MODE_CROSS, MODE_INNOVATION]
MODE_COMMON = "通用"          # 不分模式的通用规则（如学期学分上限）


@dataclass
class ModeRule:
    """培养模式规则（学分替代 / 跨选范围 / 成果替代 / 申请节点 / 学期学分上限）。"""

    plan_id: int
    mode: str                     # 常规型/科学研究型/交叉融合型/创新创业型/通用
    rule_type: str                # 学分替代/跨选范围/成果替代/申请节点/学期学分上限
    item: str = ""
    value: str = ""
    unit: str = ""
    detail: str = ""
    sort: int = 0


@dataclass
class ModeCourse:
    """培养模式专属课程清单（如科学研究型-研究生进阶课程）。"""

    plan_id: int
    mode: str
    group: str = ""               # 研究生进阶 / 跨选课程
    course_code: str = ""
    course_name: str = ""
    credit: float = 0.0
    provider: str = ""
    note: str = ""


@dataclass
class ModeScope:
    """交叉融合型-跨选专业范围。"""

    plan_id: int
    mode: str = ""
    allowed_major: str = ""
    note: str = ""
