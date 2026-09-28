"""Tests for QueryContext and contextvars-based context injection."""

from knowledge_base.auth.role_store import ROLE_GUEST, ROLE_ADMIN
from knowledge_base.core.query_context import QueryContext, inject_context, current_context


def test_default_context_has_guest_role():
    """Default context should have the most restrictive role."""
    ctx = current_context()
    assert ctx.role == ROLE_GUEST
    assert ctx.user_id == ""
    assert ctx.platform == ""


def test_inject_context_overrides_default():
    """Injected context is readable via current_context()."""
    ctx = QueryContext(platform="wecom", user_id="zhangsan", role=ROLE_ADMIN, assistant_id="mba-admission")
    inject_context(ctx)
    retrieved = current_context()
    assert retrieved.platform == "wecom"
    assert retrieved.user_id == "zhangsan"
    assert retrieved.role == ROLE_ADMIN
    assert retrieved.assistant_id == "mba-admission"


def test_context_isolation():
    """Each contextvar call site gets its own copy."""
    ctx_a = QueryContext(user_id="user_a", role="teacher")
    ctx_b = QueryContext(user_id="user_b", role="student")

    inject_context(ctx_a)
    assert current_context().user_id == "user_a"

    inject_context(ctx_b)
    assert current_context().user_id == "user_b"


def test_query_context_defaults():
    """QueryContext dataclass fields have sensible defaults."""
    ctx = QueryContext()
    assert ctx.platform == ""
    assert ctx.user_id == ""
    assert ctx.role == ROLE_GUEST
    assert ctx.assistant_id is None
    assert ctx.session_id is None
