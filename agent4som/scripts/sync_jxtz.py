#!/usr/bin/env python3
"""增量同步西安交通大学教务处「教学通知」到知识库。

每天抓取 https://jwc.xjtu.edu.cn/jxxx/jxtz2.htm（首页最新 9 条），
与本地 data/jxtz_notices.jsonl 做 URL 对比，发现新通知后：
  1. 追加到本地 JSONL
  2. 抓取详情页正文
  3. 入库到 ChromaDB（global scope）

若无新通知则直接退出，不启停 Gateway。
"""

import argparse
import ctypes
import fcntl
import json
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
import contextlib

# ── Paths ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = PROJECT_ROOT / "data" / "jxtz_notices.jsonl"
SYNC_LOG = PROJECT_ROOT / "data" / "jxtz_sync.log"
TMP_DIR = PROJECT_ROOT / "data" / "jxtz_tmp"
RUNS_FILE = PROJECT_ROOT / "data" / "jxtz_sync_runs.jsonl"
LOCK_FILE = PROJECT_ROOT / "data" / "jxtz_sync.lock"

BASE_URL = "https://jwc.xjtu.edu.cn"
LIST_URL = f"{BASE_URL}/jxxx/jxtz2.htm"

SCOPE = "global"
USER_ID = "admin"


# ═══════════════════════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════════════════════

