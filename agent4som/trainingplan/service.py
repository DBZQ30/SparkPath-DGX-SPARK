"""培养方案智能解读 —— 服务层（上传/队列/解析编排/解读摘要）。

设计 §6：单消费者串行解析队列（持久化状态），进度可见。
设计 §11：解读摘要为固定四段文本（聊天 skill 直接回报）。
"""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime
from typing import Any, Optional

from . import parsers
from .db import ALL_GRADES, TrainingPlanDB
from .models import (
    MODES,
    STANDARD_MAJORS,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PARSING,
    STATUS_QUEUED,
    ModeCourse,
    ModeRule,
    ModeScope,
    PlanDocument,
    PrereqEdge,
)

# 课程/推荐课表模型直接复用 academicwarning（同一份培养方案的同一套解析口径），
# 不再自定义重复类——2026-09-23 修正：自建 PlanCourse 与 academicwarning 撞名且字段
# 不同（缺 en_name），导致 AttributeError。见设计 §4「复用既有解析能力」。
from academicwarning.models import PlanCourse, PlanSemesterCourse  # noqa: F401

DB_PATH = os.environ.get("TRAINING_PLAN_DB", "data/training_plan.db")
_ARTIFACT_DIR = os.environ.get("TRAINING_PLAN_ARTIFACTS", "data/training_plan_artifacts")
MAX_FILE_SIZE = 10 * 1024 * 1024

# 文件中心「中心优先取数」开关（设计 007 §6；默认 on，失败自动回退老逻辑）
DOC_CENTER_RESOLVE = os.environ.get("DOC_CENTER_RESOLVE", "on").lower() in ("1", "on", "true", "yes")


# ─────────────────────────── 方案定位（中心优先） ───────────────────────────

def _center_plan_doc(db: TrainingPlanDB, major: str, grade: str) -> Optional[PlanDocument]:
    """经文件中心解析适用培养方案（中心优先取数）；不适用/未就绪 → None（回退）。"""
    if not DOC_CENTER_RESOLVE:
        return None
    try:
        from doccenter import service as dc
        for it in dc.resolve("interpret", grade, major):
            if not it.get("ready") or it.get("doc_type") != "培养方案":
                continue
            pid = (it.get("output_ref") or {}).get("plan_document_id")
            if not pid:
                continue
            d = db.get_document(int(pid))
            if (d and d.major == major and d.parsed_status == STATUS_DONE
                    and (not it.get("file_hash") or d.file_hash == it.get("file_hash"))):
                return d
    except Exception:
        return None
    return None


def find_document(db: TrainingPlanDB, major: str, grade: str) -> Optional[PlanDocument]:
    """定位目标（专业, 年级）方案：**文件中心优先**，不可用时回退既有 `applies_to` 逻辑。"""
    return _center_plan_doc(db, major, grade) or db.find_document_for_grade(major, grade)

# 队列：单消费者 worker + 唤醒事件（设计 §6）
_QUEUE_LOCK = threading.Lock()
_WAKE = threading.Event()
_WORKER: Optional[threading.Thread] = None


# ─────────────────────────── 工具 ───────────────────────────

def normalize_major(name: str) -> Optional[str]:
    """复用 academicwarning 的专业归一化（同一份方案的同一套专业名口径）。"""
    from academicwarning.parsers import canonical_major
    return canonical_major(name or "")


def academic_hint(now: Optional[datetime] = None) -> dict[str, Any]:
    """当前学年与"年级名 → 入学年级"折算（供 skill/CLI 确定性使用，避免靠模型猜日期）。

    学年起始年：9 月及以后为当年，之前为前一年。例：2026-09 → 学年 2026-2027，大一=2026级。
    """
    now = now or datetime.now()
    start = now.year if now.month >= 9 else now.year - 1
    order = [("大一", 0), ("大二", 1), ("大三", 2), ("大四", 3)]
    return {
        "date": now.strftime("%Y-%m-%d"),
        "academic_start": start,
        "academic_label": f"{start}-{start + 1} 学年",
        "grades": {name: f"{start - off}级" for name, off in order},
        "note": f"当前 {now.strftime('%Y-%m-%d')}（{start}-{start + 1} 学年）；"
                + "、".join(f"{name}={start - off}级" for name, off in order),
    }


_GRADE_CN = {"一": 0, "二": 1, "三": 2, "四": 3}


def normalize_entry_year(text: str) -> Optional[str]:
    """'2023' / '23级' / '2023级' / 文件名 '2023版' → '2023级'。

    另支持**相对说法**（按当前学年折算）：'大一'→当年入学、'大二'→前一年、'新生'/'今年'→当年。
    """
    s = str(text or "")
    m = re.search(r"(20\d\d)", s)
    if m:
        return f"{m.group(1)}级"
    m = re.search(r"(?<!\d)(\d\d)级", s)
    if m:
        return f"20{m.group(1)}级"
    m = re.search(r"大([一二三四])", s)
    if m:
        return f"{academic_hint()['academic_start'] - _GRADE_CN[m.group(1)]}级"
    if "新生" in s or "今年" in s:
        return f"{academic_hint()['academic_start']}级"
    return None


