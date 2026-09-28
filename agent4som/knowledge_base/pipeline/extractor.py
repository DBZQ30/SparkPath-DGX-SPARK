from typing import Any
from pydantic import ValidationError
from knowledge_base.models.schemas import CourseRequirementFact, BaseFact, FactStatus

def process_extraction(
    fact_id: str,
    assistant_id: str,
    program: str,
    cohort_year: str,
    payload: dict[str, Any],
    fact_key: str,
    source_node_ids: list[str]
) -> BaseFact:
    """
    Attempt to construct a strict schema fact.
    If it fails due to ValidationError, fallback to a BaseFact
    with PENDING_REVIEW status and needs_admin_confirmation = True.
    """
    try:
        return CourseRequirementFact(
            fact_id=fact_id,
            assistant_id=assistant_id,
            program=program,
            cohort_year=cohort_year,
            payload=payload,
            payload_schema_version="v1.0",
            fact_key=fact_key,
            source_node_ids=source_node_ids,
            status=FactStatus.APPROVED
        )
    except ValidationError:
        # Downgrade to generic BaseFact in pending state
        return BaseFact(
            fact_id=fact_id,
            fact_type="unknown_or_invalid",
            assistant_id=assistant_id,
            program=program,
            cohort_year=cohort_year,
            payload=payload,
            payload_schema_version="fallback_v1",
            fact_key=fact_key,
            source_node_ids=source_node_ids,
            status=FactStatus.PENDING_REVIEW,
            needs_admin_confirmation=True
        )
