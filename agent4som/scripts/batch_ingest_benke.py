#!/usr/bin/env python3
"""Batch ingest files under a directory into the knowledge base.

All writes go through chroma-server HTTP API — PersistentClient is never used,
so chroma-server stays running and the HNSW index remains consistent.

Usage:
    python scripts/batch_ingest_benke.py [--reset]
"""

import json
import os
import sys
import time
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_dir = os.path.join(base_dir, "global", "本科管理文件库")
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_success = os.path.join(base_dir, "data", f"ingest_success_{timestamp}.json")
    output_failed = os.path.join(base_dir, "data", f"ingest_failed_{timestamp}.json")

    indexable_exts = {".pdf", ".docx", ".doc", ".txt", ".md", ".xlsx", ".xls", ".csv", ".pptx", ".jpg", ".jpeg", ".png"}

    # ── Parse args ──────────────────────────────────────────────────
    do_reset = "--reset" in sys.argv

    # ── Collect files ───────────────────────────────────────────────
    all_files = []
    skipped_non_indexable = []
    for dirpath, _dirnames, filenames in os.walk(target_dir):
        for fname in sorted(filenames):
            fpath = os.path.join(dirpath, fname)
            _, ext = os.path.splitext(fname)
            if ext.lower() in indexable_exts:
                all_files.append(fpath)
            else:
                skipped_non_indexable.append(fpath)

    if skipped_non_indexable:
        print(f"⏭️  跳过 {len(skipped_non_indexable)} 个非索引文件")

    print(f"📂 发现 {len(all_files)} 个可索引文件")
    print(f"📁 目录: {target_dir}")

    # ── Load .env + init (HTTP mode — chroma-server stays running) ──
    # MUST load .env BEFORE importing orchestration modules — class-level
    # attributes like _MINERU_URL are evaluated at import time.
    from knowledge_base.bootstrap import load_dotenv
    load_dotenv()
    from knowledge_base.bootstrap import build_embedding_function, create_ingestion_orchestrator

    print("🔧 初始化 ChromaDB（HTTP 模式）和入库管线...")
    from knowledge_base.repository.chroma_repository import ChromaRepository, get_chroma_client
    from knowledge_base.core.sqlite_store import SqliteStore

    embed_fn = build_embedding_function()

    # Always use HTTP client via get_chroma_client() — handles token auth.
    client = get_chroma_client()
    sqlite_path = os.path.join(base_dir, "data", "quota.db")
    store = SqliteStore(sqlite_path)
    repo = ChromaRepository(client, embedding_function=embed_fn, sqlite_store=store)
    orch = create_ingestion_orchestrator(repo=repo)

    # ── Reset (if --reset) via HTTP API ─────────────────────────────
    if do_reset:
        print("\n🔄 重置文件通知（HTTP 模式，按 source='file' 清空）...")

        # Delete ALL file nodes regardless of source_file format.
        # Using the source field is format-agnostic — no need to guess
        # whether extensions were stripped or not.
        coll = client.get_collection("raw_nodes")
        before = coll.count()
        with contextlib.suppress(Exception):
            coll.delete(where={"source": "file"})
        after = coll.count()
        deleted = before - after
        print(f"  已删除 {deleted} 个 chunk（重置前 {before} → 重置后 {after}）")

        # Reset version metadata for admin
        store.delete_user_data("admin")
        print("  已清除版本管理器元数据")
        print("  重置完成\n")

    print("=" * 80)

    # ── Ingest ──────────────────────────────────────────────────────
    success_list = []
    failed_list = []
    total = len(all_files)

    for i, fpath in enumerate(all_files, 1):
        fname = os.path.basename(fpath)
        _, ext = os.path.splitext(fname)
        print(f"\n[{i}/{total}] {fname}")

        start = time.time()
        try:
            result = orch.ingest_file("admin", fpath, scope="global", source="file")
            elapsed = time.time() - start

            if result.status.value in ("quota_exceeded", "error", "unsupported_type", "file_not_found"):
                failed_list.append({
                    "path": fpath, "filename": fname, "ext": ext,
                    "status": result.status.value,
                    "notification": result.notification or "",
                    "error_detail": result.error_detail or "",
                    "elapsed_s": round(elapsed, 2),
                })
                print(f"  ❌ FAILED ({result.status.value}): {result.notification or result.error_detail or ''}")
            else:
                status_label = {
                    "ingested": "INGESTED", "replaced": "REPLACED", "skipped": "SKIPPED",
                }.get(result.status.value, result.status.value)
                entry = {
                    "path": fpath, "filename": fname, "ext": ext,
                    "status": result.status.value,
                    "node_count": result.node_count,
                    "elapsed_s": round(elapsed, 2),
                }
                if result.warning:
                    entry["warning"] = result.warning
                success_list.append(entry)
                extra = f" (⚠ {result.warning})" if result.warning else ""
                print(f"  ✅ {status_label} — {result.node_count} nodes, {elapsed:.1f}s{extra}")

        except Exception as exc:
            elapsed = time.time() - start
            failed_list.append({
                "path": fpath, "filename": fname, "ext": ext,
                "status": "exception",
                "notification": str(exc), "error_detail": str(exc),
                "elapsed_s": round(elapsed, 2),
            })
            print(f"  ❌ EXCEPTION: {exc}")

    # ── Write output ────────────────────────────────────────────
    os.makedirs(os.path.dirname(output_success), exist_ok=True)
    with open(output_success, "w", encoding="utf-8") as f:
        json.dump(success_list, f, ensure_ascii=False, indent=2)
    with open(output_failed, "w", encoding="utf-8") as f:
        json.dump(failed_list, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 80)
    print(f"✅ 入库完成: {len(success_list)} 成功, {len(failed_list)} 失败")
    print(f"   成功列表: {output_success}")
    print(f"   失败列表: {output_failed}")


if __name__ == "__main__":
    main()