def _file_md5(path: str) -> str:
    """复用 academicwarning 的文件哈希实现。"""
    from academicwarning.service import _file_md5 as _aw_md5
    return _aw_md5(path)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def detect_plan_meta(path: str) -> tuple[Optional[str], Optional[str]]:
    """从文件内容/文件名识别 (专业, 年级)。无法识别返回 None。"""
    major = entry = None
    try:
        from docx import Document
        doc = Document(path)
        for para in doc.paragraphs[:15]:
            t = para.text.strip()
            if not major:
                m = re.search(r"(.+?)专业培养方案", t)
                if m:
                    major = normalize_major(m.group(1))
            if not entry:
                m = re.search(r"(20\d\d)\s*版", t)
                if m:
                    entry = f"{m.group(1)}级"
            if major and entry:
                break
    except Exception:
        pass
    base = os.path.basename(path)
    if not major:
        m = re.search(r"(.+?)专业培养方案", base)
        if m:
            major = normalize_major(m.group(1))
    if not entry:
        m = re.search(r"(20\d\d)\s*版", base)
        if m:
            entry = f"{m.group(1)}级"
    return major, entry


# ─────────────────────────── 上传 / 入队 ───────────────────────────

def submit_upload(path: str, major: str = "", entry_year: str = "",
                  uploader: str = "", orig_name: str = "") -> dict[str, Any]:
    """校验 + 入队（供 HTTP 与 CLI 共用）。返回 {status, message, id, major, entry_year}。"""
    db = TrainingPlanDB(DB_PATH)
    try:
        if not os.path.isfile(path):
            return {"status": "rejected", "message": "文件不存在"}
        if os.path.getsize(path) > MAX_FILE_SIZE:
            return {"status": "rejected", "message": "文件超过 10MB 上限，已拒绝"}

        fname = orig_name or os.path.basename(path)
        det_major, det_entry = detect_plan_meta(path)

        major_n = normalize_major(major) or det_major
        entry_n = normalize_entry_year(entry_year) or det_entry

        doc = PlanDocument(major=major_n or "", entry_year=entry_n or "",
                           file_name=fname, file_path=path, uploader=uploader,
                           file_hash=_file_md5(path), upload_time=_now())

        # 专业强校验：识别到且与所选不符 → 拒绝
        if major and det_major and normalize_major(major) != det_major:
            rid = db.insert_rejected(doc, f"文件识别为「{det_major}」，与所选专业「{major}」不匹配")
            return {"status": "rejected", "id": rid,
                    "message": f"文件识别为「{det_major}」，与所选专业「{major}」不匹配，请确认是否传错"}
        # 年级强校验
        if entry_n and det_entry and entry_n != det_entry:
            rid = db.insert_rejected(doc, f"文件版次为「{det_entry}」，与所选年级「{entry_n}」不匹配")
            return {"status": "rejected", "id": rid,
                    "message": f"文件版次为「{det_entry}」，与所选年级「{entry_n}」不匹配，请确认是否传错"}

        if not doc.major:
            rid = db.insert_rejected(doc, "无法识别所属专业")
            return {"status": "rejected", "id": rid, "message": "无法识别文件所属专业，请选择专业后重传"}
        if not doc.entry_year:
            rid = db.insert_rejected(doc, "无法识别入学年级")
            return {"status": "rejected", "id": rid, "message": "无法识别培养方案年级（文件名应含「20XX版」）"}

        dup = db.find_done_by_hash(doc.major, doc.entry_year, doc.file_hash)
        if dup:
            return {"status": "done", "id": dup.id, "major": doc.major, "entry_year": doc.entry_year,
                    "message": f"该文件已上传过（{dup.upload_time}），未重复入库"}

        doc_id = db.enqueue_document(doc)
        _wake_worker()
        return {"status": "queued", "id": doc_id, "major": doc.major, "entry_year": doc.entry_year,
                "message": "已入队，等待后台解析"}
    finally:
        db.close()


def retry_document(doc_id: int) -> dict[str, Any]:
    db = TrainingPlanDB(DB_PATH)
    try:
        ok = db.retry(doc_id)
        if ok:
            _wake_worker()
            return {"status": "queued", "id": doc_id, "message": "已重新入队"}
        return {"status": "error", "message": "该记录不存在或不是失败状态，无法重试"}
    finally:
        db.close()


