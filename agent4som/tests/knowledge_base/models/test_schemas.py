import pytest
from pydantic import ValidationError
from knowledge_base.models.schemas import FactStatus, CourseRequirementFact, RawIndexNode

def test_fact_schema_validation_success():
    """Test successful instantiation of CourseRequirementFact with valid payload."""
    payload = {
        "course_code": "CS101",
        "requirement_group": "core",
        "term_scope": "fall"
    }
    fact = CourseRequirementFact(
        fact_id="fact-001",
        assistant_id="mba-assistant",
        program="MBA",
        cohort_year="2026",
        payload=payload,
        payload_schema_version="v1.0",
        fact_key="mba-assistant:MBA:2026:course_requirement:CS101",
        status=FactStatus.APPROVED,
        source_node_ids=["node-001"]
    )
    # The payload is parsed into a Pydantic model by inheritance
    assert fact.payload.course_code == "CS101"
    assert fact.status == FactStatus.APPROVED
    assert fact.needs_admin_confirmation is False

def test_fact_schema_validation_fallback_missing_payload_fields():
    """Test validation fails when payload misses required fields (e.g., course_code)."""
    payload = {
        "requirement_group": "core"
        # Missing course_code
    }
    with pytest.raises(ValidationError) as exc_info:
        CourseRequirementFact(
            fact_id="fact-002",
            assistant_id="mba-assistant",
            program="MBA",
            cohort_year="2026",
            payload=payload,
            payload_schema_version="v1.0",
            fact_key="mba-assistant:MBA:2026:course_requirement:UNKNOWN",
            source_node_ids=["node-002"]
        )
    assert "course_code" in str(exc_info.value)

def test_raw_node_metadata_serialization():
    """Test that RawIndexNode can correctly output metadata for ChromaDB."""
    node = RawIndexNode(
        node_id="node-003",
        source_tier="global",
        source_file="syllabus.pdf",
        source_path="docs/global/syllabus.pdf",
        doc_version="v2",
        content="Must complete 30 credits.",
        section_title="Graduation Requirements",
        page_start=12,
        anchor_text="See page 12",
        anchor_locator="p12",
        source_hash="abcd123",
        parser_version="v1.5",
        parse_confidence=0.98
    )
    meta = node.to_chroma_metadata()
    # Chroma metadata values must be str, int, float or bool.
    assert meta["source_tier"] == "global"
    assert meta["page_start"] == 12
    assert meta["parse_confidence"] == 0.98
    assert "content" not in meta  # Content goes to Document, not metadata

def test_raw_node_default_visibility_tag():
    """RawIndexNode defaults to visibility_tag='public'."""
    node = RawIndexNode(
        node_id="n1", source_tier="u", source_file="f.txt",
        source_path="/f.txt", content="x", anchor_text="x",
        anchor_locator="L1", source_hash="h", parser_version="v1",
    )
    assert node.visibility_tag == "public"

def test_raw_node_custom_visibility_tag():
    """Visibility tag is serialized to Chroma metadata."""
    node = RawIndexNode(
        node_id="n2", source_tier="u", source_file="f.txt",
        source_path="/f.txt", content="x", anchor_text="x",
        anchor_locator="L1", source_hash="h", parser_version="v1",
        visibility_tag="internal",
    )
    meta = node.to_chroma_metadata()
    assert meta["visibility_tag"] == "internal"
