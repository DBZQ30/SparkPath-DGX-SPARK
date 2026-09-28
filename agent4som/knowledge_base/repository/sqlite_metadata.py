"""SQLite persistence for the teaching-affairs assistant miniapp state.

拆分后的实现见 metadata_db / metadata_models / metadata_dao_* 同目录模块；
本模块保留为兼容门面（re-export），历史 import 路径不变。
"""

from __future__ import annotations

from knowledge_base.repository.metadata_db import (  # noqa: F401
    DEFAULT_DB_NAME, DatabaseManager, resolve_sqlite_db_path,
)
from knowledge_base.repository.metadata_models import (  # noqa: F401
    ConversationSession, PendingKnowledgeUpload, AdmissionProfile, LeadContactState, LeadCandidate, TeacherAuthRequest, AdminAuthRequest, StudentAuthRequest, ManagementReviewContext, AuditLogEntry,
)
from knowledge_base.repository.metadata_dao_lead import (  # noqa: F401
    WelcomeSentLogDAO, LeadContactStateDAO, LeadCandidateDAO,
)
from knowledge_base.repository.metadata_dao_auth import (  # noqa: F401
    TeacherAuthRequestDAO, AdminAuthRequestDAO, StudentAuthRequestDAO,
)
from knowledge_base.repository.metadata_dao_misc import (  # noqa: F401
    ManagementReviewContextDAO, AuditLogDAO, ConversationSessionDAO,
    PendingKnowledgeUploadDAO, AdmissionProfileDAO,
)
