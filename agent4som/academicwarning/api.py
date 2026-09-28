"""选课合理性检查 HTTP API（小程序入口，007 v1.8 方向重构）。

- 鉴权：X-API-Key header（.env 的 WARNING_API_KEY）
- 类型强校验：文件内信息+文件名识别 ≠ type_hint → 拒绝（防传错）；
  无法识别（ValueError）→ 信任模块类型
- 上传异步化：落盘 + 判重后立即返回（parsed_status=parsing），解析入库在
  后台线程执行（_OP_LOCK 内，与企微路径同锁），客户端轮询 /status 取结果；
  选课结果上传后自动执行选课合理性检查
- 选课检查与解析共用 _OP_LOCK（L7：防半入库数据被计算），API Key 即管理员凭证
"""
import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .db import WarningDB
from .models import SourceFile
from .service import (FILE_TYPES, STANDARD_MAJORS, _OP_LOCK, TYPE_NAME,
                      _delete_impl, _file_md5, _upload_impl,
                      _assemble_check_prep, dedup_check, detect_file_type,
                      run_selection_check, selection_main_grade,
                      confirm_grade_semester, grade_covered_semester)
from .parsers import (canonical_major, clean_course_name, detect_major, grade_band,
                      normalize_major, parse_selection, parse_roster,
                      representative_by_course)
from .selection_check import _grade_category

logger = logging.getLogger("academicwarning.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 首次启动自动初始化 schema（幂等：CREATE IF NOT EXISTS + ALTER 补列）
    db = WarningDB()
    try:
        db.init_schema()
        # v1.10：清理上次进程遗留的 queued 占位行（解析被中断）→ 标记 failed。
        # 单 worker 部署（uvicorn --workers 1），重启即代表解析已中断，安全。
        stale = db.fail_stale_queued()
        if stale:
            logger.info("启动清理遗留占位行 %s 条 → failed", stale)
    finally:
        db.close()
    yield


app = FastAPI(title="学业预警 API", lifespan=lifespan)

_API_KEY = os.environ.get("WARNING_API_KEY", "")
_UPLOAD_DIR = Path(__file__).resolve().parents[1] / "data" / "warning_uploads"
_MAX_FILE_SIZE = 10 * 1024 * 1024  # L6：>10MB 拒绝

# 中文模块类型 → 英文 file_type（与 service.TYPE_NAME 对应）
TYPE_HINTS = {"培养方案": "plan", "选课结果": "selection", "成绩单": "grade",
              "学籍名单": "roster", "通识课程信息表": "gen_ed"}


def _check_key(x_api_key: str | None) -> None:
    if not _API_KEY or x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="无效的 API Key")


def _detect_strict(path: str) -> str | None:
    """类型强校验信号：文件内信息+文件名识别（不含 hint 级）。
    无法识别（ValueError）返回 None → 信任模块类型。"""
    try:
        return detect_file_type(path, chat_hint="")
    except ValueError:
        return None


def _parse_in_background(path: str, chat_hint: str, force_type: str,
                         major_hint: str | None = None,
                         orig_name: str | None = None,
                         grade_hint: str = "",
                         queued_sf_id: int | None = None) -> None:
    """后台解析入库（daemon 线程，请求不等待 OCR 等慢解析）。

    - _OP_LOCK 内执行 _upload_impl（与企微路径同锁，L7 串行语义）
    - 入库后把提示文本（如"成绩单入库：…（OCR 失败 N 份）"）写回最新记录
      in_file_meta.note，供前端轮询 /status 展示
    - queued_sf_id（v1.10）：调用方已插占位行 → 传入使其原地收尾，note 按 id 精确定位
    """
    def _run() -> None:
        try:
            with _OP_LOCK:
                reply = _upload_impl(path, chat_hint=chat_hint,
                                     uploader="api-admin", platform="api",
                                     force_type=force_type,
                                     major_hint=major_hint,
                                     orig_name=orig_name,
                                     grade_hint=grade_hint,
                                     queued_sf_id=queued_sf_id)
            db = WarningDB()
            try:
                # v1.7：多专业后须按 major 定位本次记录（入库 major = 识别 or hint 兜底）
                # v1.10：占位行按 id 精确定位（优先，避免按 major 取错历史行）
                if queued_sf_id is not None:
                    row = db.conn.execute(
                        "SELECT id, in_file_meta FROM source_file WHERE id=?",
                        (queued_sf_id,)).fetchone()
                elif major_hint:
                    row = db.conn.execute(
                        "SELECT id, in_file_meta FROM source_file WHERE file_type=?"
                        " AND json_extract(in_file_meta, '$.major')=? ORDER BY id DESC LIMIT 1",
                        (force_type, major_hint)).fetchone()
                else:
                    row = db.conn.execute(
                        "SELECT id, in_file_meta FROM source_file WHERE file_type=?"
                        " ORDER BY id DESC LIMIT 1", (force_type,)).fetchone()
                if row:
                    meta = json.loads(row[1] or "{}")
                    meta["note"] = reply
                    db.conn.execute("UPDATE source_file SET in_file_meta=? WHERE id=?",
                                    (json.dumps(meta, ensure_ascii=False), row[0]))
                    db.conn.commit()
            finally:
                db.close()
        except Exception as exc:   # 线程兜底：仅记录（记录缺失 → 状态区显示未上传，可重传）
            logger.warning("后台解析异常: %s: %s", type(exc).__name__, exc, exc_info=True)
    threading.Thread(target=_run, daemon=True).start()


async def _write_upload(file: UploadFile, dest: Path) -> dict | None:
    """落盘上传文件（1MB 分块流式写）。超过 _MAX_FILE_SIZE → 删除临时文件并
    返回拒绝响应；成功返回 None。"""
    size = 0
    with dest.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > _MAX_FILE_SIZE:
                f.close()
                dest.unlink(missing_ok=True)
                return {"message": "文件超过 10MB 上限，已拒绝", "file_type": "",
                        "parsed_status": "rejected", "upload_time": ""}
            f.write(chunk)
    return None


