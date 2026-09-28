"""文件中心 —— 服务层（登记/适用性/解析编排/自动引用）——设计 007 §5/§6/§9。

- `ingest_file`：登记原件（hash 去重）+ 设默认适用性 + 建解析 job。
- `process_pending` / `sync_jobs`：编排解析（`plan_structured`/`text` 在本进程；
  `warning_plan`/`warning_gen_ed` 经 HTTP 调学业预警服务）。
- `resolve`：按 (feature, grade, major) 取适用文件 + 产物定位。
- `migrate_from_legacy`：把 `plan_source_doc` + 预警公共文件导入中心（幂等）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import urllib.request
from typing import Any, Optional

from .db import DocCenterDB
from .models import (
    ALL_GRADES,
    PIPE_PLAN,
    PIPE_POLICY,
    PIPE_TEXT,
    PIPE_WARNING_GEN_ED,
    PIPE_WARNING_PLAN,
    STATUS_DONE,
    STATUS_FAILED,
    Applicability,
    DocFile,
    default_for,
)

# 学业预警服务（供 warning_* 管线调用）
WARNING_API_URL = os.environ.get("WARNING_API_URL", "http://127.0.0.1:8008")
WARNING_API_KEY = os.environ.get("WARNING_API_KEY", "")
# 中心优先取数开关（设计 007 §6）
DOC_CENTER_RESOLVE = os.environ.get("DOC_CENTER_RESOLVE", "on").lower() in ("1", "on", "true", "yes")

TRAINING_PLAN_DB = os.environ.get("TRAINING_PLAN_DB", "data/training_plan.db")


def _file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────── 类型识别 ───────────────────────────

logger = logging.getLogger("doccenter.service")


def classify_doc_type(file_name: str) -> str:
    """文件名 → 中心原件类型（复用解读/政策两套分类）。"""
    n = file_name or ""
    try:
        from trainingplan import policy
        k = policy.classify_policy(n)
        if k:
            return k
    except Exception:
        logger.debug("policy 分类不可用，回退到 ingest.classify", exc_info=True)
    try:
        from trainingplan.ingest import classify
        c = classify(n)
    except Exception:
        c = "其他"
    return {
        "培养方案": "培养方案", "教学计划": "教学计划", "政策": "政策",
        "清单": "清单", "大纲": "大纲",
    }.get(c, "通识表" if "通识课程信息表" in n else "其他")


def detect_meta(file_name: str) -> tuple[str, str]:
    """（主年级, 归属专业）——尽量从文件名识别。"""
    year = ""
    m = re.search(r"(20\d\d)\s*版", file_name or "")
    if m:
        year = f"{m.group(1)}级"
    else:
        m = re.search(r"(20\d\d)\s*级", file_name or "")
        if m:
            year = f"{m.group(1)}级"
    major = ""
    m = re.search(r"([\u4e00-\u9fa5]{2,10})专业培养方案", file_name or "")
    if m:
        try:
            from trainingplan.service import normalize_major
            major = normalize_major(m.group(1)) or ""
        except Exception:
            major = ""
    return year, major


# ─────────────────────────── 登记 / 适用性 ───────────────────────────

def _upsert_doc_file(db: DocCenterDB, path: str, name: str, file_hash: str,
                     dtype: str, grade0: str, major0: str, uploaded_by: str,
                     note: str, orig_name: str) -> tuple[int, bool]:
    """写 doc_file（hash 去重），返回 (fid, created)。

    同内容重复上传：用显式 orig_name 修正历史临时名（否则永远显示 U4Sek…）。"""
    existed = db.get_file_by_hash(file_hash)
    f = DocFile(file_hash=file_hash, file_name=name, file_path=os.path.abspath(path),
                ext=os.path.splitext(name)[1].lower(), size_bytes=os.path.getsize(path),
                doc_type=dtype, subject_major=major0, subject_grade=grade0,
                title=name, uploaded_by=uploaded_by, note=note)
    fid, created = db.upsert_file(f)
    if not created and orig_name.strip():
        existing = db.get_file(fid)
        if existing and existing.file_name != name:
            db.update_file(fid, file_name=name, title=name)
    return fid, created


def _apply_default_applicability(db: DocCenterDB, fid: int, dtype: str,
                                 features: Optional[list[str]],
                                 grades: Optional[list[str]],
                                 majors: Optional[list[str]],
                                 grade0: str, major0: str,
                                 created: bool) -> tuple[list, list]:
    """写默认适用性（管理员之后可改）；**已存在的文件不重置适用性**（除非显式传入）。

    返回 (feats, gl) 供登记回执使用。"""
    dflt = default_for(dtype)
    feats = features if features is not None else dflt["features"]
    gl = grades if grades is not None else ([grade0] if grade0 else [ALL_GRADES])
    if not gl:
        gl = [ALL_GRADES]
    ml = majors if majors is not None else ([major0] if major0 else [""])
    explicit = (features is not None) or (grades is not None) or (majors is not None)
    if created or explicit:
        rows = [Applicability(file_id=fid, feature=ft, grade=g, major=m)
                for ft in feats for g in gl for m in ml]
        db.set_applicability(fid, rows)
    return feats, gl


def ingest_file(path: str, *, doc_type: str = "", subject_major: str = "",
                subject_grade: str = "", features: Optional[list[str]] = None,
                grades: Optional[list[str]] = None, majors: Optional[list[str]] = None,
                uploaded_by: str = "", note: str = "", db_path: Optional[str] = None,
                enqueue: bool = True, orig_name: str = "") -> dict[str, Any]:
    """登记一份公共教学文件：hash 去重 → 写 doc_file → 设适用性 → 建解析 job。

    `orig_name`：真实文件名（微信上传落盘名是临时 hash 名）。分类/识别/展示均以它为准；
    hash 去重命中时，若显式给了 `orig_name`，顺带修正已入库记录的展示名。
    """
    if not os.path.isfile(path):
        return {"status": "error", "message": "文件不存在"}
    name = os.path.basename(orig_name or path)
    file_hash = _file_md5(path)
    dtype = doc_type or classify_doc_type(name)
    det_year, det_major = detect_meta(name)
    grade0 = subject_grade or det_year
    major0 = subject_major or det_major

    db = DocCenterDB(db_path)
    try:
        fid, created = _upsert_doc_file(db, path, name, file_hash, dtype, grade0,
                                        major0, uploaded_by, note, orig_name)
        feats, gl = _apply_default_applicability(db, fid, dtype, features, grades,
                                                 majors, grade0, major0, created)
        # 解析 job（按类型默认管线；已存在则不动）
        dflt = default_for(dtype)
        if enqueue:
            for pipe in dflt["pipelines"]:
                db.ensure_job(fid, pipe)
        return {"status": "ok", "id": fid, "created": created, "doc_type": dtype,
                "subject_grade": grade0, "subject_major": major0,
                "features": feats, "grades": gl, "pipelines": dflt["pipelines"]}
    finally:
        db.close()


# ─────────────────────────── 解析编排 ───────────────────────────

def process_pending(limit: int = 5, db_path: Optional[str] = None) -> dict[str, int]:
    """消费 `plan_structured` / `text` 管线（本进程）。warning_* 由 `process_warning` 处理。"""
    db = DocCenterDB(db_path)
    done = failed = text_ok = 0
    try:
        for _ in range(max(0, limit)):
            job = db.claim_next_job([PIPE_PLAN, PIPE_POLICY, PIPE_TEXT])
            if not job:
                break
            f = db.get_file(job.file_id)
            if not f:
                db.finish_job(job.id, STATUS_FAILED, error="原件记录缺失")
                failed += 1
                continue
            try:
                if job.pipeline == PIPE_TEXT:
                    db.finish_job(job.id, STATUS_DONE, note="文本可用（原件可检索）")
                    text_ok += 1
                elif job.pipeline == PIPE_POLICY:
                    ref = _submit_policy(f)
                    if ref.get("status") in ("ok", "registered"):
                        db.finish_job(job.id, STATUS_DONE, output_ref=ref,
                                      note=ref.get("message", "政策解析完成"))
                        done += 1
                    else:
                        db.finish_job(job.id, STATUS_FAILED,
                                      error=ref.get("message", "政策解析失败"))
                        failed += 1
                else:  # plan_structured
                    ref = _submit_training_plan(f)
                    db.finish_job(job.id, "parsing", output_ref=ref,
                                  note="已入 training_plan 解析队列")
                    done += 1
            except Exception as exc:
                db.finish_job(job.id, STATUS_FAILED, error=f"{type(exc).__name__}: {exc}")
                failed += 1
    finally:
        db.close()
    sync_plan_jobs(db_path=db_path)
    return {"plan_or_text": done, "text": text_ok, "failed": failed}


def _submit_training_plan(f: DocFile) -> dict[str, Any]:
    """把培养方案/教学计划委托给 `training_plan` 单消费者队列解析。"""
    from trainingplan import service
    r = service.submit_upload(f.file_path, major=f.subject_major,
                              entry_year=f.subject_grade, uploader=f"doccenter:{f.uploaded_by or ''}")
    return {"plan_document_id": r.get("id"), "plan_status": r.get("status"),
            "message": r.get("message", "")}


def _submit_policy(f: DocFile) -> dict[str, Any]:
    """把政策文件（转专业/专业选择）委托给 `training_plan` 解析规则。

    同时登记 `plan_source_doc`（保留 text_content 与 source_doc_id 溯源），
    使文件中心成为政策入库的唯一入口。
    """
    from trainingplan import service
    return service.ingest_policy_path(f.file_path)


def sync_plan_jobs(db_path: Optional[str] = None) -> int:
    """把 `plan_structured` job 的状态同步为所关联 `plan_document` 的状态。"""
    if not os.path.isfile(TRAINING_PLAN_DB):
        return 0
    db = DocCenterDB(db_path)
    n = 0
    try:
        jobs = [j for j in db.get_jobs(pipeline=PIPE_PLAN)
                if j["status"] not in (STATUS_DONE, STATUS_FAILED)]
        if not jobs:
            return 0
        conn = sqlite3.connect(f"file:{os.path.abspath(TRAINING_PLAN_DB)}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            for j in jobs:
                try:
                    ref = json.loads(j["output_ref"] or "{}")
                except Exception:
                    ref = {}
                pid = ref.get("plan_document_id")
                if not pid:
                    continue
                row = conn.execute("SELECT parsed_status, in_file_meta FROM plan_document WHERE id=?",
                                   (pid,)).fetchone()
                if not row:
                    continue
                st = row["parsed_status"]
                if st == "done":
                    meta = {}
                    try:
                        meta = json.loads(row["in_file_meta"] or "{}")
                    except Exception:
                        meta = {}
                    db.finish_job(j["id"], STATUS_DONE, output_ref=ref,
                                  note=meta.get("note", ""))
                    n += 1
                elif st == "failed":
                    meta = {}
                    try:
                        meta = json.loads(row["in_file_meta"] or "{}")
                    except Exception:
                        meta = {}
                    db.finish_job(j["id"], STATUS_FAILED, output_ref=ref,
                                  error=meta.get("error", "培养方案解析失败"))
                    n += 1
        finally:
            conn.close()
    finally:
        db.close()
    return n


def process_warning(limit: int = 5, db_path: Optional[str] = None) -> dict[str, int]:
    """消费 `warning_plan` / `warning_gen_ed` 管线：HTTP 调学业预警上传接口（best-effort）。"""
    db = DocCenterDB(db_path)
    ok = failed = 0
    try:
        for _ in range(max(0, limit)):
            job = db.claim_next_job([PIPE_WARNING_PLAN, PIPE_WARNING_GEN_ED])
            if not job:
                break
            f = db.get_file(job.file_id)
            if not f:
                db.finish_job(job.id, STATUS_FAILED, error="原件记录缺失")
                failed += 1
                continue
            type_hint = "培养方案" if job.pipeline == PIPE_WARNING_PLAN else "通识课程信息表"
            try:
                ref = _post_warning_upload(f.file_path, f.file_name, type_hint, f.subject_grade)
                db.finish_job(job.id, STATUS_DONE, output_ref=ref,
                              note=f"已交学业预警（{ref.get('parsed_status','')}）")
                ok += 1
            except Exception as exc:
                db.finish_job(job.id, STATUS_FAILED, error=f"{type(exc).__name__}: {exc}")
                failed += 1
    finally:
        db.close()
    return {"warning": ok, "failed": failed}


def _post_warning_upload(path: str, file_name: str, type_hint: str, grade_hint: str) -> dict:
    """最小 multipart 上传（不依赖第三方库）。"""
    boundary = "----doccenter" + hashlib.md5(file_name.encode()).hexdigest()[:16]
    with open(path, "rb") as fh:
        content = fh.read()
    parts = []

    def field(name: str, value: str) -> None:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())

    field("type_hint", type_hint)
    field("grade_hint", grade_hint or "")
    field("orig_name", file_name)
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{file_name}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{WARNING_API_URL}/api/warning/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "X-API-Key": WARNING_API_KEY})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ─────────────────────────── 自动引用 ───────────────────────────

def resolve(feature: str, grade: str = "", major: str = "",
            db_path: Optional[str] = None) -> list[dict[str, Any]]:
    db = DocCenterDB(db_path)
    try:
        return db.resolve(feature, grade, major)
    finally:
        db.close()


# ─────────────────────────── 迁移（幂等） ───────────────────────────

def _legacy_rows(db_file: str, sql: str) -> list[sqlite3.Row]:
    """只读打开旧库取行；表缺失/查询失败 → 空（幂等跳过）。"""
    conn = sqlite3.connect(f"file:{os.path.abspath(db_file)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _migrate_plan_docs(training_plan_db: str, db_path: Optional[str],
                       stats: dict[str, Any]) -> None:
    """第 1 段：legacy `plan_source_doc` 逐条导入中心（stats 原地累加）。"""
    if not os.path.isfile(training_plan_db):
        return
    rows = _legacy_rows(training_plan_db,
                        "SELECT file_name,file_path,file_hash,size_bytes,ext,category,major,"
                        "entry_year,applies_to,note FROM plan_source_doc")
    for r in rows:
        path = r["file_path"] or ""
        if not path or not os.path.isfile(path):
            stats["skipped"] += 1
            continue
        try:
            dtype = {"培养方案": "培养方案", "教学计划": "教学计划", "政策": "政策",
                     "清单": "清单", "大纲": "大纲"}.get(r["category"], "其他")
            try:
                grades = json.loads(r["applies_to"] or "[]")
            except Exception:
                grades = []
            res = ingest_file(path, doc_type=dtype, subject_major=r["major"] or "",
                              subject_grade=r["entry_year"] or "", grades=grades or None,
                              uploaded_by="migrate", note=r["note"] or "")
            if res.get("created"):
                stats["registered"] += 1
            else:
                stats["skipped"] += 1
        except Exception as exc:
            stats["errors"].append(f"{r['file_name']}: {type(exc).__name__}: {exc}")


def _register_warning_doc(r: sqlite3.Row, path: str, dtype: str,
                          db_path: Optional[str], stats: dict[str, Any]) -> Optional[int]:
    """预警文件登记：优先按内容 hash 匹配已有原件（预警侧常存旧机路径，dgx 上不存在），
    未命中时按路径入库。返回 fid；跳过/失败为 None。"""
    db = DocCenterDB(db_path)
    try:
        f = db.get_file_by_hash(r["file_hash"] or "")
        fid = int(f.id) if f else None
    finally:
        db.close()
    if fid is not None:
        return fid
    if not path or not os.path.isfile(path):
        stats["skipped"] += 1
        return None
    try:
        res = ingest_file(path, doc_type=dtype, subject_grade=r["grade"] or "",
                          uploaded_by="migrate", note="来自学业预警")
        if res.get("created"):
            stats["registered"] += 1
        else:
            stats["skipped"] += 1
        return res.get("id")
    except Exception as exc:
        stats["errors"].append(f"{r['file_name']}: {type(exc).__name__}: {exc}")
        return None


def _migrate_warning_files(warning_db: str, db_path: Optional[str],
                           stats: dict[str, Any]) -> None:
    """第 2 段：预警公共文件（plan / gen_ed）导入中心（stats 原地累加）。"""
    if not os.path.isfile(warning_db):
        return
    rows = _legacy_rows(warning_db,
                        "SELECT id,file_name,file_path,file_hash,file_type,grade FROM source_file"
                        " WHERE file_type IN ('plan','gen_ed')")
    for r in rows:
        path = r["file_path"] or ""
        dtype = "培养方案" if r["file_type"] == "plan" else "通识表"
        pipe = PIPE_WARNING_PLAN if r["file_type"] == "plan" else PIPE_WARNING_GEN_ED
        fid = _register_warning_doc(r, path, dtype, db_path, stats)
        # 预警侧已解析 → 对应管线 job 标 done（复用既有 source_file，不重复上传）
        if fid:
            _mark_job_done(db_path, fid, pipe, {"source_file_id": int(r["id"])},
                           note="复用学业预警已解析文件")


def migrate_from_legacy(training_plan_db: str = TRAINING_PLAN_DB,
                        warning_db: str = "data/warning.db",
                        db_path: Optional[str] = None) -> dict[str, Any]:
    """把 `plan_source_doc` 与预警公共文件导入中心（按 hash 去重）。"""
    stats: dict[str, Any] = {"registered": 0, "skipped": 0, "errors": []}
    # 1) plan_source_doc
    _migrate_plan_docs(training_plan_db, db_path, stats)
    # 2) 预警公共文件（plan / gen_ed）
    _migrate_warning_files(warning_db, db_path, stats)
    return {"status": "ok", "registered": stats["registered"],
            "skipped": stats["skipped"], "errors": stats["errors"]}


def _mark_job_done(db_path: str, file_id: int, pipeline: str, output_ref: dict,
                   note: str = "") -> None:
    db = DocCenterDB(db_path)
    try:
        db.ensure_job(file_id, pipeline)
        jobs = list(db.get_jobs(file_id=file_id, pipeline=pipeline))
        if jobs:
            db.finish_job(jobs[0]["id"], STATUS_DONE, output_ref=output_ref, note=note)
    finally:
        db.close()
