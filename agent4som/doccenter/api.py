"""文件中心 HTTP API（FastAPI APIRouter）——设计 007 §8。

挂载在培养方案解读/学业规划服务（:8009）：`/api/doc-center/*`。
鉴权沿用 `X-API-Key`（`TRAINING_PLAN_API_KEY` 或 `WARNING_API_KEY`）；写类仅 admin/owner。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, UploadFile

from . import service
from .db import DocCenterDB
from .models import ALL_GRADES, FEATURES, Applicability

_API_KEY = os.environ.get("TRAINING_PLAN_API_KEY") or os.environ.get("WARNING_API_KEY", "")
_UPLOAD_DIR = Path(__file__).resolve().parents[1] / "data" / "doc_center_uploads"
_MAX_SIZE = 10 * 1024 * 1024
_ALLOWED_EXTS = {".docx", ".doc", ".pdf", ".xlsx", ".xls", ".txt", ".md"}

router = APIRouter(prefix="/api/doc-center", tags=["doc-center"])


def _check_key(x_api_key: str | None) -> None:
    if not _API_KEY or x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="无效的 API Key")


def _db() -> DocCenterDB:
    return DocCenterDB()


@router.get("/files")
def list_files(doc_type: str = "", feature: str = "", grade: str = "", major: str = "",
               status: str = "", x_api_key: str | None = Header(None)):
    _check_key(x_api_key)
    db = _db()
    try:
        files = db.list_files(doc_type=doc_type, feature=feature, grade=grade,
                              major=major, status=status)
    finally:
        db.close()
    return {"features": FEATURES, "count": len(files), "files": files}


@router.get("/file/{file_id}")
def get_file(file_id: int, x_api_key: str | None = Header(None)):
    _check_key(x_api_key)
    db = _db()
    try:
        f = db.get_file(file_id)
        if not f:
            raise HTTPException(status_code=404, detail="文件不存在")
        item = f.__dict__.copy()
        item["applicability"] = db.get_applicability(file_id=file_id)
        item["jobs"] = db.get_jobs(file_id=file_id)
    finally:
        db.close()
    return item


@router.post("/upload")
async def upload(file: UploadFile = File(...),
                 doc_type: str = Form(""),
                 subject_major: str = Form(""),
                 subject_grade: str = Form(""),
                 features: str = Form(""),     # 逗号分隔；空=用类型默认
                 grades: str = Form(""),       # 逗号分隔；空=用默认
                 majors: str = Form(""),       # 逗号分隔；空=用默认
                 uploaded_by: str = Form(""),
                 orig_name: str = Form(""),    # 微信临时名 → 原始文件名的修正
                 x_api_key: str | None = Header(None)):
    """上传一份公共教学文件（按内容 hash 去重）。

    微信 `wx.uploadFile` 发送的是临时文件名（如 `U4Sek...xlsx`），因此前端须同时传
    `orig_name`；没传时回退临时名（历史行为）。
    """
    _check_key(x_api_key)
    name = os.path.basename(file.filename or "upload.bin")
    # 展示/分类用真实文件名；落盘仍用临时名（唯一、免冲突）。
    # 展示名不参与路径拼接，仅 basename 防目录穿越即可（不额外拒绝反斜杠——
    # Linux 上 basename 不视其为分隔符，历史安全用例依赖此行为）。
    display = os.path.basename(orig_name.strip()) if orig_name.strip() else name
    if not display or display in (".", ".."):
        return {"status": "error", "message": "文件名无效"}
    ext = os.path.splitext(display)[1].lower()
    if ext not in _ALLOWED_EXTS:
        return {"status": "error", "message": f"不支持的文件类型：{ext}"}
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = _UPLOAD_DIR / name
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > _MAX_SIZE:
                    out.close()
                    dest.unlink(missing_ok=True)
                    return {"status": "error", "message": "文件超过 10MB 上限"}
                out.write(chunk)
    except Exception as exc:
        return {"status": "error", "message": f"写入失败：{exc}"}

    def _split(s: str) -> list[str]:
        return [x.strip() for x in (s or "").split(",") if x.strip()]

    res = service.ingest_file(
        str(dest), doc_type=doc_type.strip(), subject_major=subject_major.strip(),
        subject_grade=subject_grade.strip(),
        features=_split(features) or None, grades=_split(grades) or None,
        majors=_split(majors) if majors.strip() else None,
        uploaded_by=uploaded_by or "api-admin", orig_name=display)
    # 上传即触发解析（best-effort：plan/text 本进程；warning 调预警服务）
    if res.get("status") == "ok":
        try:
            res["process_plan_text"] = service.process_pending(limit=5)
        except Exception as exc:
            res["process_plan_text"] = {"error": str(exc)}
        try:
            res["process_warning"] = service.process_warning(limit=5)
        except Exception as exc:
            res["process_warning"] = {"error": str(exc)}
    return res


@router.patch("/file/{file_id}")
def update_file(file_id: int, body: dict = Body(...), x_api_key: str | None = Header(None)):
    """改元数据（类型/主年级/归属专业/标题/备注）。"""
    _check_key(x_api_key)
    db = _db()
    try:
        ok = db.update_file(file_id, **{k: str(v) for k, v in (body or {}).items()})
    finally:
        db.close()
    if not ok:
        raise HTTPException(status_code=404, detail="文件不存在或无可更新字段")
    return {"status": "ok", "id": file_id}


@router.put("/file/{file_id}/applicability")
def set_applicability(file_id: int, body: dict = Body(...),
                      x_api_key: str | None = Header(None)):
    """设置适用性：body `{features:[], grades:[], majors:[]}`（多选）。"""
    _check_key(x_api_key)
    feats = [str(x) for x in (body.get("features") or [])]
    grades = [str(x) for x in (body.get("grades") or [])] or [ALL_GRADES]
    majors = [str(x) for x in (body.get("majors") or [])] or [""]
    # 通配与具体项互斥：「全部」/「不限」存在时收敛为通配，避免同一文件命中多行
    # （resolve 会重复返回）与 UI 上的冗余勾选。
    if ALL_GRADES in grades and len(grades) > 1:
        grades = [ALL_GRADES]
    if "" in majors and len(majors) > 1:
        majors = [""]
    rows = [Applicability(file_id=file_id, feature=ft, grade=g, major=m)
            for ft in feats for g in grades for m in majors]
    db = _db()
    try:
        if not db.get_file(file_id):
            raise HTTPException(status_code=404, detail="文件不存在")
        db.set_applicability(file_id, rows)
    finally:
        db.close()
    return {"status": "ok", "id": file_id, "rows": len(rows)}


@router.post("/retry")
def retry(body: dict = Body(...), x_api_key: str | None = Header(None)):
    """重解析失败/指定 job：body `{file_id, pipeline?}`（pipeline 空=全部默认管线）。"""
    _check_key(x_api_key)
    fid = int(body.get("file_id") or 0)
    pipe = str(body.get("pipeline") or "")
    if not fid:
        raise HTTPException(status_code=400, detail="缺少 file_id")
    db = _db()
    try:
        f = db.get_file(fid)
        if not f:
            raise HTTPException(status_code=404, detail="文件不存在")
        from .models import default_for
        pipes = [pipe] if pipe else default_for(f.doc_type)["pipelines"]
        for p in pipes:
            db.ensure_job(fid, p, reset=True)
    finally:
        db.close()
    return {"status": "ok", "id": fid, "pipelines": pipes}


@router.delete("/file/{file_id}")
def delete_file(file_id: int, x_api_key: str | None = Header(None)):
    """删除文件：中心记录级联删；**同时按 output_ref 级联删各功能产物**（设计 007 §2）。"""
    _check_key(x_api_key)
    db = _db()
    try:
        f = db.get_file(file_id)
        if not f:
            raise HTTPException(status_code=404, detail="文件不存在")
        jobs = db.get_jobs(file_id=file_id)
        removed = _cascade_delete_locals(jobs)
        # 统一管理收口：同内容 hash 的 legacy plan_source_doc 及其派生政策规则一并清理
        removed["plan_source_doc"] = _cascade_delete_source_docs(f.file_hash)
        db.delete_file(file_id)
    finally:
        db.close()
    return {"status": "ok", "id": file_id, "cascaded": removed}


def _cascade_delete_locals(jobs: list[dict]) -> dict[str, int]:
    """按 output_ref 级联删除本功能库里的产物（培养方案 → plan_document）。

    复用 `TrainingPlanDB.delete_document` 的子表清单，避免与解读侧删除逻辑漂移。
    """
    out = {"plan_document": 0}
    pids = []
    for j in jobs:
        ref = j.get("output_ref") or {}
        if isinstance(ref, dict) and ref.get("plan_document_id"):
            pids.append(int(ref["plan_document_id"]))
    if not pids:
        return out
    from trainingplan.db import TrainingPlanDB
    if not os.path.isfile(service.TRAINING_PLAN_DB):
        return out
    db = TrainingPlanDB(service.TRAINING_PLAN_DB)
    try:
        for pid in pids:
            if db.delete_document(pid):
                out["plan_document"] += 1
    finally:
        db.close()
    return out


def _cascade_delete_source_docs(file_hash: str) -> int:
    """按内容 hash 清理 legacy `plan_source_doc` 及其派生政策规则。

    文件中心是统一管理面；删除中心原件时，同内容的 legacy 登记（含
    `source_doc_id` 关联的转专业/专业选择规则）也应一并移除，避免残留。
    """
    if not file_hash:
        return 0
    from trainingplan.db import TrainingPlanDB
    if not os.path.isfile(service.TRAINING_PLAN_DB):
        return 0
    db = TrainingPlanDB(service.TRAINING_PLAN_DB)
    n = 0
    try:
        ids = [int(r["id"]) for r in db.conn.execute(
            "SELECT id FROM plan_source_doc WHERE file_hash=?", (file_hash,)).fetchall()]
        for sid in ids:
            if db.delete_source_doc(sid):
                n += 1
    finally:
        db.close()
    return n


@router.get("/status")
def status(x_api_key: str | None = Header(None)):
    _check_key(x_api_key)
    db = _db()
    try:
        service.sync_plan_jobs()
        summary = db.queue_summary()
        jobs = db.get_jobs()
    finally:
        db.close()
    return {"summary": summary, "jobs": jobs}


@router.post("/process")
def process(limit: int = 20, warning: bool = False, x_api_key: str | None = Header(None)):
    """手动触发消费（调试/补跑）。`warning=true` 额外跑预警管线。"""
    _check_key(x_api_key)
    r1 = service.process_pending(limit=limit)
    r2 = service.process_warning(limit=limit) if warning else {}
    return {"plan_or_text": r1, "warning": r2}