def _check_type_mismatch(dest: Path, type_hint: str, force_type: str) -> dict | None:
    """类型强校验：模块为准 + 防传错（识别结果与所选模块不符 → 拒绝并删临时文件）。"""
    detected = _detect_strict(str(dest))
    if detected and detected != force_type:
        dest.unlink(missing_ok=True)
        return {
            "message": (f"文件类型与所选模块不匹配：模块={type_hint}，"
                        f"文件识别={TYPE_NAME.get(detected, detected)}，请确认是否传错"),
            "file_type": detected, "major": "",
            "parsed_status": "rejected", "upload_time": "",
        }
    return None


def _resolve_grade_major(dest: Path, force_type: str, orig: str, name: str,
                         major_hint: str, now: str) -> tuple[str, dict | None]:
    """v1.9 成绩单专业必填：专业名取"原始文件名"或"所选专业"。
    返回 (grade_major, 拒绝响应)。非 grade 类型 → ("", None)；两者都识别不出
    → ("", 拒绝响应)——无法归档到正确分区（微信临时 hash 名不入库，避免污染
    分区且挡掉后续正确上传）。"""
    if force_type != "grade":
        return "", None
    grade_major = canonical_major(os.path.basename(orig or name)) or major_hint or ""
    if not grade_major:
        dest.unlink(missing_ok=True)
        return "", {"message": "无法识别成绩单所属专业，请在页面上选择专业后重传",
                    "file_type": "grade", "major": "", "parsed_status": "rejected",
                    "upload_time": now}
    return grade_major, None


def _check_major_mismatch(dest: Path, force_type: str, orig: str,
                          name: str, major_hint: str, now: str) -> dict | None:
    """v1.7 专业强校验：明确识别为其他专业 → 拒绝（无法识别 → 信任所选专业）。
    v1.9：识别用原始文件名（orig）——落盘名是微信临时 hash 名，识别不出专业。"""
    if not major_hint or force_type not in ("plan", "grade"):
        return None
    detected_major = detect_major(str(dest), force_type, orig or name)
    if detected_major and detected_major != major_hint:
        dest.unlink(missing_ok=True)
        return {
            "message": (f"文件识别为「{detected_major}」，与所选专业「{major_hint}」"
                        f"不匹配，请确认是否传错"),
            "file_type": force_type, "major": major_hint,
            "parsed_status": "rejected", "upload_time": now,
        }
    return None


def _check_grade_mismatch(dest: Path, force_type: str, orig: str, name: str,
                          major_hint: str, grade_hint: str, now: str) -> dict | None:
    """v6 年级强校验：主年级/文件名不符 → 拒绝（防传错）；grade_hint 为空 = 旧客户端
    未选年级，跳过（兼容既有行为）；selection/roster 对文件重复解析一次（可接受）。"""
    if not grade_hint:
        return None
    if force_type in ("selection", "roster"):
        try:
            if force_type == "selection":
                _, sel_rows, _ = parse_selection(str(dest))
                main_grade = selection_main_grade(sel_rows)
            else:   # roster：文件"年级"列众数（parse_roster meta）
                rmeta, _ = parse_roster(str(dest))
                main_grade = rmeta.get("grade", "")
            if main_grade and main_grade != grade_hint:
                dest.unlink(missing_ok=True)
                return {"message": (f"文件学生主年级为「{main_grade}」，与所选年级"
                                    f"「{grade_hint}」不匹配，请确认是否传错"),
                        "file_type": force_type, "major": major_hint,
                        "parsed_status": "rejected", "upload_time": now}
        except Exception:
            pass   # 解析失败交给后台（_upload_impl 有兜底）
        return None
    m = re.search(r"(20\d\d)\s*级", orig or name)
    if m and f"{m.group(1)}级" != grade_hint:
        dest.unlink(missing_ok=True)
        return {"message": (f"文件名年级为「{m.group(1)}级」，与所选年级「{grade_hint}」"
                            f"不匹配，请确认是否传错"),
                "file_type": force_type, "major": major_hint,
                "parsed_status": "rejected", "upload_time": now}
    return None


def _validate_upload_form(grade_hint: str, type_hint: str, orig_name: str,
                          filename: str | None, now: str) \
        -> tuple[dict | None, str, str, str]:
    """表单边界校验：年级格式 / 类型 hint / 原始文件名与落盘名（防穿越）。
    返回 (拒绝响应|None, 清洗后 orig, 落盘 name, force_type)。"""
    if grade_hint and not re.fullmatch(r"20\d\d级", grade_hint):
        return {"message": f"年级格式无效: {grade_hint}（应为 2023级 等）",
                "file_type": "", "parsed_status": "rejected", "upload_time": ""}, \
            "", "", ""
    # v1.8：原始文件名同样做边界清洗（外部输入）；无效则回落盘名
    orig = os.path.basename(orig_name or "")
    if not orig or orig in (".", "..") or "/" in orig or "\\" in orig:
        orig = ""
    if type_hint not in TYPE_HINTS:
        return {"message": f"未知文件类型: {type_hint}", "file_type": "",
                "parsed_status": "rejected", "upload_time": ""}, orig, "", ""
    # 文件名边界校验（外部输入）：去路径分隔符并排除空/点，防路径穿越
    name = os.path.basename(filename or "")
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return {"message": "文件名无效，请重试", "file_type": "",
                "parsed_status": "rejected", "upload_time": now}, orig, name, ""
    return None, orig, name, TYPE_HINTS[type_hint]


def _dedup_and_queue(dest: Path, force_type: str, grade_hint: str,
                     grade_major: str, major_hint: str, orig: str, name: str,
                     now: str) -> tuple[dict | None, int]:
    """M2 判重前置（异步化后防止重复解析）+ v1.10 上传即占位。

    命中判重 → (done 响应, 0)，不启后台任务；未命中 → 插入 queued 占位记录
    （解析未完成的数分钟内 /status 即可见"解析中"），解析完成后由
    _upload_impl 原地 update 为 done/failed（不新增第二条）。"""
    db = WarningDB()
    try:
        dup = dedup_check(db, _file_md5(str(dest)), force_type, grade_hint,
                          grade_major if force_type == "grade" else "")
        if dup is not None:
            return {"message": dup, "file_type": force_type, "major": major_hint,
                    "grade": grade_hint, "file_name": orig or name,
                    "parsed_status": "done", "upload_time": now}, 0
        sf_grade = "" if force_type == "gen_ed" else grade_hint
        major_for_meta = (grade_major if force_type == "grade" else major_hint) or ""
        queued_id = db.insert_queued_source_file(SourceFile(
            file_type=force_type, file_name=orig or name, file_hash="",
            file_path=str(dest), upload_time=now, uploader="api-admin",
            in_file_meta={"major": major_for_meta} if major_for_meta else {},
            grade=sf_grade))
        return None, queued_id
    finally:
        db.close()


