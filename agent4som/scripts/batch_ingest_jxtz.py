#!/usr/bin/env python3
"""Batch fetch XJTU notice pages and ingest into knowledge base.

Reads data/jxtz_notices.jsonl (5512 records), fetches each page,
solves the JS challenge once per session, extracts vsb_content,
and ingests each notice into the global knowledge base scope.

Results written to data/jxtz_ingest_results.jsonl with status per record.
"""
import json
import os
import re
import sys
import time

import requests
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Config ────────────────────────────────────────────────────────────
INPUT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "jxtz_notices.jsonl")
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "jxtz_ingest_results.jsonl")
REQUEST_DELAY = 2  # seconds between requests
SCOPE = "global"
USER_ID = "admin"

# ── WAF challenge solver ─────────────────────────────────────────────

def _solve_challenge(session: requests.Session, test_url: str) -> bool:
    """Solve the JS challenge once per session. Returns True on success."""
    try:
        r = session.get(test_url, timeout=15)
        cid = re.search(r'challengeId\s*=\s*"([^"]+)"', r.text)
        ans = re.search(r'answer\s*=\s*(\d+)', r.text)
        if not cid or not ans:
            # No challenge found — cookie already valid
            return True
        r2 = session.post("https://jwc.xjtu.edu.cn/dynamic_challenge", json={
            "challenge_id": cid.group(1), "answer": int(ans.group(1)),
            "browser_info": {"userAgent": "Mozilla/5.0", "language": "zh-CN",
                             "platform": "Linux", "cookieEnabled": True,
                             "hardwareConcurrency": 4, "deviceMemory": 8,
                             "timezone": "Asia/Shanghai"},
        }, timeout=10)
        return r2.json().get("success", False)
    except Exception as exc:
        print(f"  ⚠️  Challenge solve failed: {exc}")
        return False

# ── Page fetcher ──────────────────────────────────────────────────────

def _fetch_notice(session: requests.Session, url: str) -> dict | None:
    """Fetch and parse a single notice page. Returns {title, date, body} or None."""
    try:
        r = session.get(url, timeout=15)
    except Exception as exc:
        return {"error": f"fetch: {exc}"}

    # Decode with best encoding
    raw = r.content
    text = ""
    for enc in ['utf-8', 'gb2312', 'gbk', 'gb18030', 'latin-1']:
        try:
            decoded = raw.decode(enc)
            if '通知' in decoded or '西安' in decoded or len(decoded) > 2000:
                text = decoded
                break
        except Exception:
            continue
    if not text:
        text = r.text

    # Check for WAF challenge
    if 'challengeId' in text and 'loader' in text:
        return {"error": "waf_blocked"}

    # Check for 404
    if '404' in text and len(text) < 5000:
        return {"error": "404_not_found"}

    # Title
    title_m = re.search(r'<title>([^<]+)</title>', text)
    title = title_m.group(1).strip() if title_m else ""

    # Date
    date_m = re.search(r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})[日号]?', text)
    date = date_m.group(1) if date_m else ""

    # Content (vsb_content = XJTU CMS content div)
    content_m = re.search(
        r'id="vsb_content"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        text, re.DOTALL,
    )
    if not content_m:
        content_m = re.search(
            r'class="v_news_content"[^>]*>(.*?)</div>\s*</div>\s*</div>',
            text, re.DOTALL,
        )
    if not content_m:
        return {"error": "no_content_div"}

    body = content_m.group(1)
    body = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL)
    body = re.sub(r'<style[^>]*>.*?</style>', '', body, flags=re.DOTALL)
    body = re.sub(r'<[^>]+>', '\n', body)
    body = re.sub(r'&nbsp;', ' ', body)
    body = re.sub(r'\n\s*\n+', '\n', body).strip()

    if len(body) < 50:
        return {"error": "content_too_short"}

    return {"title": title, "date": date, "body": body, "url": url}


# ── Pipeline helpers（离线纯函数，可单测） ─────────────────────────────

