import hashlib
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, ConfigDict


def make_chunk_id(content_hash: str, chunk_index: int, total_chunks: int, *, scope: str = "") -> str:
    """Deterministic chunk ID for idempotent ChromaDB storage.

    Format: ``chk_{hash_prefix}_{index:04d}``

    *hash_prefix* is the first 16 hex chars of
    ``SHA-256(content_hash + ":" + scope + ":" + str(total_chunks))`` (when
    *scope* is non-empty) or ``SHA-256(content_hash + ":" + str(total_chunks))``
    (when *scope* is empty, backward-compatible).

    Including *scope* in the hash ensures that the same document ingested to
    different scopes produces distinct chunk IDs — without it, ``col.upsert()``
    silently overwrites the first scope's nodes with the last scope's nodes.

    This replaces the previous random-UUID scheme (``node_{uuid4().hex[:12]}``)
    so that ``col.upsert()`` can safely be used.
    """
    structural_key = f"{content_hash}:{total_chunks}" if not scope else f"{content_hash}:{scope}:{total_chunks}"
    hash_prefix = hashlib.sha256(structural_key.encode()).hexdigest()[:16]
    return f"chk_{hash_prefix}_{chunk_index:04d}"

class FactStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"

class BaseFact(BaseModel):
    # Only allow explicitly defined fields (P2-9 fix — extra fields are set
    # via explicit attributes, not arbitrary keyword injection).
    model_config = ConfigDict(extra='forbid')

    fact_id: str
    fact_type: str
    assistant_id: str
    program: str
    cohort_year: str
    payload: dict[str, Any]
    payload_schema_version: str
    fact_key: str
    needs_admin_confirmation: bool = False
    confidence_score: float = 1.0
    source_node_ids: list[str]
    status: FactStatus = FactStatus.APPROVED
    has_user_conflict_warning: bool = False  # set by ConflictResolver when system overrides user

class CourseRequirementPayload(BaseModel):
    course_code: str
    requirement_group: str
    term_scope: str

class CourseRequirementFact(BaseFact):
    fact_type: str = "course_requirement"
    payload: CourseRequirementPayload

    def __init__(self, **data):
        super().__init__(**data)

class RawIndexNode(BaseModel):
    node_id: str
    scope: str = ""           # ACL scope identifier (global/teachers/users/{id})
    source_tier: str
    source: str = ""          # data origin — "file" (本科管理文件库) or "jxtz" (教学通知)
    source_file: str
    source_path: str
    doc_version: str | None = None
    visibility_tag: str = Field(
        default="public",
        deprecated="Not wired into ACL filtering — stored in metadata but "
                   "ignored at query time.  Will be removed in a future "
                   "release unless ACL integration is implemented.",
    )
    content: str
    section_title: str | None = None
    section_path: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    col_start: int | None = None
    col_end: int | None = None
    anchor_text: str
    anchor_locator: str
    source_hash: str
    parser_version: str
    prev_node_id: str | None = None
    next_node_id: str | None = None
    parse_confidence: float = 1.0
    distance: float = 1.0     # Cosine distance from ChromaDB (0=identical, 2=opposite); not persisted

    def to_chroma_metadata(self) -> dict[str, Any]:
        """Serialize non-content fields to Chroma compatible metadata."""
        meta = self.model_dump(exclude={"content", "distance"}, exclude_none=True)
        # Ensure Chroma metadata values are strictly primitive
        for k, v in meta.items():
            if not isinstance(v, (str, int, float, bool)):
                meta[k] = str(v)
        return meta
