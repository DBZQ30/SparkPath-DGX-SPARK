from knowledge_base.pipeline.router import ExtractorRouter, FileType
from knowledge_base.pipeline.extractor import process_extraction
from knowledge_base.models.schemas import FactStatus

def test_pipeline_routes_xlsx_to_structured_parser():
    router = ExtractorRouter()
    parser_type = router.route("timetable.xlsx")
    assert parser_type == FileType.STRUCTURED_TABLE

def test_pipeline_routes_pdf_to_document_parser():
    router = ExtractorRouter()
    parser_type = router.route("guide.pdf")
    assert parser_type == FileType.DOCUMENT

def test_schema_failure_downgrades_to_pending_review():
    # Mock LLM returning bad JSON (missing course_code)
    bad_payload = {"requirement_group": "core", "term_scope": "spring"}

    # Process extraction should catch the ValidationError and downgrade it
    fact = process_extraction(
        fact_id="fact-1",
        assistant_id="mba",
        program="MBA",
        cohort_year="2026",
        payload=bad_payload,
        fact_key="mba:MBA:2026:course_requirement:UNKNOWN",
        source_node_ids=["node-1"]
    )

    assert fact.status == FactStatus.PENDING_REVIEW
    assert fact.needs_admin_confirmation is True
    # The bad payload should still be retained for admin review!
    assert fact.payload == bad_payload