def _load_notices() -> list[dict]:
    """读 INPUT_FILE（jsonl）→ 通知列表（跳过空行）。"""
    notices = []
    with open(INPUT_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                notices.append(json.loads(line))
    return notices


def _notice_filename(cat: str, orig_title: str) -> str:
    """入库显示名："[类别] 标题"；超 200 字截断（197 + "..."）。"""
    filename = f"[{cat}] {orig_title}" if cat else orig_title
    if len(filename) > 200:
        filename = filename[:197] + "..."
    return filename


def _pub_date_from(raw_date: str) -> str:
    """页面日期原文（2024/3/5、2024年3月5日）→ YYYY-MM-DD；无匹配空串。"""
    dm = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})', raw_date)
    if dm:
        return f"{int(dm.group(1)):04d}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
    return ""


def _write_results(results: list) -> None:
    """原子写结果文件（temp + rename，POSIX 原子；中断写防损坏）。"""
    tmp_out = OUTPUT_FILE + ".tmp"
    with open(tmp_out, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp_out, OUTPUT_FILE)


def _load_resume() -> tuple[list, int, int, int, int]:
    """续跑：读已写结果 → (results, resume_idx, ok, fail, skip)。

    损坏行跳过（中断写残留）。"""
    results: list = []
    resume_idx = 0
    ok = fail = skip = 0
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    with contextlib.suppress(json.JSONDecodeError):
                        results.append(json.loads(line))
        resume_idx = len(results)
        ok = sum(1 for r in results if r.get("status") == "ok")
        fail = sum(1 for r in results if r.get("status") == "fail")
        skip = sum(1 for r in results if r.get("status") == "skip")
        print(f"📋 Resuming from #{resume_idx} (already: {ok} ok, {fail} fail, {skip} skip)")
    return results, resume_idx, ok, fail, skip