def reparse(major: str = "", entry_year: str = "", all_plans: bool = False) -> dict[str, Any]:
    """强制重新解析已入库方案（用于解析逻辑修复后回填，如同一份 docx 重跑）。

    不走队列（同步执行），只重算并覆盖该 plan_id 的结构化数据与先修边。
    注意：会**重置先修边的 verified 状态**（重新抽取的边视为未校对）。
    """
    db = TrainingPlanDB(DB_PATH)
    targets: list[tuple[int, str, str]] = []
    try:
        if all_plans:
            for d in db.list_documents(status=STATUS_DONE):
                if d.is_active:
                    targets.append((d.id, d.major, d.entry_year))
        else:
            major_n = normalize_major(major)
            entry_n = normalize_entry_year(entry_year) if entry_year else ""
            if not major_n or not entry_n:
                return {"status": "error", "message": "需要 --major 与 --entry-year，或 --all"}
            doc = db.get_active_document(major_n, entry_n)
            if not doc:
                return {"status": "error", "message": f"{major_n} {entry_n} 未收录"}
            targets.append((doc.id, doc.major, doc.entry_year))
    finally:
        db.close()

    results = []
    for pid, mj, yr in targets:
        db2 = TrainingPlanDB(DB_PATH)
        try:
            parse_document(pid, db2)
            doc = db2.get_document(pid)
            results.append({"id": pid, "major": mj, "entry_year": yr,
                            "status": doc.parsed_status if doc else "?",
                            "note": doc.note if doc else ""})
        finally:
            db2.close()
    return {"status": "ok", "count": len(results), "results": results}


# ─────────────────────────── 解析（worker 内调用） ───────────────────────────

def _fmt_num(v: Any) -> str:
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:g}"


def _build_mode_data(mode_info: dict[str, Any]):
    """把 `parsers.parse_mode_rules` 的结果转成 (ModeRule[], ModeCourse[], ModeScope[])。"""
    rules: list[ModeRule] = []
    courses: list[ModeCourse] = []
    scopes: list[ModeScope] = []
    sort = 0

    def add_rule(mode: str, rule_type: str, item: str, value: str = "",
                 unit: str = "", detail: str = "") -> None:
        nonlocal sort
        rules.append(ModeRule(plan_id=0, mode=mode, rule_type=rule_type, item=item,
                              value=value, unit=unit, detail=detail, sort=sort))
        sort += 1

    add_rule("常规型", "说明", "常规型", "", "",
             "满足培养方案修读要求，达到毕业条件")

    modes = mode_info.get("modes") or {}
    sci = modes.get("科学研究型")
    if sci:
        add_rule("科学研究型", "学分替代", "研究生进阶课程",
                 _fmt_num(sci.get("credit")), "学分", "替代专业选修课")
        for co in (sci.get("courses") or []):
            courses.append(ModeCourse(plan_id=0, mode="科学研究型", group="研究生进阶",
                                      course_code=str(co.get("course_code", "")),
                                      course_name=str(co.get("course_name", "")),
                                      credit=float(co.get("credit") or 0)))
    cross = modes.get("交叉融合型")
    if cross:
        add_rule("交叉融合型", "学分替代", "跨专业交叉课程",
                 _fmt_num(cross.get("credit")), "学分",
                 "替代专业选修课；只交叉融合一个专业的课程")
        majors = cross.get("majors") or []
        if majors:
            add_rule("交叉融合型", "跨选范围", "跨选专业范围", "、".join(majors), "",
                     "仅可选下列专业之一的课程")
            for mj in majors:
                scopes.append(ModeScope(plan_id=0, mode="交叉融合型", allowed_major=mj))
    inno = modes.get("创新创业型")
    if inno:
        add_rule("创新创业型", "成果替代", "创新创业成果",
                 _fmt_num(inno.get("credit")), "学分",
                 "替换集中实践（除毕业设计和军训外）")

    lim = mode_info.get("credit_limit")
    if lim and lim.get("value") is not None:
        add_rule("通用", "学期学分上限", "每学期修读", _fmt_num(lim["value"]),
                 "学分", lim.get("note", ""))
    if mode_info.get("apply_node"):
        add_rule("通用", "申请节点", "培养模式申请", str(mode_info["apply_node"]), "",
                 "书面申请→学院审批→参加对应类型毕业设计")
    return rules, courses, scopes


