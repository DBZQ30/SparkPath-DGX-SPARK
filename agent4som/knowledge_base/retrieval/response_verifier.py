from __future__ import annotations

import logging
import os

from knowledge_base.core.audit_logger import AuditLogger

logger = logging.getLogger(__name__)

_audit: AuditLogger | None = None


def init_audit_logger(db_path: str) -> AuditLogger:
    """Initialize the module-level AuditLogger singleton.

    Called by kb_init hook at gateway startup.  Must be called before
    any tool tries to write audit events.
    """
    global _audit
    _audit = AuditLogger(db_path)
    return _audit


def get_audit_logger() -> AuditLogger | None:
    """Return the module-level AuditLogger, or ``None`` if not initialized."""
    return _audit


def verify(response: str, user_id: str, role: str, assistant_id: str) -> str:
    """Output-layer defence-in-depth for permission enforcement.

    Primary enforcement lives in the retrieval layer (``ACLFilter`` →
    ChromaDB scope filter).  This function provides an optional secondary
    check that can be enabled in production via ``RESPONSE_VERIFY_ENABLED=true``.

    When enabled:
    - Checks that the response does not contain raw scope patterns
      (e.g. ``users/other_user_id``) that would indicate a cross-user leak.
    - Logs a warning and redacts the detected pattern if found.

    When disabled (default): returns *response* unchanged.
    """
    enabled = os.environ.get("RESPONSE_VERIFY_ENABLED", "").lower() == "true"
    if not enabled:
        return response

    import re

    # Detect raw scope patterns that should never appear in LLM output.
    # These indicate the LLM may have been shown content from a scope
    # the user should not see.
    scope_pattern = re.compile(r'\busers/[a-zA-Z0-9_.-]+\b')
    matches = scope_pattern.findall(response)

    if matches:
        unique_scopes = set(matches)
        logger.warning(
            "ResponseVerifier: detected %d potential scope leak(s) in output "
            "for user=%s role=%s: %s",
            len(matches), user_id, role, unique_scopes,
        )
        if _audit:
            _audit.log_event(
                event_type="response_scope_leak",
                user_id=user_id,
                role=role,
                detail={
                    "scopes_found": list(unique_scopes),
                    "assistant_id": assistant_id,
                },
            )
        # Redact scope patterns from the response
        for scope in unique_scopes:
            response = response.replace(scope, "[受限内容]")

    return response
