#!/usr/bin/env python3
"""Admin CLI for SOM knowledge base management.

Usage:
    python scripts/admin_cli.py

Provides menu-driven access to:
  - Ingest a file into the knowledge base
  - Sync knowledge base from directories
  - List, set, and batch-import user roles
  - Purge user data
  - View audit log
  - Check knowledge base health
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path



# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# MUST load .env BEFORE importing knowledge_base modules — class-level
# attributes like _MINERU_URL are evaluated at import time.
from knowledge_base.bootstrap import load_dotenv
load_dotenv()

# Wire role-store audit logger so set_role/unset_role write structured events
try:
    from knowledge_base.core.audit_logger import AuditLogger
    from knowledge_base.auth.role_store import init_role_audit
    init_role_audit(AuditLogger("data/audit.db"))
except Exception as exc:
    # audit.db may not exist yet; role_store falls back to logger
    print(f"[warn] 角色审计日志初始化跳过：{type(exc).__name__}: {exc}", file=sys.stderr)

from knowledge_base.bootstrap import (
    create_chroma_repository,
    create_ingestion_orchestrator,
)


def _resolve_and_check_write(user_id: str, scope: str | None) -> str | None:
    """Resolve the effective scope for *user_id* and enforce write ACL.

    Mirrors the tool-layer gate (knowledge_ingest) so the CLI cannot
    silently bypass permission checks.  Returns the effective scope
    (resolved default) or None if permission was denied (error printed).
    """
    from knowledge_base.auth.role_store import resolve_role
    from knowledge_base.retrieval.acl_filter import (
        check_write_permission,
        derive_default_scope,
        UnauthorizedAccessError,
    )

    role = resolve_role("wecom", user_id)
    effective_scope = scope or derive_default_scope(role, user_id)
    if not scope:
        print(f"  (角色 {role}，默认 scope: {effective_scope})")
    try:
        check_write_permission(role, effective_scope, user_id)
        return effective_scope
    except UnauthorizedAccessError as exc:
        print(f"  ❌ 权限不足: {exc}")
        return None


def cmd_ingest(args):
    user_id = args.user_id or "admin"
    effective_scope = _resolve_and_check_write(user_id, args.scope)
    if effective_scope is None:
        return
    orch = create_ingestion_orchestrator()
    result = orch.ingest_file(user_id, args.file, scope=effective_scope, source="file")
    print(f"[{result.status.value}] {result.notification}")


def cmd_sync(args):
    user_id = args.user_id or "admin"
    effective_scope = _resolve_and_check_write(user_id, args.scope or "global")
    if effective_scope is None:
        return
    orch = create_ingestion_orchestrator()
    from knowledge_base.scripts.sync_kb import SyncKbRunner

    runner = SyncKbRunner(orch, kb_root=args.kb_root or ".")
    result = runner.run(admin_user_id=user_id)
    print(f"Scanned: {result.files_scanned}, Nodes: {result.parsed_nodes}, Failed: {len(result.failed_files)}")


def cmd_roles_list(args):
    from knowledge_base.auth.role_store import list_roles
    roles = list_roles(args.platform)
    for uid, role in sorted(roles.items()):
        print(f"  {uid:20s} → {role}")


def cmd_roles_set(args):
    # 允许修改角色的本地用户：环境变量 AGENT4SOM_ADMIN_USERS（逗号/空格分隔）或 HERMES_OWNER。
    # 用真实 UID（不信任可被任意设置的 USER 环境变量）；未配置白名单时回退为「当前用户」
    # （单机/开发机场景），root 始终允许。
    import os as _os
    _is_root = _os.getuid() == 0
    _allowed = {u for u in _os.environ.get("AGENT4SOM_ADMIN_USERS", "").replace(",", " ").split() if u}
    _owner = _os.environ.get("HERMES_OWNER", "").strip()
    if _owner:
        _allowed.add(_owner)
    try:
        import pwd as _pwd
        _current = _pwd.getpwuid(_os.getuid()).pw_name
    except (ImportError, KeyError):
        _current = ""
    _is_admin = _is_root or (not _allowed) or (_current in _allowed)
    if not _is_admin:
        print("  权限不足：仅允许 root 或白名单用户（AGENT4SOM_ADMIN_USERS / HERMES_OWNER）修改角色")
        return
    from knowledge_base.auth.role_store import set_role
    if set_role(args.platform, args.user_id, args.role):
        print(f"  {args.user_id} → {args.role}")
        # Role change is audited inside set_role() via init_role_audit()
    else:
        print(f"  Invalid role: {args.role}")


def cmd_roles_import(args):
    from knowledge_base.scripts.role_batch import import_roles_csv
    csv_content = sys.stdin.read(10 * 1024 * 1024) if args.file == "-" else Path(args.file).read_text()
    imported, skipped, errors = import_roles_csv(csv_content)
    print(f"Imported: {imported}, Skipped: {skipped}, Errors: {len(errors)}")
    for e in errors:
        print(f"  ERROR: {e}")


def cmd_purge(args):
    repo = create_chroma_repository()
    from knowledge_base.scripts.purge_pipeline import PurgeUserKBPipeline
    pipeline = PurgeUserKBPipeline(repo)
    summary = pipeline.run(args.user_id, dry_run=args.dry_run)
    vectors = summary.get('vectors_purged', 0)
    print(f"Purge user={args.user_id} dry_run={args.dry_run}: vectors={vectors}")

    # Audit the operation (CLI tools create their own instance — no singleton).
    if not args.dry_run:
        from knowledge_base.core.audit_logger import AuditLogger
        audit = AuditLogger("data/audit.db")
        audit.log_event(
            event_type="purge",
            user_id=args.user_id,
            role="admin",
            detail={"vectors_purged": vectors},
        )


def cmd_audit(args):
    from knowledge_base.core.audit_logger import AuditLogger
    alog = AuditLogger("data/audit.db")

    if args.verify:
        total, ok = alog.verify_chain()
        print(f"Chain hash verification: {ok}/{total} passed", end="")
        if total == 0:
            print(" (no hashed rows yet)")
        elif total == ok:
            print(" ✓")
        else:
            print(f" ⚠  {total - ok} rows tampered!")
        return

    if args.stats:
        s = alog.stats()
        print(f"Total events: {s['total']}")
        print("By type:")
        for t, c in sorted(s['by_type'].items()):
            print(f"  {t}: {c}")
        print("Top users:")
        for u, c in s['top_users'].items():
            print(f"  {u}: {c}")
        return

    rows = alog.query(limit=args.limit, event_type=args.event_type, user_id=args.user_id)
    if args.csv:
        import csv
        import sys
        w = csv.writer(sys.stdout)
        w.writerow(["id", "timestamp", "event_type", "user_id", "role", "query", "filename", "scope", "node_count", "latency_ms"])
        for r in rows:
            w.writerow([r.get(k, "") for k in ["id", "timestamp", "event_type", "user_id", "role", "query", "filename", "scope", "node_count", "latency_ms"]])
        return

    for r in rows:
        extra = ""
        if r.get('query'):
            extra += f" query={r['query'][:60]}"
        if r.get('filename'):
            extra += f" file={r['filename']}"
        if r.get('scope'):
            extra += f" scope={r['scope']}"
        if r.get('latency_ms'):
            extra += f" {r['latency_ms']}ms"
        print(f"  [{r['timestamp']}] {r['event_type']:20s} user={r['user_id']:15s} role={r['role']:10s}{extra}")


def cmd_health(args):
    repo = create_chroma_repository()
    from knowledge_base.repository.chroma_repository import ChromaRepository, get_chroma_client

    print(f"ChromaDB singleton ready: {ChromaRepository.is_ready()}")

    # Verify actual ChromaDB connectivity via heartbeat
    chroma_host = os.getenv("CHROMA_HOST", "127.0.0.1")
    chroma_port = os.getenv("CHROMA_PORT", "8007")
    heartbeat_url = f"http://{chroma_host}:{chroma_port}/api/v2/heartbeat"
    try:
        import requests as _requests
        r = _requests.get(heartbeat_url, timeout=5)
        if r.status_code == 200:
            print(f"ChromaDB heartbeat: OK ({heartbeat_url})")
        else:
            print(f"ChromaDB heartbeat: FAIL (HTTP {r.status_code})")
    except Exception as e:
        print(f"ChromaDB heartbeat: FAIL ({e})")

    try:
        client = get_chroma_client(os.getenv("CHROMA_DB_PATH", "data/chroma"))
        collections = client.list_collections()
        print(f"Collections ({len(collections)}):")
        for c in collections:
            try:
                print(f"  - {c.name} ({c.count()} items)")
            except Exception:
                print(f"  - {c.name}")
    except Exception as e:
        print(f"Error: {e}")


def _dispatch_roles(args):
    """Dispatch roles subcommand to the correct handler based on action."""
    if args.action == "list":
        return cmd_roles_list(args)
    if args.action == "set":
        return cmd_roles_set(args)
    if args.action == "import":
        return cmd_roles_import(args)
    print(f"Unknown roles action: {args.action}")
    return cmd_roles_list(args)


def main():
    parser = argparse.ArgumentParser(description="SOM Knowledge Base Admin CLI")
    parser.set_defaults(func=lambda _: parser.print_help())

    sub = parser.add_subparsers(title="commands")

    p = sub.add_parser("ingest", help="Ingest a file")
    p.add_argument("file")
    p.add_argument("--scope", default=None)
    p.add_argument("--user-id", default="admin")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("sync", help="Sync knowledge base directories")
    p.add_argument("--kb-root", default=None)
    p.add_argument("--user-id", default="admin")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("roles", help="Manage roles")
    p.add_argument("action", choices=["list", "set", "import"])
    p.add_argument("--platform", default="wecom")
    p.add_argument("--user-id")
    p.add_argument("--role")
    p.add_argument("--file", help="CSV file for import (use '-' for stdin)")
    p.set_defaults(func=_dispatch_roles)

    p = sub.add_parser("purge", help="Purge user data")
    p.add_argument("user_id")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_purge)

    p = sub.add_parser("audit", help="View audit log")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--event-type")
    p.add_argument("--user-id")
    p.add_argument("--verify", action="store_true", help="Verify chain hash integrity")
    p.add_argument("--stats", action="store_true", help="Show aggregate statistics")
    p.add_argument("--csv", action="store_true", help="Export as CSV")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("health", help="Check KB health")
    p.set_defaults(func=cmd_health)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