def log(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    TMP_DIR.mkdir(parents=True, exist_ok=True)  # ensure data/ exists
    if SYNC_LOG.exists() and SYNC_LOG.stat().st_size > 5 * 1024 * 1024:
        SYNC_LOG.rename(SYNC_LOG.with_suffix(".log.old"))
    with open(SYNC_LOG, "a") as f:
        f.write(line + "\n")


# ═══════════════════════════════════════════════════════════════════════════
# WAF 挑战求解
# ═══════════════════════════════════════════════════════════════════════════

def simple_hash(s: str) -> int:
    """复现挑战页 JS simpleHash（32 位有符号整数运算后取绝对值）。"""
    h = 0
    for ch in s:
        h = ctypes.c_int32((h << 5) - h + ord(ch)).value
    return abs(h)


def solve_challenge(session: requests.Session) -> bool:
    """解 JS 反爬挑战，成功后 client_id cookie 在 session 中保持。

    新版挑战（2026-07-30 起）：var challengeId='...' + var a/b/operator 表达式，
    提交需附 hash 校验，client_id 由页面 JS 手动写入 cookie（服务端不返回
    Set-Cookie），须从响应中取回并手动设置。
    """
    try:
        r = session.get(LIST_URL, timeout=15)
        text = r.text

        # 新版：var challengeId='..' / var a=N / var b=N / var operator='+'
        cid = re.search(r"var challengeId\s*=\s*'([^']+)'", text)
        a = re.search(r"var a\s*=\s*(\d+)", text)
        b = re.search(r"var b\s*=\s*(\d+)", text)
        op = re.search(r"var operator\s*=\s*'([+\-*])'", text)
        if cid and a and b and op:
            if op.group(1) == "+":
                answer = int(a.group(1)) + int(b.group(1))
            elif op.group(1) == "-":
                answer = int(a.group(1)) - int(b.group(1))
            else:
                answer = int(a.group(1)) * int(b.group(1))
            hash_value = simple_hash(cid.group(1) + str(answer) + session.headers["User-Agent"][:10])
        else:
            # 旧版：challengeId=".." / answer=N
            cid = re.search(r'challengeId\s*=\s*"([^"]+)"', text)
            ans = re.search(r"answer\s*=\s*(\d+)", text)
            if not cid or not ans:
                return True  # 无挑战=cookie 已有效
            answer = int(ans.group(1))
            hash_value = None

        payload = {
            "challenge_id": cid.group(1),
            "answer": answer,
            "browser_info": {
                "userAgent": session.headers["User-Agent"],
                "language": "zh-CN",
                "platform": "Linux",
                "cookieEnabled": True,
                "hardwareConcurrency": 4,
                "deviceMemory": 8,
                "timezone": "Asia/Shanghai",
            },
        }
        if hash_value is not None:
            payload["hash"] = hash_value

        r2 = session.post(
            f"{BASE_URL}/dynamic_challenge",
            json=payload,
            timeout=10,
        )
        resp = r2.json()
        if not resp.get("success", False):
            return False
        client_id = resp.get("client_id")
        if client_id:
            session.cookies.set("client_id", client_id, domain="jwc.xjtu.edu.cn")
        return True
    except Exception as exc:
        log(f"⚠️ WAF 挑战失败: {exc}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
# 列表页解析
# ═══════════════════════════════════════════════════════════════════════════

def parse_list_page(text: str) -> list[dict]:
    """解析单页列表 HTML 中的通知条目（链接+日期+分类）。"""
    items = []
    # 模式1: ../../info/NNN/MMMM.htm
    for m in re.finditer(
        r'href="(\.\./(?:\.\./)?info/(\d+)/(\d+)\.htm)"[^>]*>(.*?)</a>\s*<span[^>]*>(\d{4}-\d{2}-\d{2})</span>',
        text, re.DOTALL,
    ):
        title = re.sub(r"<[^>]+>", "", m.group(4)).strip()
        if len(title) < 5:
            continue
        cat_match = re.match(r"\[([^\]]+)\]", title)
        items.append({
            "title": title,
            "category": cat_match.group(1) if cat_match else "",
            "date": m.group(5),
            "url": f"{BASE_URL}/info/{m.group(2)}/{m.group(3)}.htm",
        })

    # 模式2: ../../content.jsp?urltype=news.NewsContentUrl&wbtreeid=NNN&wbnewsid=MMMM
    for m in re.finditer(
        r'href="(\.\./(?:\.\./)?content\.jsp\?urltype=news\.NewsContentUrl&wbtreeid=(\d+)&wbnewsid=(\d+))"[^>]*>(.*?)</a>\s*<span[^>]*>(\d{4}-\d{2}-\d{2})</span>',
        text, re.DOTALL,
    ):
        title = re.sub(r"<[^>]+>", "", m.group(4)).strip()
        if len(title) < 5:
            continue
        cat_match = re.match(r"\[([^\]]+)\]", title)
        items.append({
            "title": title,
            "category": cat_match.group(1) if cat_match else "",
            "date": m.group(5),
            "url": f"{BASE_URL}/info/{m.group(2)}/{m.group(3)}.htm",
        })

    return items


def fetch_latest_notices(session: requests.Session, max_pages: int = 3) -> list[dict]:
    """抓取列表前 max_pages 页（每页约 10 条），仅链接+日期+分类，不含正文。

    抓多页可避免日发布量超过单页容量时漏抓。
    """
    page_urls: list[str] = [LIST_URL]
    try:
        r = session.get(LIST_URL, timeout=15)
        # 分页条: <a href="jxtz2/NNN.htm">2</a> 等数字页码，依次追加
        for m in re.finditer(r'href="(jxtz2/\d+\.htm)"', r.text):
            href = f"{BASE_URL}/jxxx/{m.group(1)}"
            if href not in page_urls:
                page_urls.append(href)
            if len(page_urls) >= max_pages:
                break
    except Exception as exc:
        log(f"❌ 获取列表页失败: {exc}")
        return []

    items: list[dict] = []
    seen: set[str] = set()
    for url in page_urls:
        try:
            r = session.get(url, timeout=15)
        except Exception as exc:
            log(f"❌ 获取列表页失败 {url}: {exc}")
            continue
        # 尝试正确编码（网站声称 UTF-8 但实际可能用 GB2312）
        raw = r.content
        for enc in ["utf-8", "gb2312", "gbk", "gb18030"]:
            try:
                decoded = raw.decode(enc)
                if "通知" in decoded:
                    r.encoding = enc
                    break
            except Exception:
                continue
        for n in parse_list_page(r.text):
            if n["url"] not in seen:
                seen.add(n["url"])
                items.append(n)
    return items


# ═══════════════════════════════════════════════════════════════════════════
# 详情页抓取
# ═══════════════════════════════════════════════════════════════════════════

def fetch_notice_body(session: requests.Session, url: str) -> tuple[str | None, bool]:
    """抓取通知详情页，返回 (清洗后的正文, 是否永久失败)。

    永久失败（HTTP 404 等页面不存在）：写入 JSONL 防无限重试；
    临时失败（网络异常 / 解析失败 / WAF 拦截）：留待下次同步重试。
    """
    try:
        r = session.get(url, timeout=15)
    except Exception as exc:
        log(f"  ⚠️ 获取详情失败 {url}: {exc}")
        return None, False
    if r.status_code in (404, 410):
        log(f"  ⚠️ 页面不存在 (HTTP {r.status_code}) {url}")
        return None, True

    raw = r.content
    text = ""
    for enc in ["utf-8", "gb2312", "gbk", "gb18030", "latin-1"]:
        try:
            decoded = raw.decode(enc)
            if "通知" in decoded or "西安" in decoded or len(decoded) > 2000:
                text = decoded
                break
        except Exception:
            continue
    if not text:
        text = r.text

    if "challengeId" in text and "loader" in text:
        log(f"  ⚠️ WAF 拦截 {url}")
        return None, False

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
        log(f"  ⚠️ 未找到正文区域 {url}")
        return None, False

    body = content_m.group(1)
    body = re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.DOTALL)
    body = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.DOTALL)
    body = re.sub(r"<[^>]+>", "\n", body)
    body = re.sub(r"&nbsp;", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body).strip()

    if len(body) < 50:
        log(f"  ⚠️ 正文过短 ({len(body)} 字符) {url}")
        return None, False

    return body, False


# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# 参数 / 运行记录 / 互斥锁
# ═══════════════════════════════════════════════════════════════════════════

def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="增量同步西安交通大学教务处教学通知")
    parser.add_argument("--trigger", choices=["schedule", "manual"], default="schedule",
                        help="触发来源（默认 schedule）")
    parser.add_argument("--by", default="", help="手动触发的调用者 openid（仅记录用）")
    parser.add_argument("--run-id", dest="run_id", default="",
                        help="本次运行令牌；手动路径由端点传入，未传则自生成")
    return parser.parse_args(argv)


def acquire_lock():
    """非阻塞获取进程级互斥锁；失败返回 None（锁随进程退出自动释放）。"""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


def sanitize_error(exc: BaseException) -> str:
    """异常 → 可安全落盘的摘要（类型 + 截断 200 字），不写完整 str(exc)。"""
    name = type(exc).__name__
    msg = str(exc).strip().replace("\n", " ")
    if len(msg) > 200:
        msg = msg[:200] + "…"
    return f"{name}: {msg}" if msg else name


def summarize_error(reasons: list[str]) -> str:
    if not reasons:
        return ""
    return "; ".join(reasons[:3])[:200]


def classify_status(new: int, ok: int, skip: int, fail: int) -> str:
    """四态：no_new / ok / partial / failed（skip 计入成功）。"""
    if new == 0:
        return "no_new"
    if fail == 0:
        return "ok"
    if ok + skip > 0:
        return "partial"
    return "failed"


def append_run_record(run_id: str, status: str, args, new: int, ok: int,
                      fail: int, skip: int, error: str, items: list[dict]) -> None:
    """追加一行运行记录（不截断）。"""
    record = {
        "run_id": run_id,
        "run_at": datetime.now().astimezone().isoformat(),
        "trigger": args.trigger,
        "by": args.by,
        "status": status,
        "new": new,
        "ok": ok,
        "fail": fail,
        "skip": skip,
        "error": error,
        "items": items,
    }
    RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RUNS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════

def _load_existing_urls() -> set[str]:
    """本地 JSONL 全部 URL（坏行静默跳过）。"""
    existing_urls: set[str] = set()
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            for line in f:
                with contextlib.suppress(json.JSONDecodeError, KeyError):
                    existing_urls.add(json.loads(line.strip())["url"])
    return existing_urls


def _fetch_latest_with_retry(session) -> list[dict]:
    """WAF 挑战 + 首页列表获取，失败时退避重试（网络瞬断 / WAF 临时风控）。"""
    latest: list[dict] = []
    for attempt in range(1, 4):
        if attempt > 1:
            log(f"⚠️ 第 {attempt} 次尝试...")
            time.sleep(15 * (attempt - 1))
        if not solve_challenge(session):
            log(f"⚠️ WAF 挑战失败（第 {attempt} 次）")
            continue
        latest = fetch_latest_notices(session)
        if latest:
            break
    return latest


