"""miniapp 元数据模型的 dataclass 定义（原 sqlite_metadata.py 拆分）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional


def _loads_dict(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}

def _loads_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


@dataclass
class ConversationSession:
    platform: str
    user_id: str
    session_id: Optional[str] = None
    current_stage: str = "new"
    current_topic: Optional[str] = None
    collected_fields_json: str = "{}"
    refused_fields_json: str = "[]"
    pending_confirmation_json: str = "{}"
    attempts_in_round: int = 0
    summary: Optional[str] = None

    @property
    def collected_fields(self) -> Dict[str, Any]:
        return _loads_dict(self.collected_fields_json)

    @property
    def refused_fields(self) -> list[str]:
        return _loads_list(self.refused_fields_json)

    @property
    def pending_confirmation(self) -> Dict[str, Any]:
        return _loads_dict(self.pending_confirmation_json)


@dataclass
class PendingKnowledgeUpload:
    platform: str
    user_id: str
    session_id: Optional[str] = None
    trace_id: str = ""
    message_id: Optional[str] = None
    original_text: str = ""
    file_paths_json: str = "[]"
    requested_scope: Optional[str] = None
    expires_at: str = ""

    @property
    def file_paths(self) -> list[str]:
        return [str(item) for item in _loads_list(self.file_paths_json) if str(item)]


@dataclass
class AdmissionProfile:
    platform: str
    user_id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    company: Optional[str] = None
    position: Optional[str] = None
    project_experience: Optional[str] = None
    undergraduate_school: Optional[str] = None
    undergraduate_major: Optional[str] = None
    highest_degree: Optional[str] = None
    mgmt_knowledge_base: Optional[str] = None
    learning_experience: Optional[str] = None
    student_auth_intent: Optional[str] = None
    source_file: Optional[str] = None
    collection_status: str = "none"
    status: str = "pending"


@dataclass
class LeadContactState:
    platform: str
    user_id: str
    status: str = "active"
    contact_count: int = 0
    last_contact_type: Optional[str] = None
    last_contact_at: Optional[str] = None
    next_followup_at: Optional[str] = None
    opted_out: int = 0


@dataclass
class LeadCandidate:
    platform: str
    user_id: str
    source: str = "manual"
    status: str = "active"
    raw_json: str = "{}"
    discovered_at: Optional[str] = None
    last_seen_at: Optional[str] = None

    @property
    def raw(self) -> Dict[str, Any]:
        return _loads_dict(self.raw_json)


@dataclass
class TeacherAuthRequest:
    id: Optional[int]
    platform: str
    user_id: str
    name: str
    staff_id: str
    status: str = "pending"
    raw_json: str = "{}"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_note: Optional[str] = None
    created_at: Optional[str] = None

    @property
    def raw(self) -> Dict[str, Any]:
        return _loads_dict(self.raw_json)


@dataclass
class AdminAuthRequest:
    id: Optional[int]
    platform: str
    user_id: str
    name: str
    staff_id: str
    reason: str = ""
    status: str = "pending"
    raw_json: str = "{}"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_note: Optional[str] = None
    created_at: Optional[str] = None

    @property
    def raw(self) -> Dict[str, Any]:
        return _loads_dict(self.raw_json)


@dataclass
class StudentAuthRequest:
    id: Optional[int]
    platform: str
    user_id: str
    name: str
    phone: str
    status: str = "pending"
    profile_snapshot_json: str = "{}"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_note: Optional[str] = None

    @property
    def profile_snapshot(self) -> Dict[str, Any]:
        return _loads_dict(self.profile_snapshot_json)


@dataclass
class ManagementReviewContext:
    platform: str
    reviewer_user_id: str
    request_type: str
    request_id: int
    pending_action: Optional[str] = None
    updated_at: Optional[str] = None






@dataclass
class AuditLogEntry:
    id: Optional[int]
    platform: str
    operator_id: str
    operator_role: str
    action: str
    target_type: str
    target_id: Optional[str]
    result: str
    detail_json: str = "{}"
    created_at: Optional[str] = None

    @property
    def detail(self) -> Dict[str, Any]:
        return _loads_dict(self.detail_json)