@app.post("/api/warning/upload")
async def upload(file: UploadFile = File(...),
                 type_hint: str = Form(...),
                 grade_hint: str = Form(""),
                 major_hint: str = Form(""),
                 orig_name: str = Form(""),
                 x_api_key: str | None = Header(None)):
    """上传数据文件（单文件；异步解析：落盘 + 判重后立即返回，解析后台执行）。

    响应 parsed_status：rejected（校验拒绝，含 message）/ done（判重命中，文件已在库）
    / parsing（已接收，解析入库中，客户端轮询 /status）。
    v1.7 专业分区：major_hint 非空且文件明确识别为其他专业 → 同步拒绝（防传错）。
    v1.8：orig_name 为原始文件名（微信上传发送临时 hash 名），入库 file_name 用原名，
    响应回传 file_name 供前端轮询精确匹配。
    v6 年级分区：grade_hint 非空时校验格式（应为 2023级 等）+ 主年级/文件名比对，
    不符 → 同步拒绝（防传错）；响应回传 grade 供前端回显。

    校验链（各步不符即拒绝并删除临时文件）：表单参数 → 文件名/大小 → 类型
    强校验（_check_type_mismatch）→ 成绩单专业必填（_check_grade_major）→
    专业/年级强校验（_check_major_mismatch / _check_grade_mismatch）→
    M2 判重 → v1.10 queued 占位（_dedup_and_queue）→ 后台解析。"""
    _check_key(x_api_key)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    major_hint = (major_hint or "").strip()
    grade_hint = (grade_hint or "").strip()
    err, orig, name, force_type = _validate_upload_form(
        grade_hint, type_hint, orig_name, file.filename, now)
    if err is not None:
        return err
    Path(_UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    dest = Path(_UPLOAD_DIR) / name
    too_large = await _write_upload(file, dest)
    if too_large is not None:
        return too_large

    err = _check_type_mismatch(dest, type_hint, force_type)
    if err is not None:
        return err
    grade_major, err = _resolve_grade_major(dest, force_type, orig, name,
                                            major_hint, now)
    if err is not None:
        return err
    err = _check_major_mismatch(dest, force_type, orig, name, major_hint, now)
    if err is not None:
        return err
    err = _check_grade_mismatch(dest, force_type, orig, name, major_hint,
                                grade_hint, now)
    if err is not None:
        return err

    # M2 判重前置（异步化后防止重复解析）；命中直接返回，不启后台任务
    # v6 年级分区：带 grade_hint（空 = 旧客户端不带条件，跨年级同内容可重复注册）
    # v1.9：grade 追加专业维度（专业名同上，取自原始文件名/所选专业）
    # v1.10 上传即占位：全部校验/判重通过后，立即插一条 queued 记录——
    # 解析未完成的数分钟内 /status 即可见（"解析中"），而非"未上传"。
    # 解析完成后由 _upload_impl 原地 update 为 done/failed（不新增第二条）。
    dup_or_none, queued_id = _dedup_and_queue(
        dest, force_type, grade_hint, grade_major, major_hint, orig, name, now)
    if dup_or_none is not None:
        return dup_or_none

    # 落盘完成 → 后台线程解析入库（_OP_LOCK 串行，与企微路径同语义）
    _parse_in_background(str(dest), type_hint, force_type, major_hint or None,
                         orig or None, grade_hint, queued_sf_id=queued_id)
    return {"message": "文件已接收，正在解析入库，请稍候刷新",
            "file_type": force_type, "major": major_hint,
            "grade": grade_hint, "file_name": orig or name,
            "parsed_status": "parsing", "upload_time": now}

@app.post("/api/warning/delete")
def delete_file(file_type: str = Body(..., embed=True),
                major: str | None = Body(None, embed=True),
                grade: str | None = Body(None, embed=True),
                x_api_key: str | None = Header(None)):
    """删除上传文件（v1.7 与上传分区一一对应；v6 分区粒度 年级×专业）。

    plan/grade 必传 major：物理删除该专业全部记录（含历史，不可恢复）+ plan 级联，
    其他专业不受影响；grade 非空时仅删该年级分区（plan 级联同步限该年级），
    为空 = 全删（兼容旧调用）；其余类型整类删除。删除后该分区回"未上传"。"""
    _check_key(x_api_key)
    if file_type not in FILE_TYPES:
        return {"message": f"未知文件类型: {file_type}"}
    if file_type in ("plan", "grade") and not major:
        return {"message": f"{TYPE_NAME[file_type]}按专业删除，缺少 major 参数"}
    with _OP_LOCK:
        msg = _delete_impl(file_type, major or None, grade or None)
    return {"message": msg}


@app.post("/api/warning/grade")
def add_grade(name: str = Body(..., embed=True),
              x_api_key: str | None = Header(None)):
    """管理员添加年级（前端 tab 数据源；重名/格式错返回 ok=false）。

    add_grade 底层 INSERT OR IGNORE 重复静默，故重复检测在此层做。"""
    _check_key(x_api_key)
    name = (name or "").strip()
    if not re.fullmatch(r"20\d\d级", name):
        return {"ok": False, "message": "年级格式无效（应为 2023级 等）"}
    db = WarningDB()
    try:
        if name in db.list_grades():
            return {"ok": False, "message": f"年级 {name} 已存在"}
        db.add_grade(name)
    finally:
        db.close()
    return {"ok": True, "message": f"已添加年级 {name}"}


@app.get("/api/warning/grades")
def grades(x_api_key: str | None = Header(None)):
    """年级注册表（统一事实来源，供各功能页与小程序选项读取）。"""
    _check_key(x_api_key)
    db = WarningDB()
    try:
        return {"grades": db.list_grades()}
    finally:
        db.close()


@app.delete("/api/warning/grade")
def delete_grade(name: str = "", x_api_key: str | None = Header(None)):
    """管理员删除年级（毕业年级滚出）。

    仅从注册表移除，历史分区数据保留；删除后所有读取注册表的选项同步消失。"""
    _check_key(x_api_key)
    name = (name or "").strip()
    if not re.fullmatch(r"20\d\d级", name):
        return {"ok": False, "message": "年级格式无效（应为 2023级 等）"}
    db = WarningDB()
    try:
        ok = db.delete_grade(name)
    finally:
        db.close()
    return {"ok": ok, "message": f"已删除年级 {name}" if ok else f"年级 {name} 不存在"}


@app.put("/api/warning/grade")
def update_grade(name: str = Body(...), current_semester: str = Body(...),
                 x_api_key: str | None = Header(None)):
    """设置年级当前学期（11 个合法值；空 = 恢复文件推断）。"""
    _check_key(x_api_key)
    from .service import SEMESTERS
    if current_semester and current_semester not in SEMESTERS:
        return {"ok": False, "message": f"学期无效（可选：{'、'.join(SEMESTERS)}）"}
    db = WarningDB()
    try:
        if name not in db.list_grades():
            return {"ok": False, "message": f"年级 {name} 不存在（请先添加）"}
        db.set_grade_semester(name, current_semester)
    finally:
        db.close()
    return {"ok": True, "message": f"{name} 当前学期已设为 {current_semester or '（自动推断）'}"}


@app.post("/api/warning/grade/confirm")
def confirm_grade_sem(name: str = Body(...), sem: str = Body(...),
                      x_api_key: str | None = Header(None)):
    """人工确认成绩覆盖学期（校验成绩单确有该学期整批记录）。"""
    _check_key(x_api_key)
    db = WarningDB()
    try:
        ok, msg = confirm_grade_semester(db, name, sem)
    finally:
        db.close()
    return {"ok": ok, "message": msg}


@app.get("/api/warning/selection-check")
def selection_check(grade: str = "", x_api_key: str | None = Header(None)):
    """选课合理性检查结果（2026-08-31 方向重构；v6 按年级分区）：最新选课文件
    对应的名单。"""
    _check_key(x_api_key)
    db = WarningDB()
    try:
        sf_id, checked_at, rows = db.latest_selection_check(grade)
        sel = db.conn.execute(
            "SELECT in_file_meta FROM source_file WHERE id=?",
            (sf_id,)).fetchone() if sf_id else None
        meta = json.loads(sel[0] or "{}") if sel else {}
        # v6.3：年级学期状态（前端状态行数据源；无年级 = 旧客户端 → 空值）
        cur, confirm = db.get_grade_semesters(grade or "")
        auto, covered = grade_covered_semester(db, grade) if grade else ("", "")
        return {
            "grade": grade,
            "current_semester": cur, "covered_sem": covered,
            "covered_auto": auto, "confirm_sem": confirm,
            "source_file_id": sf_id,
            "semester_label": meta.get("semester_label", ""),
            "checked_at": checked_at,
            "summary": _check_summary(rows),
            "exempt_courses": meta.get("exempt_courses", {}),
            # v6.5：名单内未选课学生（run 时随批次落 meta；未跑过检查 → 空）
            "not_selected": meta.get("not_selected", []),
            "not_selected_count": len(meta.get("not_selected", [])),
            "students": [{
                "student_id": r.student_id, "name": r.name, "major": r.major,
                "class_name": r.class_name, "category": r.category,
                "expected_credit": r.expected_credit,
                "gained_credit": r.gained_credit, "selected_credit": r.selected_credit,
                "gap": r.gap, "message": r.message,
                # v7：结构化触发明细（缺修课含 code + 类别差额逐条；解析后 JSON）
                "details": json.loads(r.details or "{}"),
            } for r in rows],
        }
    finally:
        db.close()


def _check_summary(rows) -> str:
    """名单摘要（无检查记录时给空态文案）。"""
    if not rows:
        return ""
    from collections import Counter
    by_major = Counter(r.major for r in rows)
    parts = "、".join(f"{m} {by_major.get(m, 0)} 人" for m in by_major)
    return f"选课检查：选课不合理 {len(rows)} 人（{parts}）"


@app.post("/api/warning/selection-check/run")
def selection_check_run(grade: str = Body("", embed=True),
                        x_api_key: str | None = Header(None)):
    """手动重跑选课合理性检查（v6 带年级；上传后自动检查之外的重跑入口——
    先传选课、后补传成绩单/方案时的修正手段）。"""
    _check_key(x_api_key)
    with _OP_LOCK:
        n, note = run_selection_check(grade=grade)
    db = WarningDB()
    try:
        sf_id, checked_at, _ = db.latest_selection_check(grade)
    finally:
        db.close()
    return {"message": note, "source_file_id": sf_id, "checked_at": checked_at,
            "count": n}


@app.get("/api/warning/selection-check/export")
def selection_check_export(grade: str = "", x_api_key: str | None = Header(None)):
    """下载该年级最新检查报告 xlsx（无报告 → 404）。"""
    _check_key(x_api_key)
    if grade and not re.fullmatch(r"20\d\d级", grade):
        raise HTTPException(404, "年级参数无效")
    out_dir = Path(__file__).resolve().parent / "result"   # 报告落盘目录（export.OUT_DIR）
    pattern = f"选课检查名单-{grade}-*.xlsx" if grade else "选课检查名单-*.xlsx"
    files = sorted(out_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise HTTPException(status_code=404, detail="暂无导出的检查报告")
    return FileResponse(files[0], filename=files[0].name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _status_item(major: str | None, sf) -> dict:
    """状态行（v1.7）：占位行（无记录）major 外其余为 None。
    2026-09-24 去掉「有效期」概念：不再返回 fresh_days / fresh。"""
    return {
        "major": major,
        "id": sf.id if sf else None,
        "file_name": sf.file_name if sf else None,
        "upload_time": sf.upload_time if sf else None,
        "parsed_status": sf.parsed_status if sf else None,
        "note": (sf.in_file_meta or {}).get("note") if sf else None,
        "error": (sf.in_file_meta or {}).get("error") if sf else None,
    }


@app.get("/api/warning/status")
def status(grade: str = "", x_api_key: str | None = Header(None)):
    """五类文件上传状态（v1.7 分组嵌套；v6 按年级分区）。

    plan/grade：items = 标准专业 4 行（无记录为占位，major+其余 null）+ 非标准
    major 记录（历史脏数据，原样展示）；其余类型 items 1 行（major=null）。
    grade 非空时仅展示该年级分区文件，空 = 全部（兼容旧客户端）。"""
    _check_key(x_api_key)
    db = WarningDB()
    try:
        from .service import FILE_TYPES, TYPE_NAME
        files = []
        for ft in FILE_TYPES:
            if ft in ("plan", "grade"):
                by_major = db.latest_source_files_by_major(ft, grade)
                items = [_status_item(m, by_major.get(m))
                         for m in STANDARD_MAJORS]
                items += [_status_item(m, sf)
                          for m, sf in by_major.items() if m not in STANDARD_MAJORS]
                files.append({"file_type": ft, "type_name": TYPE_NAME[ft],
                              "majors": STANDARD_MAJORS, "items": items})
            else:
                # gen_ed 全局单份不随年级分区（grade='' 取全库最新）
                sf = db.latest_source_file(ft, "" if ft == "gen_ed" else grade)
                files.append({"file_type": ft, "type_name": TYPE_NAME[ft],
                              "majors": [], "items": [_status_item(None, sf)]})
        return {"files": files, "grades": db.list_grades()}
    finally:
        db.close()


# ============ v7 豁免（2026-09-04：类型A 课程豁免 / 类型B 旧课学分认可）============

# 学分认可（kind=credit）类别白名单 = 检查 8 类除"课外实践"（该类别无应修，
# 认可无意义，N4）；与检查层 JSON 类别键一致
_CREDIT_CATEGORY_WHITELIST = ("公共课程", "模块课程", "学科门类基础课程",
                              "专业大类基础课程", "专业核心课程", "专业选修课程",
                              "集中实践")


def _latest_grade_rows(db: WarningDB, grade: str) -> list:
    """该年级最新 grade 文件（done，按专业）中全部成绩行——与 run 装配
    _assemble_check_prep 同源取数（豁免校验/详情分组共用）。"""
    rows = []
    for sf in db.latest_source_files_by_major("grade", grade).values():
        if sf.parsed_status != "done":
            continue
        rows += db.get_grades(sf.id)
    return rows


def _student_prep(db: WarningDB, grade: str, stu):
    """该生个人轻量 Prep（豁免校验用，实现宜轻不整跑年级装配）：
    方案 = 该生 roster 专业（标准名）的当前方案课程；成绩 = 该年级最新成绩行中
    该生部分（含名单姓名交叉校正口径——OCR 学号漏位，与 _assemble_check_prep
    同款）。返回 None = 该生专业无当前方案（无法校验课程归属/匹配）。"""
    from .rules import Prep
    major = normalize_major(stu.major)
    pid = db.latest_active_plans(grade).get(major)
    if pid is None:
        return None
    grades = _latest_grade_rows(db, grade)
    roster_sids = {s.student_id for s in db.latest_roster(grade)}
    for g in grades:   # 名单外学号按姓名回填（成绩单页码错位/漏位）
        if g.student_id not in roster_sids and g.student_name == (stu.name or ""):
            g.student_id = stu.student_id
    mine = [g for g in grades if g.student_id == stu.student_id]
    return Prep([stu], [], mine,
                plan_by_major={major: db.get_plan_courses(pid)},
                sem_by_major={major: []}, status_changes=[],
                semester_code="4-1")


def _latest_term_credit(rows) -> float:
    """N4：认可学分取该旧课**最新学期**记录的 credit（服务端取，不信任前端）。
    term_label 按"第X学年…第Y学期"序号取最大；解析不出按 0 序，同序取成绩单
    文档后到者（与"后到覆盖"口径一致）。"""
    from .parsers import _term_ordinal
    best = rows[0]
    for g in rows[1:]:
        if (_term_ordinal(g.term_label) or (0, 0)) >= \
                (_term_ordinal(best.term_label) or (0, 0)):
            best = g
    return best.credit


def _find_source_conflict(waivers: list[dict], sid: str, n_clean: str) -> str | None:
    """N2 互斥探测：三类豁免（course 平替源 / credit 认可课 / grade 旧课豁免）
    不得重复引用同一成绩单旧课。

    返回冲突角色（``"credit"`` / ``"grade"`` / ``"course-source"``）或 ``None``。
    豁免按列表顺序逐条判定；单条豁免的 kind 互斥，故类别判定顺序无关。"""
    for w in waivers:
        if w["student_id"] != sid:
            continue
        if w["kind"] in ("credit", "grade") and clean_course_name(w["course_name"]) == n_clean:
            return w["kind"]
        if (w["kind"] == "course" and w.get("grade_source")
                and clean_course_name(w["grade_source"]) == n_clean):
            return "course-source"
    return None


# 各 kind 分支下，_find_source_conflict 返回角色 → 提示语尾（前缀固定为 课程《名》）
_WAIVER_CONFLICT_TAILS = {
    "course": {
        "credit": "已有学分认可豁免，不可再作为平替来源",
        "grade": "已有旧课豁免，不可再作为平替来源",
        "course-source": "已被其他课程豁免用作平替来源",
    },
    "grade": {
        "grade": "的旧课豁免已存在，请勿重复添加",
        "credit": "已有学分认可豁免，不可再旧课豁免",
        "course-source": "已被课程豁免用作平替来源，不可再旧课豁免",
    },
    "credit": {
        "credit": "的学分认可豁免已存在，请勿重复添加",
        "course-source": "已被课程豁免用作平替来源，不可再学分认可",
        "grade": "已有旧课豁免，不可再学分认可",
    },
}


def _validate_substitute_source(prep, stu, waivers: list[dict], source: str) -> dict | None:
    """course 豁免平替源校验：有效课程名 → 成绩单在册 → 未匹配方案（_grade_category
    None，与认可对象域一致）→ 无互斥引用（N2）→ 最高分及格（E/C2）。

    返回错误响应 dict 或 ``None``（校验通过）。"""
    sid = stu.student_id
    src_clean = clean_course_name(source)
    if not src_clean:
        return {"ok": False, "message": f"平替来源课程名无效: {source}"}
    rows = [g for g in prep._student_grades(sid)
            if clean_course_name(g.course_name) == src_clean]
    if not rows:
        return {"ok": False, "message":
                f"该生成绩单中未找到平替课程《{source}》，请从已修课程中选择"}
    if any(_grade_category(prep, sid, g) is not None for g in rows):
        return {"ok": False, "message":
                f"课程《{source}》已匹配当前培养方案课程（成绩单已计入应修），无需作为平替来源"}
    role = _find_source_conflict(waivers, sid, src_clean)
    if role is not None:
        return {"ok": False, "message": f"课程《{source}》{_WAIVER_CONFLICT_TAILS['course'][role]}"}
    # E/C2：平替源须及格（D3 同课多行取最高分代表行；挂科课未完成，
    # 作平替来源与及格制自相矛盾——仅免修不受限；金标准学生式判断走免修）
    rep = max(representative_by_course(rows).values(),
              key=lambda g: grade_band(g.grade_raw))
    if rep.pass_flag != 1:
        return {"ok": False, "message":
                f"课程《{source}》最高分未及格（{rep.grade_raw}），仅限及格课作平替/认可源"}
    return None


def _add_course_waiver(db: WarningDB, prep, stu, waivers: list[dict],
                       body: dict, grade: str, note: str, now: str) -> dict:
    """kind=course：免修/平替豁免。code 必须在该生 roster 专业（标准名）当前方案
    中——code 跨专业复用按专业解析防错配；同 (grade, sid, course_code) 重复拒绝
    （P1：应用层预检 + 唯一索引 idx_waiver_course_dup 兜底竞态）。"""
    sid = stu.student_id
    code = str(body.get("course_code") or "").strip()
    if not code:
        return {"ok": False, "message": "课程豁免缺少 course_code（课程代码）"}
    pc = next((c for c in prep._plan_by_major.get(
        normalize_major(stu.major), []) if c.course_code == code), None)
    if pc is None:
        return {"ok": False, "message":
                f"课程代码 {code} 不在该生专业（{stu.major}）当前培养方案中，请核对课程代码"}
    dup = next((w for w in waivers if w["student_id"] == sid
                and w["kind"] == "course" and w["course_code"] == code), None)
    if dup is not None:
        tip = f"平替《{dup['grade_source']}》" if dup.get("grade_source") else "免修"
        return {"ok": False, "message":
                f"课程《{pc.course_name}》的豁免已存在（{tip}，id={dup['id']}），"
                "请先撤销后重新添加"}
    source = str(body.get("grade_source") or "").strip()
    if source:
        err = _validate_substitute_source(prep, stu, waivers, source)
        if err is not None:
            return err
    try:
        wid = db.add_waiver({"grade": grade, "student_id": sid,
                             "kind": "course", "course_code": code,
                             "course_name": pc.course_name,   # 服务端方案课名
                             "grade_source": source, "note": note,
                             "created_by": "api-admin", "created_at": now})
    except sqlite3.IntegrityError:
        return {"ok": False, "message": "豁免记录冲突，请刷新豁免列表后重试"}
    tip = f"平替《{source}》" if source else "免修"
    return {"ok": True, "id": wid,
            "message": f"已添加课程豁免《{pc.course_name}》（{tip}），"
                       "下次重跑选课检查后生效"}


def _add_grade_waiver(db: WarningDB, prep, stu, waivers: list[dict],
                      body: dict, grade: str, note: str, now: str) -> dict:
    """kind=grade（2026-09-07）：方案外旧课免修——纯记录豁免：使该旧课退出
    未及格科目/未匹配候选清单，可撤销；不改动方案课判定/差额。仅限最高分
    代表行**不及格**的旧课（及格旧课应正常作平替/认可源，禁止误豁免）。"""
    sid = stu.student_id
    name = str(body.get("course_name") or "").strip()
    n_clean = clean_course_name(name)
    if not name or not n_clean:
        return {"ok": False, "message":
                "旧课免修缺少课程名 course_name（须为该生成绩单中的课程名）"}
    rows = [g for g in prep._student_grades(sid)
            if clean_course_name(g.course_name) == n_clean]
    if not rows:
        return {"ok": False, "message":
                f"该生成绩单中未找到课程《{name}》，仅支持成绩单中已修的旧版课程"}
    rep = next(iter(representative_by_course(rows).values()))
    if rep.pass_flag == 1:
        return {"ok": False, "message":
                f"课程《{name}》最高分已及格（{rep.grade_raw}），无需旧课豁免"
                "（可正常作平替/认可源）"}
    if any(_grade_category(prep, sid, g) is not None for g in rows):
        return {"ok": False, "message":
                f"课程《{name}》已匹配当前培养方案课程（{rep.grade_raw} 分未过），"
                "请在该课的缺修/未及格行使用课程免修"}
    role = _find_source_conflict(waivers, sid, n_clean)
    if role is not None:
        return {"ok": False, "message": f"课程《{name}》{_WAIVER_CONFLICT_TAILS['grade'][role]}"}
    try:
        wid = db.add_waiver({"grade": grade, "student_id": sid,
                             "kind": "grade", "course_name": name,
                             "note": note, "created_by": "api-admin",
                             "created_at": now})
    except sqlite3.IntegrityError:
        return {"ok": False, "message": "豁免记录冲突，请刷新豁免列表后重试"}
    return {"ok": True, "id": wid,
            "message": f"已记录旧课豁免《{name}》（最高分 {rep.grade_raw} 分未过），"
                       "该课不再列入未及格/候选清单，可随时撤销"}


def _add_credit_waiver(db: WarningDB, prep, stu, waivers: list[dict],
                       body: dict, grade: str, note: str, now: str) -> dict:
    """kind=credit：学分认可。course_name 必填为该生成绩单未匹配课，credit 取
    最新学期记录学分（服务端取，N4），category 白名单（除课外实践），
    重复键 (grade, sid, course_name) 拒绝 + grade_source 引用互斥查重（N2）。"""
    sid = stu.student_id
    name = str(body.get("course_name") or "").strip()
    n_clean = clean_course_name(name)
    if not name or not n_clean:
        return {"ok": False, "message":
                "学分认可缺少课程名 course_name（须为该生成绩单中的课程名）"}
    cat = str(body.get("category") or "").strip()
    if cat not in _CREDIT_CATEGORY_WHITELIST:
        return {"ok": False, "message":
                f"认可类别无效：{cat or '（空）'}（可选："
                f"{'、'.join(_CREDIT_CATEGORY_WHITELIST)}）"}
    rows = [g for g in prep._student_grades(sid)
            if clean_course_name(g.course_name) == n_clean]
    if not rows:
        return {"ok": False, "message":
                f"该生成绩单中未找到课程《{name}》，仅支持认可成绩单中已修的旧版课程"}
    if any(_grade_category(prep, sid, g) is not None for g in rows):
        return {"ok": False, "message":
                f"课程《{name}》已匹配当前培养方案课程（成绩单已计入应修类别），无需学分认可"}
    role = _find_source_conflict(waivers, sid, n_clean)
    if role is not None:
        return {"ok": False, "message": f"课程《{name}》{_WAIVER_CONFLICT_TAILS['credit'][role]}"}
    # E/C2：认可源须及格（同课最高分代表行；挂科旧课学分折入类别与及格制
    # 矛盾——仅豁免/免修不受限）
    rep = max(representative_by_course(rows).values(),
              key=lambda g: grade_band(g.grade_raw))
    if rep.pass_flag != 1:
        return {"ok": False, "message":
                f"课程《{name}》最高分未及格（{rep.grade_raw}），仅限及格课作平替/认可源"}
    credit = _latest_term_credit(rows)   # N4：最新学期记录学分（服务端取）
    try:
        wid = db.add_waiver({"grade": grade, "student_id": sid,
                             "kind": "credit", "course_name": name,
                             "category": cat, "credit": credit,
                             "note": note, "created_by": "api-admin",
                             "created_at": now})
    except sqlite3.IntegrityError:
        return {"ok": False, "message": "豁免记录冲突，请刷新豁免列表后重试"}
    return {"ok": True, "id": wid,
            "message": f"已添加学分认可：课程《{name}》{credit:g} 学分计入"
                       f"「{cat}」，下次重跑选课检查后生效"}


@app.post("/api/warning/waiver")
def add_waiver(body: dict = Body(...), x_api_key: str | None = Header(None)):
    """新增豁免（v7）。kind 语义与校验规则详见各 handler docstring：
    course → _add_course_waiver（含平替源校验 _validate_substitute_source）；
    grade → _add_grade_waiver；credit → _add_credit_waiver。"""
    _check_key(x_api_key)
    grade = str(body.get("grade") or "").strip()
    sid = str(body.get("student_id") or "").strip()
    kind = str(body.get("kind") or "").strip()
    note = str(body.get("note") or "").strip()
    if kind not in ("course", "credit", "grade"):
        return {"ok": False, "message": f"未知豁免类型: {kind}（可选 course/credit）"}
    if not sid:
        return {"ok": False, "message": "缺少学生学号 student_id"}
    db = WarningDB()
    try:
        stu = next((s for s in db.latest_roster(grade) if s.student_id == sid),
                   None)
        if stu is None:
            return {"ok": False, "message":
                    f"学生 {sid} 不在{grade}学籍名单中，无法添加豁免"}
        prep = _student_prep(db, grade, stu)
        if prep is None:
            return {"ok": False, "message":
                    f"学生 {sid}（{stu.major or '未知专业'}）当前无有效培养方案，无法校验豁免"}
        waivers = db.list_waivers(grade)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        handler = {"course": _add_course_waiver, "grade": _add_grade_waiver,
                   "credit": _add_credit_waiver}[kind]
        return handler(db, prep, stu, waivers, body, grade, note, now)
    finally:
        db.close()


@app.get("/api/warning/waiver")
def waivers(grade: str = "", x_api_key: str | None = Header(None)):
    """豁免记录列表（v7；grade 空 = 全年级，兼容旧调用）。"""
    _check_key(x_api_key)
    db = WarningDB()
    try:
        return {"waivers": db.list_waivers(grade)}
    finally:
        db.close()


@app.delete("/api/warning/waiver")
def waiver_delete(id: int = Body(..., embed=True),
                  x_api_key: str | None = Header(None)):
    """撤销豁免——物理删除、无撤销审计（L3 注明：created_by/created_at 留痕添加侧，
    撤销不追溯，轻量原则）。"""
    _check_key(x_api_key)
    db = WarningDB()
    try:
        db.delete_waiver(id)
    finally:
        db.close()
    return {"ok": True, "message": "已撤销豁免"}


def _empty_student_detail() -> dict:
    """学生详情空态（无选课批次/无方案/名单外学生）。"""
    return {"student": None, "details": {"cats": [], "other_missing": []},
            "courses": {"matched": [], "unmatched": [], "failed": []},
            "selections": [], "waivers": []}


def _latest_details_json(db: WarningDB, grade: str, sid: str) -> dict:
    """该生最新批次 details（解析后 JSON）；未触发/解析失败 → 空结构。"""
    det = {"cats": [], "other_missing": []}
    for r in db.latest_selection_check(grade)[2]:
        if r.student_id == sid:
            try:
                det = json.loads(r.details or "{}")
            except ValueError:
                det = {"cats": [], "other_missing": []}
            break
    return det


def _waiver_used_clean_names(waivers: list[dict]) -> set[str]:
    """已消耗的未匹配课（clean 名集合）：kind=course 平替源 grade_source 或
    kind=credit/grade 已认可/豁免旧课——不再作为候选返回（前端候选池/平替
    选择列表排除；撤销豁免后自动恢复出现）。"""
    used = {clean_course_name(w["grade_source"])
            for w in waivers if w["kind"] == "course" and w["grade_source"]}
    used |= {clean_course_name(w["course_name"])
             for w in waivers if w["kind"] in ("credit", "grade")}
    return used


def _detail_missing_codes(det: dict) -> set[str]:
    """I1：reported_missing 判定集 = 该生已加载批次缺修清单 code（与页面
    同屏清单单一真源——不复用实时选课近似，防双口径不一致）。
    2026-09-22：缺修课按类别归入 cats[].missing 与 other_missing，取并集。"""
    codes = {m["code"] for c in det.get("cats", [])
             for m in (c.get("missing") or [])}
    codes |= {m["code"] for m in (det.get("other_missing") or [])}
    codes |= {m["code"] for m in (det.get("missing") or [])}   # 老批次兼容
    return codes


def _failed_row_for(g, key: str, pc, used_clean: set[str],
                    missing_codes: set[str]) -> dict | None:
    """D4 未及格科目节单行：代表行不及格每课一条；code 经 _match_plan_course
    命中的方案课程码（未命中空串），reported_missing = code 在批次缺修清单中
    （在修不报以批次为准）；used_clean（已认可/已作平替源/旧课豁免）且无方案码
    → 不再展示（撤销后恢复）→ None。"""
    code = pc.course_code if pc else ""
    if key in used_clean and code == "":
        return None
    return {
        "course_name": g.course_name or "",
        "code": code,
        "credit": g.credit, "grade_raw": g.grade_raw,
        "term_label": g.term_label or "",
        "marker": g.marker or "",
        "reported_missing": code != "" and code in missing_codes}


def _group_student_courses(prep, sid: str, reps, attempts,
                           used_clean: set[str], missing_codes: set[str]) \
        -> tuple[list[dict], list[dict], list[dict]]:
    """真实已修课程三分组（已修分界判定用代表行）：

    matched：_grade_category is not None = 计入已修（方案匹配 + ◆选修课/
    ◇核心课/英语/军训隐式归并，按 JSON 类别标注；挂科方案课仍在组内，
    行内 ok=false 供标注"未及格"）；unmatched：None = 未计入旧课
    （认可/平替候选，仅未被豁免消耗项；挂科旧课保留候选但 usable=false +
    usable_reason，前端禁用）；failed：代表行不及格每课一条
    （_failed_row_for）。L2：分组为真实成绩，不注入豁免虚拟行。"""
    matched, unmatched, failed = [], [], []
    for key, g in reps.items():
        cat = _grade_category(prep, sid, g)
        pc = prep._match_plan_course(sid, g)
        row = {"course_name": g.course_name or "",
               "credit": g.credit, "best_raw": g.grade_raw,
               "term_label": g.term_label or "",
               "marker": g.marker or "", "attempts": attempts[key],
               "ok": g.pass_flag == 1}
        if g.pass_flag != 1:
            frow = _failed_row_for(g, key, pc, used_clean, missing_codes)
            if frow is not None:
                failed.append(frow)
        if cat is not None:
            matched.append(dict(row, category=cat))
            continue
        if key in used_clean:
            continue   # 已被平替/认可消耗 → 不进候选（撤销豁免后恢复）
        # 挂科旧课保留候选，行级 usable=false + 固定禁用原因（前端禁用）
        row["usable"] = g.pass_flag == 1
        if not row["usable"]:
            row["usable_reason"] = "挂科未过"
        unmatched.append(row)
    matched.sort(key=lambda d: (d["category"], d["course_name"]))
    unmatched.sort(key=lambda d: d["course_name"])
    failed.sort(key=lambda d: d["course_name"])
    return matched, unmatched, failed


@app.get("/api/warning/selection-check/student")
def student_detail(grade: str = "", sid: str = "",
                   x_api_key: str | None = Header(None)):
    """学生详情（v7）：details（该生最新批次触发明细——与列表同源，未触发学生
    空结构）+ 真实已修课程三分组（_group_student_courses）+ 该生豁免状态
    （前端标注"已豁免"）。

    2026-09-07 D3/D4/F：matched/unmatched/failed 按 clean 课名**每课一行**
    ——同课多行（重修/补考）取最高分代表行（representative_by_course），行字段
    即代表行（credit/best_raw/term_label/marker/ok），attempts = 同课记录数。
    语义详见 _group_student_courses 与 _failed_row_for docstring。"""
    _check_key(x_api_key)
    db = WarningDB()
    try:
        sel_file = db.latest_source_file("selection", grade)
        if sel_file is None or sel_file.parsed_status != "done":
            return _empty_student_detail()
        prep = _assemble_check_prep(db, sel_file)
        if prep is None:
            return _empty_student_detail()
        stu = prep.students.get(sid)
        if stu is None:
            return _empty_student_detail()   # 名单外/未选课学生：无检查对象
        det = _latest_details_json(db, grade, sid)
        waivers = [w for w in db.list_waivers(grade) if w["student_id"] == sid]
        from collections import Counter
        grades = prep._student_grades(sid)
        reps = representative_by_course(grades)   # 同课(clean 名)最高分代表行
        attempts = Counter(g.course_name_clean or g.course_name for g in grades)
        matched, unmatched, failed = _group_student_courses(
            prep, sid, reps, attempts,
            _waiver_used_clean_names(waivers), _detail_missing_codes(det))
        return {
            "student": {"student_id": stu.student_id, "name": stu.name or "",
                        "major": stu.major or "",
                        "class_name": stu.class_name or ""},
            "details": det,
            "courses": {"matched": matched, "unmatched": unmatched,
                        "failed": failed},
            # 2026-09-07：本学期选中课程（Prep 已过滤 status=选中；转专业
            # del_selection=是 的学生为空）——详情页"本学期选课"节数据源
            "selections": sorted(
                [{"course_code": sel.course_code, "course_name": sel.course_name,
                  "credit": sel.credit, "nature": sel.nature,
                  "category": sel.category, "retake": sel.retake}
                 for sel in prep._student_selections(sid)],
                key=lambda d: (d["course_name"], d["course_code"])),
            "waivers": waivers,
        }
    finally:
        db.close()