def parse_document(doc_id: int, db: TrainingPlanDB) -> None:
    """解析一份培养方案：课程/课表（复用）+ 学分结构 + 毕业条件 + 先修图 VL + 培养模式。"""
    doc = db.get_document(doc_id)
    if not doc:
        return
    path = doc.file_path
    try:
        _meta, courses, sem_courses, bad_sems = parsers.parse_plan_data(path)
        credit_nodes, top = parsers.parse_credit_structure(path)
        grad_reqs = parsers.parse_graduation_reqs(path, top)
        # 培养模式规则（四路径）+ 课程备注（设计 005 §4）
        mode_info = parsers.parse_mode_rules(path)
        course_notes = parsers.parse_course_notes(path)
        mode_rules, mode_courses, mode_scopes = _build_mode_data(mode_info)

        # 先修图：抽取第八章图 → VL → 仅保留"像先修图"的结果
        imgs = parsers.extract_prereq_candidates(path)
        course_names = [c.course_name for c in courses]
        edges: list[PrereqEdge] = []
        vl_note = "未找到先修关系图"
        prereq_png = ""
        if imgs:
            media = imgs[0]["media"]
            try:
                # 服务端托管为 PNG（供校对页展示 / 解读页折叠查看）
                prereq_png = parsers.materialize_prereq_image(path, media, _ARTIFACT_DIR, doc_id)
            except Exception:
                prereq_png = ""
            try:
                res = parsers.parse_prereq_vl(prereq_png or media, course_names)
                if res.get("is_prereq_graph"):
                    for e in res["edges"]:
                        edges.append(PrereqEdge(plan_id=doc_id, from_course_name=e["from"],
                                                to_course_name=e["to"], source="vl", confidence=0.0))
                    vl_note = f"先修边 {len(edges)} 条（未校对）"
                    if res.get("unmatched"):
                        vl_note += f"，{len(res['unmatched'])} 个名称待确认"
                else:
                    vl_note = (f"先修图识别存疑（课程名命中率 {res.get('matched_ratio', 0):.0%}，"
                               f"边 {len(res.get('edges', []))} 条），待管理员人工录入")
            except Exception as exc:
                vl_note = f"先修图 VL 解析失败：{type(exc).__name__}"

        db.replace_plan_data(doc_id, credit_nodes, courses, sem_courses, edges, grad_reqs,
                             course_notes=course_notes)
        db.replace_mode_data(doc_id, mode_rules, mode_courses, mode_scopes)
        db.deactivate_other_versions(doc.major, doc.entry_year, doc_id)
        mode_note = ""
        if mode_info.get("modes"):
            mode_note = f"；培养模式 {len(mode_info['modes'])} 类 / 规则 {len(mode_rules)} 条"
        note = (f"课程 {len(courses)} 门 / 推荐课表 {len(sem_courses)} 条 / "
                f"学分结构 {len(credit_nodes)} 项 / 毕业条件 {len(grad_reqs)} 条"
                f"{mode_note}；{vl_note}")
        meta_extra = {"top": top, "prereq_image": prereq_png,
                      "bad_semesters": bad_sems, "mode_info": mode_info}
        db.finish_document(doc_id, STATUS_DONE, note=note, meta_extra=meta_extra)
    except Exception as exc:
        db.finish_document(doc_id, STATUS_FAILED, error=f"{type(exc).__name__}: {exc}")


# ─────────────────────────── 队列 worker（单消费者） ───────────────────────────

def _wake_worker() -> None:
    _WAKE.set()
    _ensure_worker()


def _ensure_worker() -> None:
    global _WORKER
    with _QUEUE_LOCK:
        if _WORKER and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_worker_loop, name="trainingplan-worker", daemon=True)
        _WORKER.start()


def _worker_loop() -> None:
    while True:
        db = TrainingPlanDB(DB_PATH)
        try:
            doc = db.claim_next()
        finally:
            db.close()
        if doc is None:
            _WAKE.clear()
            # 双检：清事件后再查一次，避免漏唤醒
            db2 = TrainingPlanDB(DB_PATH)
            try:
                doc2 = db2.claim_next()
            finally:
                db2.close()
            if doc2 is None:
                break
            doc = doc2
        db3 = TrainingPlanDB(DB_PATH)
        try:
            parse_document(doc.id, db3)
        finally:
            db3.close()


def start_worker() -> int:
    """启动时调用：清理遗留 parsing + 唤醒 worker。返回恢复条数。"""
    db = TrainingPlanDB(DB_PATH)
    try:
        n = db.recover_stale()
    finally:
        db.close()
    _wake_worker()
    return n


# ─────────────────────────── 解读（四段摘要） ───────────────────────────

_SEM_ORDER = ["1-1", "1-2", "1-3", "2-1", "2-2", "2-3", "3-1", "3-2", "3-3", "4-1", "4-2"]


def resolve(major: str, entry_year: str = "") -> dict[str, Any]:
    """解析目标方案，返回 {state, ...}。state ∈ done/parsing/unavailable/need_year/bad_major。"""
    db = TrainingPlanDB(DB_PATH)
    try:
        major_n = normalize_major(major)
        if not major_n:
            return {"state": "bad_major", "message": f"无法识别专业「{major}」",
                    "majors": STANDARD_MAJORS}
        entry_n = normalize_entry_year(entry_year) if entry_year else ""
        if not entry_n:
            grades, has_all = db.available_grades(major_n)
            return {"state": "need_year", "major": major_n,
                    "years": grades + ([ALL_GRADES] if has_all else [])}
        doc = find_document(db, major_n, entry_n)
        if not doc:
            grades, has_all = db.available_grades(major_n)
            return {"state": "unavailable", "major": major_n, "entry_year": entry_n,
                    "years": grades + ([ALL_GRADES] if has_all else []),
                    "message": f"{major_n} {entry_n} 的培养方案尚未收录"}
        if doc.parsed_status in (STATUS_QUEUED, STATUS_PARSING):
            return {"state": "parsing", "major": major_n, "entry_year": entry_n,
                    "status": doc.parsed_status, "id": doc.id}
        if doc.parsed_status != STATUS_DONE:
            return {"state": "unavailable", "major": major_n, "entry_year": entry_n,
                    "message": doc.error or "该方案解析失败，请联系管理员重传"}
        return {"state": "done", "major": major_n, "entry_year": entry_n,
                "doc_info": {"id": doc.id, "file_name": doc.file_name,
                             "upload_time": doc.upload_time, "version_label": doc.version_label}}
    finally:
        db.close()


