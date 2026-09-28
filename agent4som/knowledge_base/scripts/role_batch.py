"""Batch role management — CSV import/export for user roles.

Usage:
    from knowledge_base.scripts.role_batch import import_roles_csv, export_roles_csv

CSV format:
    platform,user_id,role
    wecom,zhangsan,admin
    wecom,lisi,teacher
"""

from __future__ import annotations

import csv
import io

from knowledge_base.auth.role_store import (
    VALID_ROLES,
    get_role,
    list_roles,
    set_role,
)


def export_roles_csv(platform: str) -> str:
    """Export all role assignments for *platform* as CSV string."""
    assignments = list_roles(platform)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["platform", "user_id", "role"])
    for user_id, role in sorted(assignments.items()):
        writer.writerow([platform, user_id, role])
    return buf.getvalue()


def import_roles_csv(csv_content: str) -> tuple[int, int, list[str]]:
    """Import role assignments from CSV string.

    Returns:
        (imported, skipped, errors) tuple where errors is a list of
        human-readable error messages for rows that could not be processed.
    """
    imported = 0
    skipped = 0
    errors: list[str] = []

    reader = csv.DictReader(io.StringIO(csv_content))
    for row_num, row in enumerate(reader, start=2):
        platform = row.get("platform", "").strip()
        user_id = row.get("user_id", "").strip()
        role = row.get("role", "").strip().lower()

        if not platform or not user_id or not role:
            errors.append(f"Row {row_num}: missing required field(s)")
            continue

        if role not in VALID_ROLES:
            errors.append(f"Row {row_num}: invalid role {role!r}, must be one of {sorted(VALID_ROLES)}")
            continue

        current = get_role(platform, user_id)
        if current == role:
            skipped += 1
            continue

        if set_role(platform, user_id, role):
            imported += 1
        else:
            errors.append(f"Row {row_num}: failed to set role for {platform}:{user_id}")

    return imported, skipped, errors