def _init_ingest_pipeline():
    """加载 .env 并初始化入库管线（HTTP 模式 — chroma-server 保持运行）。

    MUST load .env BEFORE importing orchestration modules — class-level
    attributes like _MINERU_URL are evaluated at import time.
    初始化失败向上抛（调用方给固定文案，不把可能含 token 的原始异常写进运行记录）。"""
    from knowledge_base.bootstrap import load_dotenv
    load_dotenv()
    from knowledge_base.bootstrap import build_embedding_function, create_ingestion_orchestrator
    from knowledge_base.repository.chroma_repository import ChromaRepository, get_chroma_client
    from knowledge_base.core.sqlite_store import SqliteStore

    client = get_chroma_client()
    ef = build_embedding_function()
    store = SqliteStore(str(PROJECT_ROOT / "data" / "quota.db"))
    repo = ChromaRepository(client, embedding_function=ef, sqlite_store=store)
    return create_ingestion_orchestrator(repo=repo)


def _ingest_one_notice(session, orch, n: dict) -> dict:
    """单条通知入库。返回 {outcome(ok/skip/fail), item, commit, reason}。

    commit 非空 = 记入 JSONL（入库成功 / 已在库 / 永久失败 404），None =
    临时失败，下次同步自动重试（避免"已记录但从未入库"的静默丢失）。"""
    url = n["url"]
    title = n["title"]
    record_id = url.rstrip("/").split("/")[-1].replace(".htm", "")

    body, permanent = fetch_notice_body(session, url)
    # 临时失败，可能是 WAF 拦截 → 重新解挑战后重试一次
    if body is None and not permanent and solve_challenge(session):
        body, permanent = fetch_notice_body(session, url)
    if body is None:
        if permanent:
            item = {"title": title, "url": url, "status": "file_not_found", "nodes": 0}
            return {"outcome": "fail", "item": item, "commit": n,
                    "reason": f"页面不存在 {url}"}
        item = {"title": title, "url": url, "status": "error", "nodes": 0}
        return {"outcome": "fail", "item": item, "commit": None,
                "reason": f"抓取失败 {url}"}

    # 构造带来源信息的正文并落临时文件
    body_with_source = (
        f"来源: {url}\n"
        f"发布日期: {n['date']}\n"
        f"类别: {n['category']}\n"
        f"标题: {title}\n"
        f"来源类型: web_notice\n\n"
        f"{body}"
    )
    safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)[:80]
    tmp_path = TMP_DIR / f"jxtz_{record_id}_{safe_title}.txt"
    tmp_path.write_text(body_with_source, encoding="utf-8")

    try:
        result = orch.ingest_file(
            USER_ID, str(tmp_path), scope=SCOPE, source="jxtz", count_quota=False
        )
        tmp_path.unlink(missing_ok=True)

        item_status = result.status.value
        item = {"title": title, "url": url,
                "status": item_status, "nodes": result.node_count}
        if item_status in ("ingested", "replaced"):
            log(f"    ✅ {item_status} — {result.node_count} nodes")
            return {"outcome": "ok", "item": item, "commit": n, "reason": None}
        if item_status == "skipped":
            log("    ⏭️  SKIPPED (已存在)")
            return {"outcome": "skip", "item": item, "commit": n, "reason": None}
        log(f"    ❌ {item_status}: {result.notification or ''}")
        return {"outcome": "fail", "item": item, "commit": None,
                "reason": f"{item_status} {url}"}
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        log(f"    ❌ 异常: {exc}")
        item = {"title": title, "url": url, "status": "error", "nodes": 0}
        return {"outcome": "fail", "item": item, "commit": None,
                "reason": f"入库异常 {type(exc).__name__}"}