def interpret(major: str, entry_year: str = "") -> dict[str, Any]:
    """生成解读结果（结构化数据 + 四段摘要文本）。

    年级策略（2026-09-23 最终确认）：**专业与年级缺一不可，任一缺失都反问**。
    - 未指定年级 → 返回 `need_year`（附可用年级），**不自动替换年级**；
    - 指定年级 → 按 `applies_to` 解析：**精确匹配年级优先，其次"全部年级"方案**；
      都没有 → `unavailable`（附可用年级），**不用其他年级冒充**。
    """
    db = TrainingPlanDB(DB_PATH)
    try:
        major_n = normalize_major(major)
        if not major_n:
            return {"state": "need_major", "message": f"无法识别专业「{major}」",
                    "majors": STANDARD_MAJORS}
        entry_n = normalize_entry_year(entry_year) if entry_year else ""
        grades, has_all = db.available_grades(major_n)
        year_list = grades + ([ALL_GRADES] if has_all else [])
        if not entry_n:
            return {"state": "need_year", "major": major_n, "years": year_list,
                    "message": f"请提供年级（如 2023级）；{major_n} 已收录年级："
                               + ("、".join(year_list) if year_list else "暂无")}

        doc = find_document(db, major_n, entry_n)
        if not doc:
            return {"state": "unavailable", "major": major_n, "entry_year": entry_n,
                    "years": year_list,
                    "message": f"{major_n} {entry_n} 的培养方案尚未收录"}
        if doc.parsed_status in (STATUS_QUEUED, STATUS_PARSING):
            return {"state": "parsing", "major": major_n, "entry_year": entry_n,
                    "message": f"{major_n} {entry_n} 的培养方案正在解析中，请稍后再问"}
        if doc.parsed_status != STATUS_DONE:
            return {"state": "unavailable", "major": major_n, "entry_year": entry_n,
                    "message": "该方案解析失败，请联系管理员重传"}

        data = _build_interpret_data(db, doc, requested_grade=entry_n)
        data["state"] = "done"
        data["summary_text"] = _render_summary(data)
        db.record_interpret_run("", major_n, entry_n, {"summary": data["summary_text"]})
        return data
    finally:
        db.close()


def modes(major: str, entry_year: str = "") -> dict[str, Any]:
    """四路径培养模式规则 + 课程清单 + 跨选范围（设计 005 §4）。

    与 `interpret` 同样的专业/年级校验口径（缺年级反问、未收录不冒充）。
    """
    db = TrainingPlanDB(DB_PATH)
    try:
        major_n = normalize_major(major)
        if not major_n:
            return {"state": "bad_major", "message": f"无法识别专业「{major}」",
                    "majors": STANDARD_MAJORS}
        entry_n = normalize_entry_year(entry_year) if entry_year else ""
        grades, has_all = db.available_grades(major_n)
        year_list = grades + ([ALL_GRADES] if has_all else [])
        if not entry_n:
            return {"state": "need_year", "major": major_n, "years": year_list,
                    "message": f"请提供年级（如 2023级）；{major_n} 已收录年级："
                               + ("、".join(year_list) if year_list else "暂无")}
        doc = find_document(db, major_n, entry_n)
        if not doc:
            return {"state": "unavailable", "major": major_n, "entry_year": entry_n,
                    "years": year_list,
                    "message": f"{major_n} {entry_n} 的培养方案尚未收录"}
        if doc.parsed_status in (STATUS_QUEUED, STATUS_PARSING):
            return {"state": "parsing", "major": major_n, "entry_year": entry_n,
                    "message": f"{major_n} {entry_n} 的培养方案正在解析中，请稍后再问"}
        if doc.parsed_status != STATUS_DONE:
            return {"state": "unavailable", "major": major_n, "entry_year": entry_n,
                    "message": "该方案解析失败，请联系管理员重传"}
        rules = db.get_mode_rules(doc.id)
        courses = db.get_mode_courses(doc.id)
        scopes = db.get_mode_scopes(doc.id)
        # 兜底：库里还没有模式数据（旧方案未回填）→ 即时解析原件
        if not rules and doc.file_path and os.path.isfile(doc.file_path):
            rules, courses, scopes = _parse_modes_live(doc.file_path)
        return {"state": "done", "major": major_n, "entry_year": entry_n,
                "modes": MODES,
                "rules": rules,
                "courses": courses,
                "scopes": scopes}
    finally:
        db.close()


