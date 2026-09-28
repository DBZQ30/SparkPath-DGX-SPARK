import pytest
from knowledge_base.retrieval.conflict_resolver import ConflictResolver, UnresolvableSystemConflictError
from knowledge_base.models.schemas import BaseFact

def create_mock_fact(tier, fact_key, effective_from=0):
    return BaseFact(
        fact_id=f"{tier}-{fact_key}-{effective_from}",
        fact_type="mock",
        assistant_id="bot",
        program="MBA",
        cohort_year="2026",
        payload={"effective_from": effective_from},
        payload_schema_version="v1",
        fact_key=fact_key,
        source_node_ids=[f"node-{tier}"],
        # In a real app, tier would be inferred from the source node metadata or stored directly.
        # We inject it into payload for mock testing purposes.
        # For this test, let's assume we pass the tier explicitly to the resolver.
    )

def test_conflict_resolver_system_overrides_user():
    resolver = ConflictResolver()
    user_fact = create_mock_fact("user", "course_101")
    system_fact = create_mock_fact("system", "course_101")

    resolved = resolver.resolve([
        {"tier": "user", "fact": user_fact},
        {"tier": "system", "fact": system_fact}
    ])

    assert len(resolved) == 1
    assert resolved[0]["tier"] == "system"
    assert getattr(resolved[0]["fact"], "has_user_conflict_warning", False) is True

def test_conflict_resolver_unresolvable_system_conflict():
    resolver = ConflictResolver()
    system_fact_1 = create_mock_fact("system", "course_101", effective_from=2026)
    system_fact_2 = create_mock_fact("system", "course_101", effective_from=2026) # Same effective time!

    with pytest.raises(UnresolvableSystemConflictError):
        resolver.resolve([
            {"tier": "system", "fact": system_fact_1},
            {"tier": "system", "fact": system_fact_2}
        ])

def test_conflict_resolver_resolvable_system_conflict():
    resolver = ConflictResolver()
    # Fact 2 is newer
    system_fact_old = create_mock_fact("system", "course_101", effective_from=2025)
    system_fact_new = create_mock_fact("system", "course_101", effective_from=2026)

    resolved = resolver.resolve([
        {"tier": "system", "fact": system_fact_old},
        {"tier": "system", "fact": system_fact_new}
    ])

    assert len(resolved) == 1
    assert resolved[0]["fact"].fact_id == "system-course_101-2026"