def _prepend_committed(committed: list[dict]) -> None:
    """committed 记录写至 JSONL 开头（从新到旧；原子替换防半写损坏）。"""
    if not committed:
        return
    old_lines = []
    if DATA_FILE.exists():
        raw = DATA_FILE.read_text(encoding="utf-8").strip()
        if raw:
            old_lines = raw.split("\n")
    new_lines = [json.dumps(n, ensure_ascii=False) for n in committed]
    tmp_jsonl = DATA_FILE.with_suffix(".jsonl.tmp")
    tmp_jsonl.write_text("\n".join(new_lines + old_lines) + "\n", encoding="utf-8")
    tmp_jsonl.replace(DATA_FILE)
    log(f"已写入 {len(committed)} 条到 {DATA_FILE} 开头")


def _sync_once(args, run_id: str) -> int:
    sys.path.insert(0, str(PROJECT_ROOT))  # must precede any knowledge_base import
    log("=" * 60)
    log("开始增量同步检查")

    # ── 1. 加载本地全部 URL ────────────────────────────────────────
    existing_urls = _load_existing_urls()
    log(f"本地 {len(existing_urls)} 条记录用于比对")

    # ── 2. 抓取首页 ──────────────────────────────────────────────────
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    latest = _fetch_latest_with_retry(session)
    if not latest:
        log("❌ 未获取到任何通知，可能原因：WAF 升级 / HTML 结构变更 / 网络问题")
        log("⚠️  请检查 XJTU CMS 页面结构是否变化：正则匹配模式可能需要更新")
        append_run_record(run_id, "failed", args, 0, 0, 0, 0,
                          "未获取到任何通知（WAF / 页面结构 / 网络）", [])
        return 1
    log(f"首页获取到 {len(latest)} 条通知")

    # ── 3. 对比找出新增 ──────────────────────────────────────────────
    new_notices = [n for n in latest if n["url"] not in existing_urls]
    if not new_notices:
        log("✅ 无新通知，跳过")
        append_run_record(run_id, "no_new", args, 0, 0, 0, 0, "", [])
        return 0

    log(f"🆕 发现 {len(new_notices)} 条新通知:")
    for n in new_notices:
        log(f"   {n['date']} [{n['category']}] {n['title'][:60]}")

    # ── 4. 逐条入库，成功（或永久失败）才记入 JSONL ────────────────
    try:
        orch = _init_ingest_pipeline()
    except Exception as exc:
        log(f"❌ 入库管线初始化失败: {type(exc).__name__}: {exc}")
        append_run_record(run_id, "failed", args, len(new_notices), 0, 0, 0,
                          "入库管线初始化失败", [])
        return 1

    ok = fail = skip = 0
    committed: list[dict] = []  # 入库成功/已在库/永久失败的记录 → 写入 JSONL
    items: list[dict] = []
    fail_reasons: list[str] = []
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    for n in new_notices:
        log(f"  入库: {n['title'][:60]}")
        r = _ingest_one_notice(session, orch, n)
        if r["outcome"] == "ok":
            ok += 1
        elif r["outcome"] == "skip":
            skip += 1
        else:
            fail += 1
        if r["commit"]:
            committed.append(r["commit"])
        items.append(r["item"])
        if r["reason"]:
            fail_reasons.append(r["reason"])

    # ── 5. 写入 JSONL 开头 ── 6. 输出摘要 + 运行记录 ──────────────────
    _prepend_committed(committed)
    log(f"同步完成: {ok} 成功, {fail} 失败, {skip} 跳过 (共 {len(new_notices)} 条新增)")
    run_status = classify_status(len(new_notices), ok, skip, fail)
    append_run_record(run_id, run_status, args, len(new_notices), ok, fail, skip,
                      summarize_error(fail_reasons), items)
    return 1 if run_status == "failed" else 0


def main() -> int:
    args = parse_args()
    # 持锁失败是唯一不写运行记录的终止路径；必须在读账本前拿到锁。
    lock_fd = acquire_lock()
    if lock_fd is None:
        log("⚠️ 已有同步在运行，跳过本次")
        return 0

    run_id = args.run_id.strip() or uuid.uuid4().hex
    try:
        return _sync_once(args, run_id)
    except BaseException as exc:
        log(f"❌ 同步异常终止: {type(exc).__name__}: {exc}")
        append_run_record(run_id, "failed", args, 0, 0, 0, 0, sanitize_error(exc), [])
        return 1


if __name__ == "__main__":
    sys.exit(main())