def _parse_modes_live(path: str) -> tuple[list[dict], list[dict], list[dict]]:
    """即时解析培养模式（不写库）——返回 dict 列表。"""
    from dataclasses import asdict
    rules, courses, scopes = _build_mode_data(parsers.parse_mode_rules(path))
    return ([asdict(r) for r in rules], [asdict(c) for c in courses],
            [asdict(s) for s in scopes])


def backfill_modes(major: str = "", entry_year: str = "") -> dict[str, Any]:
    """回填培养模式数据（**只动模式表与课程备注，不碰先修边等其他表**）。"""
    db = TrainingPlanDB(DB_PATH)
    try:
        docs = db.list_documents(major=normalize_major(major) or "",
                                 entry_year=normalize_entry_year(entry_year) or "",
                                 status=STATUS_DONE)
        results = []
        for doc in docs:
            if not doc.file_path or not os.path.isfile(doc.file_path):
                results.append({"major": doc.major, "entry_year": doc.entry_year,
                                "status": "skip", "note": "原件缺失"})
                continue
            mi = parsers.parse_mode_rules(doc.file_path)
            notes = parsers.parse_course_notes(doc.file_path)
            rules, mcourses, scopes = _build_mode_data(mi)
            db.replace_mode_data(doc.id, rules, mcourses, scopes)
            n = db.update_course_notes(doc.id, notes)
            results.append({"major": doc.major, "entry_year": doc.entry_year, "status": "ok",
                            "modes": len(mi.get("modes") or {}), "rules": len(rules),
                            "courses": len(mcourses), "scopes": len(scopes), "notes": n})
        return {"status": "ok", "count": len(results), "results": results}
    finally:
        db.close()


# ─────────────────────────── 政策入库（转专业 / 专业选择） ───────────────────────────

def ingest_policy_file(path: str, source_doc_id: int = 0) -> dict[str, Any]:
    """解析并写入一份政策文件（转专业 / 考核安排 / 专业选择方案）。"""
    from . import policy
    parsed = policy.parse_policy_file(path)
    base = os.path.basename(path)
    if not parsed:
        return {"status": "skip", "message": "未识别为政策文件", "file": base}
    db = TrainingPlanDB(DB_PATH)
    try:
        kind = parsed["doc_type"]
        if kind == "转专业政策":
            year = parsed["policy_year"]
            db.replace_transfer_policy(year, parsed["plans"], parsed["rules"], source_doc_id)
            return {"status": "ok", "doc_type": kind, "policy_year": year,
                    "plans": len(parsed["plans"]), "rules": len(parsed["rules"]), "file": base}
        if kind == "转专业考核安排":
            year = parsed["policy_year"] or db.latest_transfer_policy_year()
            if not year:
                return {"status": "skip", "message": "缺少政策年份（请先导入转专业实施细则）",
                        "file": base}
            n = db.add_transfer_rules(year, parsed["rules"], source_doc_id)
            return {"status": "ok", "doc_type": kind, "policy_year": year, "rules": n, "file": base}
        if kind == "专业选择方案":
            year = parsed["entry_year"]
            db.replace_major_selection_policy(year, parsed["plans"], parsed["rules"], source_doc_id)
            return {"status": "ok", "doc_type": kind, "entry_year": year,
                    "plans": len(parsed["plans"]), "rules": len(parsed["rules"]), "file": base}
        return {"status": "skip", "message": f"未处理类型 {kind}", "file": base}
    finally:
        db.close()


def ingest_policy_path(path: str) -> dict[str, Any]:
    """登记并解析**单个**政策原件（供文件中心统一入库调用）。

    先写 `plan_source_doc`（保留 text_content 与 source_doc_id 溯源），再解析规则；
    「操作指引」只登记不解析。返回结果含 `source_doc_id`。幂等：重复入库取回原 id。
    """
    from . import policy
    from .ingest import _file_md5
    name = os.path.basename(path)
    kind = policy.classify_policy(name)
    if not kind:
        return {"status": "skip", "message": "未识别为政策文件", "file": name}
    text = policy.extract_text(path)
    fhash = _file_md5(path)
    db = TrainingPlanDB(DB_PATH)
    try:
        sid = db.upsert_source_doc({
            "category": "政策", "file_name": name, "file_path": os.path.abspath(path),
            "file_hash": fhash, "size_bytes": os.path.getsize(path),
            "ext": os.path.splitext(name)[1].lower(), "text_chars": len(text),
            "text_content": text, "note": "政策类型：" + kind, "applies_to": [ALL_GRADES],
        })
        if not sid:   # 已登记（file_name+file_hash 去重）→ 取回 id 供规则溯源
            row = db.conn.execute(
                "SELECT id FROM plan_source_doc WHERE file_name=? AND file_hash=?",
                (name, fhash)).fetchone()
            sid = int(row["id"]) if row else 0
    finally:
        db.close()
    if kind == "操作指引":
        return {"status": "registered", "doc_type": kind, "file": name, "source_doc_id": sid}
    res = ingest_policy_file(path, source_doc_id=sid)
    res["source_doc_id"] = sid
    return res


