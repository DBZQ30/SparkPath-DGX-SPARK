"""培养方案智能解读 HTTP API（FastAPI）。

挂载到 agent4som 现有服务（与学业预警同进程、不同路由）：
  /api/plan/*

鉴权：沿用 007 的 `X-API-Key`（`TRAINING_PLAN_API_KEY` 或复用 `WARNING_API_KEY`），
内网部署。GET 类不含 guest（身份校验）；写类仅 admin/owner。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from . import service
from .db import TrainingPlanDB
from .models import STANDARD_MAJORS

logger = logging.getLogger("trainingplan.api")

_API_KEY = os.environ.get("TRAINING_PLAN_API_KEY") or os.environ.get("WARNING_API_KEY", "")
# 图片短期签名密钥：优先专用密钥，其次 API Key；都未配置时用**进程内随机值**
# （不可预测，避免硬编码兜底被用于绕过 header 鉴权；重启后旧 token 自然失效）。
_IMAGE_SECRET = (os.environ.get("TRAINING_PLAN_IMAGE_SECRET") or _API_KEY
                 or secrets.token_hex(32)).encode()
_IMAGE_TOKEN_TTL = int(os.environ.get("TRAINING_PLAN_IMAGE_TTL", "600"))  # 秒
_UPLOAD_DIR = Path(__file__).resolve().parents[1] / "data" / "training_plan_uploads"
_FRESH_MAX = 10 * 1024 * 1024


def _check_key(x_api_key: str | None) -> None:
    if not _API_KEY or x_api_key != _API_KEY:
        # 仅记录哈希/长度，便于排查 key 轮换或多构建不一致；不落明文
        got = hashlib.sha256((x_api_key or "").encode()).hexdigest()[:12]
        exp = hashlib.sha256(_API_KEY.encode()).hexdigest()[:12]
        logger.warning(
            "API key mismatch: got=%s(len=%d) expected=%s(len=%d)",
            got, len(x_api_key or ""), exp, len(_API_KEY))
        raise HTTPException(status_code=401, detail="无效的 API Key")


def _sign_image(major: str, entry_year: str, exp: int) -> str:
    msg = f"{major}|{entry_year}|{exp}".encode()
    return base64.urlsafe_b64encode(
        hmac.new(_IMAGE_SECRET, msg, hashlib.sha256).digest()
    ).decode().rstrip("=")


def _image_path(major: str, entry_year: str) -> str:
    """生成带短期签名的图片 URL（相对路径）。

    为什么需要：小程序 `<image src>` **无法携带自定义 header**，不能传 X-API-Key。
    故用 HMAC 短期签名（默认 10 分钟）替代 header 鉴权，随 prereq 响应下发。
    """
    exp = int(time.time()) + _IMAGE_TOKEN_TTL
    from urllib.parse import quote
    return (f"/api/plan/prereq-image?major={quote(major)}&entry_year={quote(entry_year)}"
            f"&exp={exp}&token={_sign_image(major, entry_year, exp)}")


def _verify_image_token(major: str, entry_year: str, exp: str, token: str) -> bool:
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < time.time():
        return False
    return hmac.compare_digest(_sign_image(major, entry_year, exp_i), token or "")


def _db() -> TrainingPlanDB:
    return TrainingPlanDB(service.DB_PATH)


@asynccontextmanager
async def lifespan(_: FastAPI):
    service.start_worker()
    yield


app = FastAPI(title="training-plan-interpretation", lifespan=lifespan)

# 文件中心（设计 007）：作为同一服务下的 /api/doc-center/* 路由
try:
    from doccenter.api import router as _doc_center_router
    app.include_router(_doc_center_router)
except Exception:  # 文件中心未就绪时不阻塞解读/规划
    logger.warning("文件中心路由挂载失败（doccenter.api 不可用），跳过", exc_info=True)

# 设备算力指标（执行轨迹遥测）：/api/metrics/gpu
# 放在本服务而非 hermes 网关：网关单元 PrivateDevices=yes，看不到 nvidia 设备
try:
    from gpu_metrics import router as _gpu_metrics_router
    app.include_router(_gpu_metrics_router)
except Exception:  # 指标不可用时不阻塞解读/规划
    logger.warning("GPU 指标路由挂载失败（gpu_metrics 不可用），跳过", exc_info=True)


# ─────────────────────────── 只读：学生/教师/管理员 ───────────────────────────

@app.get("/api/plan/majors")
def majors(x_api_key: str | None = Header(None)):
    _check_key(x_api_key)
    db = _db()
    try:
        items = db.list_major_years()
    finally:
        db.close()
    return {"majors": STANDARD_MAJORS, "plans": items}


@app.get("/api/plan/overview")
def overview(major: str = "", entry_year: str = "", x_api_key: str | None = Header(None)):
    _check_key(x_api_key)
    result = service.interpret(major, entry_year)
    if result.get("state") != "done":
        return result
    result.pop("db", None)
    return result


@app.get("/api/plan/semester-map")
def semester_map(major: str = "", entry_year: str = "", x_api_key: str | None = Header(None)):
    _check_key(x_api_key)
    result = service.interpret(major, entry_year)
    if result.get("state") != "done":
        return result
    return {"major": result["major"], "entry_year": result["entry_year"],
            "semester_map": result["semester_map"]}


@app.get("/api/plan/courses")
def courses(major: str = "", entry_year: str = "", semester: str = "",
            category: str = "", x_api_key: str | None = Header(None)):
    _check_key(x_api_key)
    db = _db()
    try:
        major_n = service.normalize_major(major)
        year_n = service.normalize_entry_year(entry_year)
        if not major_n or not year_n:
            return {"state": "bad", "message": "专业或年级无法识别"}
        # 与 interpret/route 同口径：按 applies_to 解析（精确年级优先，其次「全部」），
        # 否则方案设为「全部」后，其它年级能看正文却查不到课程/先修。
        doc = service.find_document(db, major_n, year_n)
        if not doc or doc.parsed_status != "done":
            return {"state": "unavailable", "message": "该方案尚未收录或未解析完成"}
        rows = db.get_courses(doc.id, semester, category)
    finally:
        db.close()
    return {"major": major_n, "entry_year": year_n, "count": len(rows), "courses": rows}


@app.get("/api/plan/prereq")
def prereq(major: str = "", entry_year: str = "", include_unverified: int = 0,
           x_api_key: str | None = Header(None)):
    _check_key(x_api_key)
    db = _db()
    try:
        major_n = service.normalize_major(major)
        year_n = service.normalize_entry_year(entry_year)
        if not major_n or not year_n:
            return {"state": "bad", "message": "专业或年级无法识别"}
        # 同 interpret/route：applies_to 解析，「全部」可惠及其它年级。
        doc = service.find_document(db, major_n, year_n)
        if not doc or doc.parsed_status != "done":
            return {"state": "unavailable", "message": "该方案尚未收录或未解析完成"}
        edges = db.get_prereq_edges(doc.id, verified_only=not include_unverified)
        img = (doc.in_file_meta or {}).get("prereq_image", "")
        verified_n = len(db.get_prereq_edges(doc.id, verified_only=True))
    finally:
        db.close()
    return {"major": major_n, "entry_year": year_n, "count": len(edges),
            "verified_count": verified_n,
            # 带签名的图片相对路径（小程序 <image> 无法带 header，故用短期签名）
            "image_path": _image_path(major_n, year_n) if img and os.path.isfile(img) else "",
            "edges": edges}


@app.get("/api/plan/prereq-image")
def prereq_image(major: str = "", entry_year: str = "",
                 exp: str = Query("", description="签名过期时间戳"),
                 token: str = Query("", description="HMAC 签名"),
                 x_api_key: str | None = Header(None)):
    """先修关系原图。

    鉴权二选一：`X-API-Key` header（接口调用）或 `exp`+`token` 短期签名
    （小程序 `<image>` 无法带 header，用 `_image_path` 下发的签名 URL）。
    """
    major_n = service.normalize_major(major)
    year_n = service.normalize_entry_year(entry_year)
    if not (major_n and year_n):
        raise HTTPException(status_code=400, detail="专业或年级无法识别")
    if not _verify_image_token(major_n, year_n, exp, token):
        _check_key(x_api_key)   # 无有效签名则回退 header 鉴权（无 key → 401）
    db = _db()
    try:
        # 与 prereq 同口径：按 applies_to 解析，保证签名 URL 指向同一份方案。
        doc = service.find_document(db, major_n, year_n)
        img = (doc.in_file_meta or {}).get("prereq_image", "") if doc else ""
    finally:
        db.close()
    if not img or not os.path.isfile(img):
        raise HTTPException(status_code=404, detail="先修关系图不存在")
    return FileResponse(img, media_type="image/png")


@app.get("/api/plan/status")
def status(major: str = "", entry_year: str = "", x_api_key: str | None = Header(None)):
    _check_key(x_api_key)
    db = _db()
    try:
        summary = db.queue_summary()
        items = db.queue_items(major, entry_year)
    finally:
        db.close()
    return {"summary": summary, "items": items}


# ─────────────────────────── 写：仅 admin/owner ───────────────────────────

@app.post("/api/plan/upload")
async def upload(file: UploadFile = File(...),
                 major: str = Form(""), entry_year: str = Form(""),
                 orig_name: str = Form(""), x_api_key: str | None = Header(None)):
    _check_key(x_api_key)
    name = os.path.basename(file.filename or "")
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return {"status": "rejected", "message": "文件名无效"}
    if not name.lower().endswith(".docx"):
        return {"status": "rejected", "message": "仅支持 .docx 培养方案"}
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = _UPLOAD_DIR / name
    size = 0
    with dest.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > _FRESH_MAX:
                fh.close()
                dest.unlink(missing_ok=True)
                return {"status": "rejected", "message": "文件超过 10MB 上限"}
            fh.write(chunk)
    return service.submit_upload(str(dest), major=major, entry_year=entry_year,
                                 uploader="api-admin", orig_name=orig_name or name)


@app.post("/api/plan/retry")
def retry(doc_id: int = Body(..., embed=True), x_api_key: str | None = Header(None)):
    _check_key(x_api_key)
    return service.retry_document(doc_id)


@app.post("/api/plan/prereq/verify")
def prereq_verify(edges: list = Body(..., embed=True),
                  plan_id: int = Body(0, embed=True),
                  major: str = Body("", embed=True),
                  entry_year: str = Body("", embed=True),
                  x_api_key: str | None = Header(None)):
    """提交人工校对结果。

    定位方案：优先 `plan_id`；否则按 `major + entry_year` 取当前激活方案
    （管理员页从列表点进来时只有专业/年级，避免前端多传一次 id）。
    """
    _check_key(x_api_key)
    db = _db()
    try:
        target_id = plan_id
        if not target_id:
            major_n = service.normalize_major(major)
            year_n = service.normalize_entry_year(entry_year)
            if not major_n or not year_n:
                raise HTTPException(status_code=400, detail="需提供 plan_id 或 major+entry_year")
            doc = db.get_active_document(major_n, year_n)
            if not doc:
                raise HTTPException(status_code=404, detail="方案不存在")
            target_id = doc.id
        n = db.set_prereq_verified(target_id, edges, verifier="api-admin")
    finally:
        db.close()
    return {"status": "ok", "plan_id": target_id, "applied": n}


# ─────────────── 学业规划：路线图 / 对比 / 专业选择 / 转专业（只读） ───────────────

@app.get("/api/plan/route/options")
def route_options(major: str = "", entry_year: str = "", x_api_key: str | None = Header(None)):
    """四方向可选项 + 各方向规则摘要。"""
    _check_key(x_api_key)
    r = service.modes(major, entry_year)
    if r.get("state") != "done":
        return r
    return {"state": "done", "major": r["major"], "entry_year": r["entry_year"],
            "modes": r["modes"], "rules": r["rules"], "scopes": r["scopes"],
            "courses": r["courses"]}


@app.get("/api/plan/route")
def route_map(major: str = "", entry_year: str = "", mode: str = "常规型",
              student_id: str = "", x_api_key: str | None = Header(None)):
    """四年路线图（逐学期课程/学分/负荷/节奏节点）。"""
    _check_key(x_api_key)
    from . import route as route_mod
    return route_mod.build_route(major, entry_year, mode, student_id)


@app.get("/api/plan/route/compare")
def route_compare(major: str = "", entry_year: str = "",
                  x_api_key: str | None = Header(None)):
    """四方向横向对比。"""
    _check_key(x_api_key)
    from . import route as route_mod
    return route_mod.compare_modes(major, entry_year)


@app.get("/api/plan/select/simulate")
def select_simulate(entry_year: str = "", student_id: str = "", choices: str = "",
                    x_api_key: str | None = Header(None)):
    """专业选择（分流）模拟。"""
    _check_key(x_api_key)
    from . import simulate as simulate_mod
    ch = [c for c in choices.split(",") if c] if choices else None
    return simulate_mod.simulate_major_selection(entry_year, student_id, ch)


@app.get("/api/plan/simulate/transfer")
def simulate_transfer(from_major: str = "", to_major: str = "", entry_year: str = "",
                      student_id: str = "", x_api_key: str | None = Header(None)):
    """转专业模拟（可抵扣/需补修/压力/红线/考核）。"""
    _check_key(x_api_key)
    from . import simulate as simulate_mod
    return simulate_mod.simulate_transfer(from_major, to_major, entry_year, student_id)


@app.get("/api/plan/simulate/minor")
def simulate_minor(major: str = "", minor: str = "", entry_year: str = "",
                   student_id: str = "", x_api_key: str | None = Header(None)):
    """辅修模拟（预留）。"""
    _check_key(x_api_key)
    from . import simulate as simulate_mod
    return simulate_mod.simulate_minor(major, minor, entry_year, student_id)