def _ingest_notice(orch, i: int, notice: dict, notice_data: dict) -> tuple[str, dict]:
    """单条通知入库：写临时 txt → ingest → (计数键 ok/skip/fail, 结果记录)。"""
    url = notice["url"]
    cat = notice.get("category", "")
    orig_title = notice.get("title", "")
    record_id = url.rstrip("/").split("/")[-1].replace(".htm", "")
    filename = _notice_filename(cat, orig_title)

    body = notice_data["body"]
    body_with_source = (
        f"来源: {notice_data.get('url', '')}\n"
        f"发布日期: {_pub_date_from(notice_data.get('date', ''))}\n"
        f"类别: {cat}\n"
        f"标题: {orig_title}\n"
        f"来源类型: web_notice\n\n"
        f"{body}"
    )
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', orig_title)[:80]
    tmp_dir = os.path.join(os.path.dirname(INPUT_FILE), "jxtz_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"jxtz_{record_id}_{safe_title}.txt")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(body_with_source)

    try:
        result = orch.ingest_file(
            USER_ID, tmp_path, scope=SCOPE, source="jxtz", count_quota=False
        )
        os.remove(tmp_path)

        if result.status.value in ("ingested", "replaced"):
            print(f"  ✅ {result.status.value} — {result.node_count} nodes, {len(body_with_source)} chars")
            return "ok", {
                "index": i, "url": url, "category": cat, "title": orig_title,
                "filename": filename + ".txt",
                "status": "ok", "nodes": result.node_count,
                "content_chars": len(body),
            }
        if result.status.value == "skipped":
            print("  ⏭️  SKIPPED (already indexed)")
            return "skip", {
                "index": i, "url": url, "category": cat, "title": orig_title,
                "status": "skip", "reason": "already_indexed",
            }
        print(f"  ❌ INGEST: {result.status.value} — {result.notification or ''}")
        return "fail", {
            "index": i, "url": url, "category": cat, "title": orig_title,
            "status": "fail",
            "error": f"ingest_{result.status.value}",
            "detail": result.notification or "",
        }
    except Exception as exc:
        with contextlib.suppress(Exception):
            os.remove(tmp_path)
        print(f"  ❌ INGEST EXCEPTION: {exc}")
        return "fail", {
            "index": i, "url": url, "category": cat, "title": orig_title,
            "status": "fail", "error": f"ingest_exception: {exc}",
        }


# ── Main ──────────────────────────────────────────────────────────────

def main():
    notices = _load_notices()
    total = len(notices)
    print(f"📂 Loaded {total} notices from {INPUT_FILE}")

    # ── Load .env + init (HTTP mode — chroma-server stays running) ──
    # MUST load .env BEFORE importing orchestration modules — class-level
    # attributes like _MINERU_URL are evaluated at import time.
    from knowledge_base.bootstrap import load_dotenv
    load_dotenv()
    from knowledge_base.bootstrap import build_embedding_function, create_ingestion_orchestrator

    from knowledge_base.repository.chroma_repository import ChromaRepository, get_chroma_client
    from knowledge_base.core.sqlite_store import SqliteStore

    # Always HTTP client via get_chroma_client() — handles token auth.
    client = get_chroma_client()
    ef = build_embedding_function()
    store = SqliteStore(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "quota.db"))
    repo = ChromaRepository(client, embedding_function=ef, sqlite_store=store)
    orch = create_ingestion_orchestrator(repo=repo)

    # ── Reset (--reset) via HTTP API ────────────────────────────
    if "--reset" in sys.argv:
        print("\n🔄 重置教学通知（HTTP 模式，按 source='jxtz' 清空）...")

        coll = client.get_collection("raw_nodes")
        before = coll.count()
        # Delete ALL jxtz nodes regardless of source_file format.
        # Previous code tried to match by computed filenames which
        # diverged across format changes.  Using the source field is
        # format-agnostic and reliable.
        with contextlib.suppress(Exception):
            coll.delete(where={"source": "jxtz"})
        after = coll.count()
        print(f"  已删除 {before - after} 个 chunk（重置前 {before} → 重置后 {after}）")

        store.delete_user_data("admin")
        print("  已清除版本管理器元数据")

        if os.path.exists(OUTPUT_FILE):
            os.remove(OUTPUT_FILE)
            print("  已清除旧结果文件")

        print("  重置完成\n")

    # HTTP session (reuses challenge cookie)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    # Solve challenge once
    print("🔐 Solving WAF challenge...")
    if not _solve_challenge(session, notices[0]["url"]):
        print("❌ Failed to solve WAF challenge. Aborting.")
        sys.exit(1)
    print("✅ Challenge solved, cookie valid for 24h")

    # Load previous results for resume
    results, resume_idx, ok, fail, skip = _load_resume()

    for i, notice in enumerate(notices):
        if i < resume_idx:
            continue

        url = notice["url"]
        cat = notice.get("category", "")
        orig_title = notice.get("title", "")

        print(f"\n[{i+1}/{total}] {orig_title[:60]}")

        # Fetch
        notice_data = _fetch_notice(session, url)

        if notice_data and "error" in notice_data:
            print(f"  ❌ FETCH: {notice_data['error']}")
            results.append({
                "index": i, "url": url, "category": cat, "title": orig_title,
                "status": "fail", "error": notice_data["error"],
            })
            fail += 1
        elif notice_data:
            status, rec = _ingest_notice(orch, i, notice, notice_data)
            results.append(rec)
            if status == "ok":
                ok += 1
            elif status == "skip":
                skip += 1
            else:
                fail += 1
        else:
            print("  ❌ FETCH: no data returned")
            results.append({
                "index": i, "url": url, "category": cat, "title": orig_title,
                "status": "fail", "error": "no_data",
            })
            fail += 1

        # Save progress every 50 records (atomic write via temp + rename)
        if (i + 1) % 50 == 0:
            _write_results(results)
            print(f"\n--- Progress saved: {ok} ok, {fail} fail, {skip} skip ---")

        time.sleep(REQUEST_DELAY)

    # Final save (atomic write via temp + rename)
    _write_results(results)

    print("\n" + "=" * 60)
    print(f"✅ 完成: {ok} 成功, {fail} 失败, {skip} 跳过")
    print(f"   结果文件: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
