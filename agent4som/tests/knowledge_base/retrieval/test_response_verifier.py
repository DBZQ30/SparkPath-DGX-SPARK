"""Tests for response_verifier.verify — now a pass-through.

Permission enforcement for KB content lives in the retrieval layer (L6:
``ACLFilter`` → ChromaDB scope filter). The former output-layer citation
redaction was redundant with that guarantee and fail-closed on every
bare-filename citation, wrongly redacting authorized users. ``verify()``
therefore returns its input unchanged.

See docs/others/014-citation-verification-fix.md (v2.0).
"""
from knowledge_base.retrieval.response_verifier import verify


def _verify(response, **kw):
    kw.setdefault("user_id", "u")
    kw.setdefault("role", "student")
    kw.setdefault("assistant_id", "")
    return verify(response, **kw)


def test_bare_filename_citation_not_redacted():
    """Regression: the bare-filename citation that used to be wrongly redacted
    (owner asking about 保研流程) is now passed through untouched."""
    response = "院内保研流程见[出处: 本科－院内保研流程.doc]。"
    assert _verify(response, role="owner") == response


def test_scoped_citation_passed_through():
    response = "根据[出处: global/培养方案.pdf]可知总学分为45分。"
    assert _verify(response) == response


def test_compound_citation_passed_through():
    response = "参考[出处: 保研流程.doc / 实施细则.pdf]。"
    assert _verify(response) == response


def test_teachers_citation_passed_through_for_student():
    """Formerly redacted. Now intentionally kept: L6 already prevents a student
    from retrieving teachers-scope content, so a real citation here cannot occur,
    and the output layer no longer second-guesses the LLM's text."""
    response = "内部信息请看[出处: teachers/notes.pdf]"
    assert _verify(response, role="student") == response


def test_non_citation_text_unchanged():
    response = "您好，请问有什么可以帮您？"
    assert _verify(response, role="guest") == response


def test_never_mutates_regardless_of_role_or_format():
    """verify() is role- and format-agnostic: it never alters the response."""
    s = "混合[出处: global/a.pdf]、[出处: teachers/b.pdf]、裸名[出处: c.doc]。"
    for role in ("student", "teacher", "admin", "owner", "guest"):
        assert _verify(s, role=role) == s
