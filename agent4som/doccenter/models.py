"""文件中心 —— 常量与数据模型（设计 007 §4）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# 三个消费功能
FEATURE_INTERPRET = "interpret"
FEATURE_PLAN = "plan"
FEATURE_WARNING = "warning"
FEATURES: list[str] = [FEATURE_INTERPRET, FEATURE_PLAN, FEATURE_WARNING]
FEATURE_LABELS = {FEATURE_INTERPRET: "智能解读", FEATURE_PLAN: "学业规划", FEATURE_WARNING: "选课预警"}

# 原件类型
DOC_TYPES: list[str] = [
    "培养方案", "政策", "大纲", "清单", "教学计划", "通识表",
    "转专业政策", "转专业考核安排", "专业选择方案", "操作指引", "其他",
]

# 解析管线
PIPE_PLAN = "plan_structured"     # trainingplan（服务 interpret+plan）
PIPE_WARNING_PLAN = "warning_plan"   # academicwarning（服务 warning）
PIPE_WARNING_GEN_ED = "warning_gen_ed"
PIPE_POLICY = "policy_rules"      # trainingplan 政策规则（转专业/专业选择）
PIPE_TEXT = "text"                # 通用文本（检索/引用）
PIPELINES: list[str] = [PIPE_PLAN, PIPE_WARNING_PLAN, PIPE_WARNING_GEN_ED, PIPE_POLICY, PIPE_TEXT]

# 状态机
STATUS_QUEUED = "queued"
STATUS_PARSING = "parsing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

ALL_GRADES = "全部"

# 文件类型 → 默认适用功能 / 解析管线（管理员可改；设计 007 §4）
DOC_TYPE_DEFAULT: dict[str, dict[str, Any]] = {
    "培养方案":       {"features": FEATURES, "pipelines": [PIPE_PLAN, PIPE_WARNING_PLAN]},
    "政策":           {"features": [FEATURE_INTERPRET, FEATURE_PLAN], "pipelines": [PIPE_TEXT]},
    "转专业政策":     {"features": [FEATURE_PLAN], "pipelines": [PIPE_TEXT, PIPE_POLICY]},
    "转专业考核安排": {"features": [FEATURE_PLAN], "pipelines": [PIPE_TEXT, PIPE_POLICY]},
    "专业选择方案":   {"features": [FEATURE_PLAN], "pipelines": [PIPE_TEXT, PIPE_POLICY]},
    "操作指引":       {"features": [FEATURE_PLAN], "pipelines": [PIPE_TEXT, PIPE_POLICY]},
    "通识表":         {"features": [FEATURE_WARNING, FEATURE_PLAN], "pipelines": [PIPE_WARNING_GEN_ED]},
    "清单":           {"features": [FEATURE_WARNING, FEATURE_PLAN], "pipelines": [PIPE_TEXT]},
    "大纲":           {"features": [FEATURE_INTERPRET], "pipelines": [PIPE_TEXT]},
    "教学计划":       {"features": [FEATURE_INTERPRET, FEATURE_PLAN], "pipelines": [PIPE_TEXT]},
    "其他":           {"features": [], "pipelines": [PIPE_TEXT]},
}

# 功能 → 需要的产物定位键（用于 resolve 时判断该文件是否"已就绪"）
FEATURE_PIPELINE: dict[str, str] = {
    FEATURE_INTERPRET: PIPE_PLAN,
    FEATURE_PLAN: PIPE_PLAN,
    FEATURE_WARNING: PIPE_WARNING_PLAN,
}


@dataclass
class DocFile:
    """原件身份 + 元数据（按 file_hash 唯一）。"""

    file_hash: str
    file_name: str
    file_path: str = ""
    ext: str = ""
    size_bytes: int = 0
    doc_type: str = "其他"
    subject_major: str = ""       # 文件归属专业（空 = 不限）
    subject_grade: str = ""       # 文件主年级（如 "2023级"；空 = 不限）
    title: str = ""
    uploaded_by: str = ""
    uploaded_at: str = ""
    note: str = ""
    id: Optional[int] = None


@dataclass
class Applicability:
    """适用性：功能 × 年级 × 专业（多对多）。"""

    file_id: int
    feature: str
    grade: str = ALL_GRADES
    major: str = ""
    created_at: str = ""
    id: Optional[int] = None


@dataclass
class ParseJob:
    """解析任务（按管线）。"""

    file_id: int
    pipeline: str
    status: str = STATUS_QUEUED
    queue_seq: int = 0
    output_ref: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    error: str = ""
    enqueued_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    id: Optional[int] = None


def default_for(doc_type: str) -> dict[str, Any]:
    return DOC_TYPE_DEFAULT.get(doc_type, DOC_TYPE_DEFAULT["其他"])
