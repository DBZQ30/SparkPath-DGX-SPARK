from __future__ import annotations
from typing import Optional

import contextvars
from dataclasses import dataclass

from knowledge_base.auth.role_store import ROLE_GUEST

# NOTE (D-1): WeCom platform injects QueryContext via inject_context() in
# wecom.py:510-512, so ctx.user_id is typically the sender_id.  For platforms
# that do NOT wire inject_context, QueryContext defaults to empty user_id and
# ROLE_GUEST — tools then resolve user identity from the WeCom cache path or
# gateway context dict as a fallback.  If inject_context() becomes universally
# wired across all platforms, the tools' manual role resolution can be replaced
# with ctx.role / ctx.user_id.


@dataclass
class QueryContext:
    platform: str = ""
    user_id: str = ""
    role: str = ROLE_GUEST
    assistant_id: Optional[str] = None
    session_id: Optional[str] = None


# 默认值用 None 而非共享 QueryContext 实例：可变默认单例会让未注入 context 的
# 所有调用方共享同一对象，任何一处就地修改都会全局泄漏（role-bleed 温床）。
_current_query_context: contextvars.ContextVar[QueryContext | None] = contextvars.ContextVar(
    "current_query_context", default=None
)


def inject_context(ctx: QueryContext) -> None:
    _current_query_context.set(ctx)


def current_context() -> QueryContext:
    return _current_query_context.get() or QueryContext()