def ingest_policy_dir(directory: str) -> dict[str, Any]:
    """扫描目录，按类型优先级逐份调用 `ingest_policy_path`（幂等）。"""
    from . import policy
    _priority = {"转专业政策": 0, "转专业考核安排": 1, "专业选择方案": 2, "操作指引": 3}
    names = [n for n in os.listdir(directory)
             if os.path.isfile(os.path.join(directory, n)) and policy.classify_policy(n)]
    # 先导入「转专业政策」（会重置该年规则），再并入「考核安排」，避免被覆盖
    names.sort(key=lambda n: _priority.get(policy.classify_policy(n) or "", 9))
    results = [ingest_policy_path(os.path.join(directory, n)) for n in names]
    return {"status": "ok", "count": len(results), "results": results}


def _build_interpret_data(db: TrainingPlanDB, doc: PlanDocument,
                          requested_grade: str = "") -> dict[str, Any]:
    top = dict(doc.in_file_meta.get("top", {}) or {})
    # 展示层清洗：总分 "148+（8）" → "148+8"
    top["total"] = parsers.display_credit(str(top.get("total", "")))
    credit_nodes = db.get_credit_nodes(doc.id)
    courses = db.get_courses(doc.id)
    sem_courses = db.get_semester_courses(doc.id)
    edges = db.get_prereq_edges(doc.id, verified_only=False)
    verified_edges = [e for e in edges if e["verified"] == 1]
    grad_reqs = db.get_graduation_reqs(doc.id)

    # 学期地图
    sem_map: dict[str, list[dict]] = {}
    for s in sem_courses:
        item = dict(s)
        item["course_name"] = parsers.display_course_name(item["course_name"])
        item["course_code"] = re.sub(r"([A-Z]{4}\d{6})(?=[A-Z])", r"\1/", item["course_code"] or "")
        sem_map.setdefault(item["semester"], []).append(item)
    semesters = sorted(sem_map.keys(), key=lambda x: (_SEM_ORDER.index(x) if x in _SEM_ORDER else 99))

    # 先修链（已校对边，最长链）
    chains = _build_chains(verified_edges)

    return {
        "major": doc.major,
        "entry_year": doc.entry_year,
        "requested_grade": requested_grade,
        "applies_to": doc.applies_to,
        "version_label": doc.version_label or doc.file_name,
        "top": top,
        "credit_tree": _credit_tree(credit_nodes, top),
        "graduation_requirements": [
            {"type": g["req_type"], "item": g["item"], "value": g["value"], "unit": g["unit"]}
            for g in grad_reqs
        ],
        "semester_map": [
            {"semester": s, "courses": sem_map[s],
             "credit": round(sum(c["credit"] or 0 for c in sem_map[s]), 1)}
            for s in semesters
        ],
        "course_count": len(courses),
        "course_type_summary": course_type_summary(db, doc.id),
        "prereq": {
            "verified": len(verified_edges),
            "unverified": len(edges) - len(verified_edges),
            "edges": verified_edges,
            "chains": chains,
        },
        "prereq_verified": len(verified_edges) > 0 and len(edges) == len(verified_edges),
    }


def _credit_tree(nodes: list[dict], top: dict) -> dict[str, Any]:
    """把扁平节点组装成顶层树（展示层清洗学分文本）。

    结构来源：Table 0 的顶层分类（课程教学 / 集中实践 / 课外实践）。
    课程教学子项按 Table 0 的二级标签归并；**当 Table 0 二级小计为合并值
    （如"专业大类基础+专业课程"共 61）时，回落到按课程类型聚合**，保证
    前端占比条/明细始终有可靠的子项（2026-09-23 修正）。
    """
    def _credit(n: dict) -> str:
        return parsers.display_credit(n["credit"])

    # 顶层节点
    top_children: dict[str, dict[str, Any]] = {}
    for n in nodes:
        if n["category"] == "毕业要求":
            continue
        if n["parent_path"] == "":
            top_children.setdefault(n["name"], {
                "name": n["name"], "credit": _credit(n),
                "note": n.get("credit_note", ""), "children": [],
            })

    # 课程教学子项：优先 Table 0 二级节点
    subs = [n for n in nodes if n["category"] == "课程教学" and n["parent_path"] == "课程教学"]
    ct = top_children.get("课程教学")
    if ct is not None and subs:
        for n in subs:
            ct["children"].append({"name": n["name"], "credit": _credit(n),
                                   "note": n.get("credit_note", "")})

    total = parsers.display_credit(str(top.get("total", ""))) or ""
    children = [top_children[k] for k in ("课程教学", "集中实践", "课外实践")
                if k in top_children]
    # 其余顶层项（如有）追加在后
    children += [v for k, v in top_children.items()
                 if k not in ("课程教学", "集中实践", "课外实践")]
    return {"name": "毕业总学分", "credit": total, "children": children}


def course_type_summary(db: TrainingPlanDB, plan_id: int) -> list[dict[str, Any]]:
    """按课程类型聚合学分（前端学分结构明细的可靠来源）。"""
    rows = db.conn.execute(
        "SELECT course_type, SUM(credit) AS c, COUNT(*) AS n FROM plan_course"
        " WHERE plan_id=? GROUP BY course_type ORDER BY c DESC",
        (plan_id,),
    ).fetchall()
    return [{"name": r["course_type"], "credit": round(r["c"] or 0, 1), "count": int(r["n"])}
            for r in rows]


def _build_chains(edges: list[dict], max_chain: int = 5) -> list[list[str]]:
    """从已校对边构造关键前导链（取最长若干条）。"""
    adj: dict[str, list[str]] = {}
    indeg: dict[str, int] = {}
    for e in edges:
        f, t = e["from_course_name"], e["to_course_name"]
        adj.setdefault(f, []).append(t)
        indeg[t] = indeg.get(t, 0) + 1
        indeg.setdefault(f, indeg.get(f, 0))
    chains: list[list[str]] = []

    def dfs(node: str, path: list[str]):
        nxt = [x for x in adj.get(node, []) if x not in path]
        if not nxt:
            if len(path) >= 2:
                chains.append(list(path))
            return
        for n in nxt:
            dfs(n, [*path, n])

    for n in list(indeg.keys()):
        if indeg.get(n, 0) == 0:
            dfs(n, [n])
    chains.sort(key=len, reverse=True)
    # 去重（保留最长）
    out, seen = [], set()
    for c in chains:
        key = tuple(c)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= max_chain:
            break
    return out


def _credit_head(top: dict[str, Any]) -> str:
    """一、学分结构 行（含课程教学/集中实践/课外实践小计，有则拼括号）。"""
    total = top.get("total", "")
    ct = top.get("course_teaching")
    pr = top.get("practice")
    ep = top.get("extra_practice")
    head = f"一、学分结构：毕业总学分 {parsers.display_credit(str(total))}"
    if ct is not None or pr is not None:
        parts = []
        if ct is not None:
            parts.append(f"课程教学 {ct:g}")
        if pr is not None:
            parts.append(f"集中实践 {pr:g}")
        if ep is not None:
            parts.append(f"课外实践 {ep:g}")
        head += f"（{' · '.join(parts)}）"
    return head


def _credit_section(data: dict[str, Any], lines: list[str]) -> None:
    """一、学分结构：头部 + 课程教学二级小计 + 课程总表门数。"""
    lines.append(_credit_head(data["top"]))
    subs = [c for c in data["credit_tree"]["children"] if c["name"] == "课程教学"]
    if subs:
        for c in subs[0]["children"]:
            lines.append(f"   {c['name']} {c['credit']}")
    lines.append(f"   课程总表 {data['course_count']} 门")


def _semester_section(data: dict[str, Any], lines: list[str]) -> None:
    """二、四年课程地图：前四个学期（每学期课程名≤6，超出加 等）。"""
    sem = data["semester_map"]
    if not sem:
        return
    lines.append(f"二、四年课程地图：共 {len(sem)} 个学期")
    for s in sem[:4]:
        names = "、".join(c["course_name"] for c in s["courses"][:6])
        more = " 等" if len(s["courses"]) > 6 else ""
        lines.append(f"   {s['semester']}：{names}{more}（{s['credit']:g} 学分）")


def _prereq_section(data: dict[str, Any], lines: list[str]) -> None:
    """三、先修关系：待校对 / 已校对前导链 / 暂无 三分支。"""
    p = data["prereq"]
    if p["verified"] == 0 and p["unverified"] > 0:
        lines.append(f"三、先修关系：已抽取 {p['unverified']} 条待管理员校对，暂不展示。")
    elif p["chains"]:
        lines.append("三、先修关系（已校对）关键前导链：")
        for ch in p["chains"][:3]:
            lines.append("   " + " → ".join(ch))
        if p["unverified"]:
            lines.append(f"   （另有 {p['unverified']} 条待校对，暂不展示）")
    else:
        lines.append("三、先修关系：暂无已校对的前导链。")


def _render_summary(data: dict[str, Any]) -> str:
    major, year = data["major"], data["entry_year"]
    lines = [f"【{major} · {year} 培养方案解读】"]
    applies = data.get("applies_to") or []
    if applies and applies != [year]:
        lines.append(f"（该方案适用于：{'、'.join(applies)}）")

    # 一、学分结构
    _credit_section(data, lines)
    # 二、四年课程地图
    _semester_section(data, lines)
    # 三、先修关系
    _prereq_section(data, lines)

    # 四、毕业/学位硬条件
    reqs = data["graduation_requirements"]
    if reqs:
        lines.append("四、毕业/授学位硬条件：")
        for r in reqs:
            unit = r["unit"] or ""
            val = r["value"] or ""
            lines.append(f"   [{r['type']}] {r['item']}：{val}{unit}")
    lines.append("完整图表请打开『功能』→『培养方案解读』。")
    return "\n".join(lines)
