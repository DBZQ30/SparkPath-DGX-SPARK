"""上传/选课检查编排（2026-08-31 方向重构）。权限：admin/owner（H2）。"""
import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .rules import Prep

from .db import WarningDB
from .export import build_report_xlsx
from .models import SourceFile, Grade, Selection
from .parsers import (
    parse_plan, parse_selection, parse_grades, parse_roster,
    canonical_major, clean_course_name, EXCLUDED_CATEGORIES, representative_by_course,
)

logger = logging.getLogger("academicwarning.service")

FILE_TYPES = ("plan", "selection", "grade", "roster", "gen_ed")
TYPE_NAME = {"plan": "培养方案", "selection": "选课结果", "grade": "成绩单",
             "roster": "学籍名单", "gen_ed": "通识课程信息表"}
# v1.7 标准专业名单（与 parsers.MAJOR_CANONICAL 的 4 个标准值一致，测试断言）：
# 状态区分组/占位行/缺专业提示的依据；非标准 major（历史脏数据）归"其他"区
STANDARD_MAJORS = ["工商管理", "大数据管理与应用", "工业工程", "会计学（ACCA）"]

# 培养方案学分结构 JSON（2026-08-31 用户提供的权威毕业要求：模块课程/专业选修等）
_CREDIT_STRUCT_FILE = Path(__file__).resolve().parent / \
    "docs/大数据管理与应用-工商管理-工业工程-会计学-ACCA培养方案学分结构.json"


def load_credit_req() -> dict[str, dict[str, float]]:
    """读取学分结构 JSON → {标准专业名: {类别: 毕业要求学分}}。

    只取"按毕业要求全量"的类别（模块课程/专业选修课程——4-1 前已全部开课，
    应修满）；其余类别（公共/学科门类/专业大类/核心/集中实践）用 Table 1
    进度口径（≤ 当前学期，自动排除毕业设计等未来课程），课外实践无课程不计。
    留学生豁免（2026-08-31 用户确认：留学生不学英语/思政/军训）：公共课程
    要求减去"思想政治理论+大学英语+军事理论"（如 25−15−6−2=2，仅剩体育）。"""
    import json as _json
    data = _json.load(open(_CREDIT_STRUCT_FILE, encoding="utf-8"))
    out: dict[str, dict[str, float]] = {}
    for major in data:
        name = major["name"].replace("专业", "")
        req: dict[str, float] = {}

        def _walk(node):
            n = node.get("name", "")
            if n in ("模块课程", "专业选修课程"):
                req[n] = float(str(node.get("credit", "0")).replace("学分", ""))
            if n == "公共课程":
                pub = float(str(node.get("credit", "0")).replace("学分", ""))
                req["公共课程"] = pub
                exempt = 0.0
                for k in node.get("children", []):
                    if k.get("name") in ("思想政治理论", "大学英语", "军事理论"):
                        exempt += float(str(k.get("credit", "0")).replace("学分", ""))
                req["公共课程_留学生豁免"] = exempt
            for k in node.get("children", []):
                _walk(k)

        _walk(major)
        out[name] = req
    return out


# 通识课程信息表（2026-09-07 D5 起作通识选课学分权威：补全顺序置于全校众数之前；
# 2026-09-07 全局单份上传体系：source_file(file_type='gen_ed', grade='') 最新
# done 版生效，未上传回落到内置 docs/ 默认表；上传/删除后经缓存键失效重载）
_GEN_ED_TABLE_FILE = Path(__file__).resolve().parent / \
    "docs/通识课程信息表.xlsx"
_GEN_ED_TABLE = None        # 惰性加载缓存 (by_code, by_name)；None=未加载
_GEN_ED_TABLE_KEY = None    # 缓存对应源标识（None=内置默认 / source_file id）


def _parse_gen_ed_xlsx(path) -> tuple[dict[str, float], dict[str, float]]:
    """解析通识表 xlsx → (课号→学分, clean课名→学分)；表结构异常由调用方兜底。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb["基本信息"]
    by_code, by_name = {}, {}
    for r in list(ws.iter_rows(values_only=True))[1:]:
        if not r or not r[1]:
            continue
        try:
            cr = float(str(r[8]).strip())
        except (TypeError, ValueError):
            continue
        by_code[str(r[1]).strip()] = cr
        nm = clean_course_name(str(r[2]))
        if nm:
            by_name.setdefault(nm, cr)
    wb.close()
    return by_code, by_name


def _resolve_gen_ed_path(db=None) -> tuple[Path, object]:
    """生效源：(最新上传 done 的 gen_ed source_file, 其 id)；无 → (内置默认表, None)。"""
    if db is not None:
        row = db.conn.execute(
            "SELECT id, file_path FROM source_file WHERE file_type='gen_ed'"
            " AND parsed_status='done' ORDER BY id DESC LIMIT 1").fetchone()
        if row and row[1]:
            return Path(row[1]), row[0]
    return _GEN_ED_TABLE_FILE, None


def _load_gen_ed_table(db=None) -> tuple[dict[str, float], dict[str, float]]:
    """读生效通识表 → (课号→学分, clean 课名→学分)。

    上传版解析失败 → 回落到内置默认表；默认表也失败 → ({}, {})（调用方兜底
    众数/2.0，不阻断 run）。缓存按生效源键失效（上传/删除在 _upload_impl /
    _delete_impl 内同步置 None）。"""
    global _GEN_ED_TABLE, _GEN_ED_TABLE_KEY
    path, key = _resolve_gen_ed_path(db)
    if _GEN_ED_TABLE is not None and key == _GEN_ED_TABLE_KEY:
        return _GEN_ED_TABLE
    try:
        _GEN_ED_TABLE = _parse_gen_ed_xlsx(path)
    except Exception:
        try:
            _GEN_ED_TABLE = _parse_gen_ed_xlsx(_GEN_ED_TABLE_FILE)
        except Exception:
            _GEN_ED_TABLE = ({}, {})
    _GEN_ED_TABLE_KEY = key
    return _GEN_ED_TABLE

# 并发锁（L7，2026-08-28 补实现）：覆盖"解析入库 + 选课检查"关键路径，
# 防止解析与检查并发读到半入库数据
_OP_LOCK = threading.Lock()


def is_admin(platform: str, user_id: str) -> bool:
    from knowledge_base.auth.role_store import resolve_role
    return resolve_role(platform, user_id) in ("admin", "owner")


def _file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sniff_xlsx(path: str) -> str | None:
    """xlsx 首行嗅探：学年学期 → selection；课程号/名/模块 → gen_ed。"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path)   # 不用 read_only（B1 dimension 陷阱）
        ws = wb.active
        row = next(ws.iter_rows(values_only=True))
        wb.close()
        if row and row[0] and "学年学期" in str(row[0]):
            return "selection"
        if row:
            head = "".join(str(c or "") for c in row)
            if "课程号" in head and "课程名" in head and "课程模块" in head:
                return "gen_ed"
    except Exception:
        pass
    return None


def _sniff_docx(path: str) -> str | None:
    """docx 嗅探：正文含"培养方案" → plan；表 1 表头课程+成绩 → grade。"""
    try:
        from docx import Document
        doc = Document(path)
        for p in doc.paragraphs[:10]:
            if "培养方案" in p.text:
                return "plan"
        if doc.tables:
            hdr = [c.text for c in doc.tables[0].rows[1].cells][:3]
            if "课程" in "".join(hdr) and "成绩" in "".join(hdr):
                return "grade"
    except Exception:
        pass
    return None


def detect_file_type(path: str, chat_hint: str = "") -> str:
    """三级识别：文件内信息 → 文件名 → 对话说明（§6.1 第2条）。"""
    base = os.path.basename(path)
    # 1) 文件内信息
    if base.endswith(".xlsx"):
        sniffed = _sniff_xlsx(path)
        if sniffed:
            return sniffed
    if base.endswith(".docx"):
        sniffed = _sniff_docx(path)
        if sniffed:
            return sniffed
    # 2) 文件名关键词
    if "培养方案" in base:
        return "plan"
    if "选课" in base:
        return "selection"
    if "成绩单" in base:
        return "grade"
    # 3) 对话说明
    for t, kw in (("plan", "培养方案"), ("selection", "选课"), ("grade", "成绩单")):
        if kw in chat_hint:
            return t
    raise ValueError(f"无法识别文件类型: {path}")


def upload_file(path: str, chat_hint: str = "", uploader: str = "",
                platform: str = "wecom", db: WarningDB | None = None) -> str:
    """上传入库主流程。返回回复文本（§6.1）。L7：并发锁包裹写库关键路径。"""
    if not is_admin(platform, uploader):
        return "无权限：仅管理员可上传数据文件（H2）"
    with _OP_LOCK:
        return _upload_impl(path, chat_hint, uploader, platform, db)


def selection_main_grade(rows: list[dict]) -> str:
    """选课解析行 → 主年级（"年级"列众数；无有效年级返回 ''）。"""
    from collections import Counter
    cnt = Counter(str(r.get("grade", "")).strip() for r in rows)
    if not cnt:
        return ""
    main, _ = cnt.most_common(1)[0]
    return main if re.fullmatch(r"20\d\d级", main) else ""


def _parse_upload_payload(path: str, ftype: str, fname: str,
                          major_hint: str | None, grade_hint: str) \
        -> tuple[dict | None, tuple | str]:
    """上传解析阶段：按 *ftype* 解析文件 → (in_file_meta, 待入库 payload)。

    软退出（不记 failed 的友好回复）返回 ``(None, reply)``：roster 年级不匹配、
    未知类型。解析失败 raise ValueError（调用方统一记 failed 并友好回复）。
    payload 首元素为 kind 标记（plan/selection/grade/roster/gen_ed），
    其余字段由对应 commit handler 解包。"""
    if ftype == "plan":
        meta, courses, sem_courses, bad_sems = parse_plan(path)
        # M1 标准专业名；v1.7 兜底：识别值无标准词 → 用所选专业（改名文件归对分区）
        raw = meta.get("major", "")
        major = canonical_major(raw) or major_hint or raw or "未知"
        return {"major": major}, \
            ("plan", major, courses, sem_courses, bad_sems,
             meta.get("elective_req", 0.0))

    if ftype == "selection":
        meta, rows, status_note = parse_selection(path)
        in_file_meta = {"semester_label": meta["semester_label"],
                        "semester_code": meta["semester_code"],
                        "grades": meta["grades"],
                        "entry_year": meta["entry_year"]}   # v6 Task4：入学年（可能 0）
        return in_file_meta, ("selection", meta, rows, status_note)

    if ftype == "grade":
        # v1.9：major 由调用方在判重前定好（原始文件名 → 所选专业；识别不出已
        # 拒绝），不再用落盘名（微信临时 hash 名）当专业名兜底。
        major = canonical_major(fname) or major_hint or ""
        students_grades, failed_idx = parse_grades(path)
        in_file_meta = {"major": major,
                        "ocr_failed_indices": failed_idx}   # B4：OCR 失败持久化
        return in_file_meta, ("grade", major, students_grades, failed_idx)

    if ftype == "gen_ed":
        try:
            by_code, _ = _parse_gen_ed_xlsx(path)
        except Exception as exc:
            raise ValueError(f"通识课程信息表解析失败：{exc}") from exc
        if not by_code:
            raise ValueError("通识课程信息表为空或缺少有效学分列（需 课程号/课程名/课程模块/学分）")
        return {"rows": len(by_code)}, ("gen_ed", len(by_code))

    if ftype == "roster":
        meta, rows = parse_roster(path)
        # I-1：空文件/表头不符等解析 0 行 → 按解析失败记录（不 done、不删旧名单）
        if not rows:
            raise ValueError("学籍名单解析为空（0 名学生），请检查文件是否为学籍名单")
        # v6.5 年级校验（防传错，文案同 selection）：文件年级列主年级 vs 所选年级
        main = meta.get("grade", "")
        if grade_hint and main and main != grade_hint:
            return None, (f"文件学生主年级为「{main}」，与所选年级「{grade_hint}」"
                          f"不匹配，请确认是否传错")
        return {"grade": main, "majors": meta.get("majors", {}),
                "count": len(rows)}, ("roster", main, rows)

    return None, "未知文件类型，未入库"


def _commit_plan(db: WarningDB, payload: tuple, fid: int, fname: str,
                 grade_hint: str, now: str) -> str:
    """plan 入库 + 回复。L7：版本号按同专业历史 +1。"""
    _, major, courses, sem_courses, bad_sems, elective_req = payload
    prev = db.conn.execute(
        "SELECT COUNT(*) FROM training_plan WHERE major_name=?", (major,)
    ).fetchone()[0]
    plan_id = db.insert_plan_meta(major, f"v{prev + 1}", fname, now,
                                  grade=grade_hint, elective_req=elective_req)
    db.insert_plan_courses(plan_id, courses)
    db.insert_plan_semester_courses(plan_id, sem_courses)
    note = (f"培养方案入库：专业「{major}」，课程 {len(courses)} 门，"
            f"推荐课表 {len(sem_courses)} 条")
    if bad_sems:
        note += f"；未识别学期 {len(bad_sems)} 个（见清单）"
    return f"{note}；上传时间 {now}"


def _commit_selection(db: WarningDB, payload: tuple, fid: int,
                      grade_hint: str) -> str:
    """selection 入库（学分从培养方案关联——选课结果无学分列）+ 锁内自动
    选课检查。v6：selection 行落 grade（主年级分区，与 source_file.grade 一致）。"""
    from .models import Selection as Sel
    _, meta, rows, status_note = payload
    sels = [Sel(student_id=r["student_id"], name=r.get("name", ""),
                major=r.get("major", ""), grade=grade_hint,
                semester_label=meta["semester_label"],
                semester_code=meta["semester_code"], course_code=r["course_code"],
                course_name=r["course_name"], nature=r["nature"], category=r["category"],
                status=r["status"], retake=r["retake"], source_file_id=fid)
            for r in rows if r["student_id"]]
    plans = db.latest_active_plans()
    plan_courses = {}
    for pid in plans.values():
        for c in db.get_plan_courses(pid):
            plan_courses[c.course_code] = c.credit
    for s in sels:
        s.credit = plan_courses.get(s.course_code, 0.0)
    db.insert_selections(sels)
    # 2026-08-31：选课合理性检查（上传后自动，锁内同步执行；摘要进回复）
    # v6 Task4：按文件年级分区独立检查（grade_hint 空 = 旧客户端不分区，行为同前）
    _, check_note = run_selection_check(db, grade_hint)
    return (f"选课结果入库：{meta['semester_label']}，{len(sels)} 条，"
            f"{len({s.student_id for s in sels})} 名学生；{status_note}\n"
            f"{check_note}")


def _commit_grade(db: WarningDB, payload: tuple, fid: int) -> str:
    """grade 入库。H1：成绩单不做删除——计算时经 latest_source_files_by_major
    ("grade") 按专业取最新，旧文件数据保留在库但不被读取
    （test_upload_two_grade_files_both_kept 断言两份都在）。"""
    _, _major, students_grades, failed_idx = payload
    for _sid, _name, grades in students_grades:
        for g in grades:
            g.source_file_id = fid
        db.insert_grades(grades)
    return (f"成绩单入库：{len(students_grades)} 名学生"
            f"（OCR 失败 {len(failed_idx)} 份：第 {failed_idx[:5]} 份学号无法识别，"
            f"已记录，请人工核对后重传/补录）"
            if failed_idx else f"成绩单入库：{len(students_grades)} 名学生")


def _commit_roster(db: WarningDB, payload: tuple, fid: int, grade_hint: str) -> str:
    """roster 入库。同年级重传 = 名单版本更新：先删旧行再插新（单事务，
    要么全成功要么全不生效）。"""
    _, main, rows = payload
    for r in rows:
        r["source_file_id"] = fid
    db.replace_roster(main or grade_hint, rows)
    return f"学籍名单入库：{main or grade_hint or ''} {len(rows)} 名学生"


def _commit_gen_ed(db: WarningDB, payload: tuple) -> str:
    """gen_ed 入库。全局单份生效：新表为最新 done 版；缓存清零 + 源键失配
    双保险（下次 load 自动重载新表）。"""
    global _GEN_ED_TABLE, _GEN_ED_TABLE_KEY   # 修复：原局部赋值是无效的空操作
    _, rows_n = payload
    _GEN_ED_TABLE = None
    _GEN_ED_TABLE_KEY = None
    return (f"通识课程信息表入库：{rows_n} 门课程（全局单份，覆盖旧版），"
            "下次运行选课检查时作为通识课学分权威生效")


def _record_upload_failed(db: WarningDB, exc: Exception, *,
                          queued_sf_id: int | None, ftype: str, fname: str,
                          fhash: str, path: str, uploader: str,
                          grade_hint: str, now: str) -> None:
    """M5：解析失败落 failed 记录。v1.10 占位已存在 → 原地收尾 failed
    （不新增第二条记录）；否则 insert 新行。

    占位路径的 in_file_meta 合并写入（保留上传时记下的 major，只补 error）：
    整体替换会让 plan/grade 的 failed 记录丢专业 → /status 按专业归组取不到，
    显示回退成"未上传"而非"解析失败"（test_upload_failed_finishes_queued_as_failed
    首次在开发机跑真实样本时暴露，2026-09-26）。"""
    if queued_sf_id is not None:
        row = db.conn.execute(
            "SELECT in_file_meta FROM source_file WHERE id=?",
            (queued_sf_id,)).fetchone()
        meta = json.loads(row[0] or "{}") if row else {}   # 连接未设 row_factory → 元组取列
        meta["error"] = str(exc)
        db.finish_source_file(queued_sf_id, "failed", in_file_meta=meta,
                              file_hash=fhash or None)
    else:
        db.insert_source_file(SourceFile(
            file_type=ftype, file_name=fname, file_hash=fhash, file_path=path,
            upload_time=now, uploader=uploader, parsed_status="failed",   # M5
            in_file_meta={"error": str(exc)}, grade=grade_hint))


def _upsert_source_file(db: WarningDB, *, queued_sf_id: int | None, ftype: str,
                        fname: str, fhash: str, path: str, uploader: str,
                        grade_hint: str, now: str, in_file_meta: dict) -> int:
    """落库 source_file → fid。占用占位行（v1.10）原地收尾 done（保持 id 不变，
    note 由 API 层回写）；旧路径 insert 新行。"""
    if queued_sf_id is not None:
        db.finish_source_file(queued_sf_id, "done", in_file_meta=in_file_meta,
                              file_hash=fhash or None)
        return queued_sf_id
    return db.insert_source_file(SourceFile(
        file_type=ftype, file_name=fname, file_hash=fhash, file_path=path,
        upload_time=now, uploader=uploader, parsed_status="done",
        in_file_meta=in_file_meta, grade=grade_hint))


def _upload_impl(path: str, chat_hint: str = "", uploader: str = "",
                 platform: str = "wecom", db: WarningDB | None = None,
                 force_type: str | None = None,
                 major_hint: str | None = None,
                 orig_name: str | None = None,
                 grade_hint: str = "",
                 queued_sf_id: int | None = None) -> str:
    """上传入库实现（锁内执行，L7）。解析阶段 _parse_upload_payload（软退出 /
    ValueError）→ 落库 source_file（queued 占位原地收尾）→ 按 kind 分发
    _commit_* 入库并回复。

    force_type（007 小程序入口，2026-08-28）：非空时跳过内部类型识别
    （含 hint 级，否则 HTTP 层强校验形同虚设），直接按该类型解析入库；
    None 保持原三级识别逻辑（企微路径零影响）。

    major_hint（v1.7 专业分区）：plan/grade 入库 major 兜底——识别值无法识别
    （无标准词，如改名文件）时用所选专业，保证状态区分组与上传入口一致。

    orig_name（v1.8）：微信上传发送临时文件名（hash），前端随请求携带原始
    文件名；入库 file_name 用原名（状态区展示/轮询匹配），缺省回落盘名。

    grade_hint（v6 年级分区）：文件所属年级（"2023级"）——source_file/
    training_plan/selection 记录落 grade 列（grade 表无 grade 列，按
    source_file.grade 取）；空串 = 未选年级（兼容旧调用不落分区）。
    gen_ed 全局单份，强制清空不分区。

    queued_sf_id（v1.10 上传即占位）：非空时表示调用方（API 层）已先插入一条
    parsed_status='queued' 占位行（id=queued_sf_id），本函数解析完成后**原地
    update 该行为 done/failed**，而非再 insert 一条。None = 旧路径（企微/CLI/
    测试），行为完全不变。"""
    owns_db = db is None   # 仅关闭自建连接；调用方注入的连接由调用方管理生命周期
    db = db or WarningDB()
    if force_type == "gen_ed":
        grade_hint = ""   # 全局单份：不随年级分区（防旧客户端误传年级参数）
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ftype = force_type or ""   # force_type 优先；否则识别失败时为空串（failed 记录不参与判重，I1）
    fname = orig_name or os.path.basename(path)   # v1.8：原名优先（微信临时名兜底）
    fhash = ""
    try:   # M5：类型识别/读文件/解析异常统一兜底——友好回复而非 traceback
        ftype = force_type or detect_file_type(path, chat_hint)
        fhash = _file_md5(path)

        # v1.9：成绩单专业名必须先定（原始文件名优先 → 所选专业兜底）。落盘名是
        # 微信上传的临时 hash 名，识别不出专业；若放任其兜底成 major，会以垃圾
        # 专业名入库（错误分区），且判重会把之后正确专业的上传挡掉——表现为前端
        # 立刻显示成功、但成绩单分区仍显示未上传。识别不出 → 明确拒绝。
        grade_major = ""
        if ftype == "grade":
            grade_major = canonical_major(fname) or major_hint or ""
            if not grade_major:
                raise ValueError("无法识别成绩单所属专业，请在页面上选择专业后重传")

        # M2：同内容判重（dedup_check 单点实现）
        # v6 年级分区：带 grade_hint（空 = 旧调用不带条件，跨年级同内容可重复注册）
        dup = dedup_check(db, fhash, ftype, grade_hint, grade_major)
        if dup:
            return dup
        if ftype == "gen_ed":
            grade_hint = ""   # 全局单份：不随年级分区（防旧客户端误传 grade）

        # B3：in_file_meta 必须在 insert_source_file 之前确定（insert 时即序列化落库）
        in_file_meta, payload = _parse_upload_payload(
            path, ftype, fname, major_hint, grade_hint)
        if in_file_meta is None:
            return payload   # 软退出（roster 年级不匹配 / 未知类型）
    except Exception as exc:
        try:
            _record_upload_failed(db, exc, queued_sf_id=queued_sf_id,
                                  ftype=ftype, fname=fname, fhash=fhash,
                                  path=path, uploader=uploader,
                                  grade_hint=grade_hint, now=now)
        finally:   # 记录插入再抛错也不泄漏连接
            if owns_db:
                db.close()
        return f"文件解析失败（{type(exc).__name__}: {exc}），请检查文件内容后重传"

    # 落库 source_file：v1.10 占位行原地收尾 done（保持 id 不变，note 由 API
    # 层回写）；旧路径 insert 新行
    fid = _upsert_source_file(db, queued_sf_id=queued_sf_id, ftype=ftype,
                              fname=fname, fhash=fhash, path=path,
                              uploader=uploader, grade_hint=grade_hint, now=now,
                              in_file_meta=in_file_meta)

    handler = {
        "plan": lambda: _commit_plan(db, payload, fid, fname, grade_hint, now),
        "selection": lambda: _commit_selection(db, payload, fid, grade_hint),
        "grade": lambda: _commit_grade(db, payload, fid),
        "roster": lambda: _commit_roster(db, payload, fid, grade_hint),
        "gen_ed": lambda: _commit_gen_ed(db, payload),
    }.get(payload[0])
    if handler is None:
        # 类型分发在 upload() 前置嗅探/校验，正常到不了这里；显式兜底契约（-> str）
        return f"未处理的文件类型：{payload[0] or '未知'}，未入库"
    try:
        return handler()
    finally:
        if owns_db:
            db.close()


def _delete_impl(file_type: str, major: str | None = None,
                 grade: str | None = None) -> str:
    """v1.7 删除（锁内执行，与 _upload_impl 同模式）：与上传分区一一对应——
    plan/grade 按专业物理删除全部记录（含历史版本，不可恢复）+ plan 级联；
    其他类型整类删除。v6 年级分区：grade 非空时仅删该年级分区，为空=全删。
    删除后该分区回"未上传"（重新上传可正常入库）。
    gen_ed = 全局单份（记录 grade=''）：客户端可能带年级参数，强制全局删除
    （与上传侧 grade_hint 归一对称，2026-09-07）。"""
    if file_type == "gen_ed":
        grade = None
    db = WarningDB()
    try:
        sfs = db.delete_major_files(file_type, major, grade)
        if file_type == "gen_ed":
            _GEN_ED_TABLE = None
            _GEN_ED_TABLE_KEY = None   # 回退内置默认表，下次 load 生效
    finally:
        db.close()
    if not sfs:
        scope = f"「{major}」" if major else ""
        scope += f"「{grade}」" if grade else ""
        return f"暂无{TYPE_NAME[file_type]}{scope}文件可删除"
    latest = sfs[-1]
    scope = f"{major} " if major else ""
    scope += f"{grade} " if grade else ""
    return (f"已删除{TYPE_NAME[file_type]}文件：{scope}{latest.file_name}"
            f"（共 {len(sfs)} 份，上传于 {latest.upload_time}）")


def dedup_check(db: WarningDB, file_hash: str, file_type: str,
                grade: str = "", major: str = "") -> str | None:
    """M2 同内容判重（service/API 共用单点）：命中 → "已上传过"回复；未命中 → None。
    2026-09-24 去掉「有效期」概念：不再有"过期 → 刷新时间戳"分支——文件不再过期，
    同内容重复上传直接提示已上传，不影响计算（原 M2 的 hash×新鲜度死角随之消失）。
    v6 年级分区：grade 非空时仅在该年级分区内判重（跨年级同内容不互斥）。
    v1.9 major：grade 传非空专业名时追加专业维度判重——否则同一份成绩单
    被错误专业注册后，正确专业的上传会被判重挡掉（前端立刻显示成功，
    但成绩单分区仍显示未上传）。"""
    existing = db.file_hash_exists(file_hash, file_type, grade, major or None)
    if existing is None:
        return None
    return f"该文件已上传过（{existing.upload_time}），无需重复上传"


def gen_ed_credit_override(grades: list[Grade], selections: list[Selection],
                           db: WarningDB | None = None) -> None:
    """通识选课学分补全（原地修改 Selection.credit，仅 credit<=0 的通识课）。

    db 传入时学分权威 = 全局最新上传的通识表（source_file gen_ed），无上传/未传
    db 时回落内置默认表。

    候选 = 课程号/课名在通识课程信息表 或 类别为通识类；跨选/辅修课不参与
    （EXCLUDED_CATEGORIES，与 rules 统计口径一致）。学分顺序：本人成绩单同名
    （clean）→ 表课程号 → 表 clean 课名 → 全校同名众数 → 2.0 估算。表缺失/
    损坏时回落众数/2.0 兜底链，不抛异常阻断 run。"""
    from collections import Counter
    gen_cats = ("基础通识类核心课", "基础通识类选修课")
    mode_by_clean: dict[str, float] = {}
    cnt_by_clean: dict[str, Counter] = {}
    for g in grades:
        cn = clean_course_name(g.course_name)
        if not cn:
            continue
        cnt_by_clean.setdefault(cn, Counter())[g.credit] += 1
    for cn, c in cnt_by_clean.items():
        mode_by_clean[cn] = c.most_common(1)[0][0]
    try:
        by_code, by_name = _load_gen_ed_table(db)
    except Exception:
        by_code, by_name = {}, {}   # 通识表缺失/损坏 → 回落原众数/2.0 链
    for s in selections:
        if s.credit > 0 or s.category in EXCLUDED_CATEGORIES:
            continue   # 跨选/辅修课不参与（EXCLUDED 语义）
        cn = clean_course_name(s.course_name)
        if not cn:
            continue   # 清洗后为空名 → 跳过匹配（避免与无关成绩行误匹配）
        if s.course_code not in by_code and cn not in by_name \
                and s.category not in gen_cats:
            continue   # 非通识候选：表无此课 且 类别非通识类
        own = next((g.credit for g in grades
                    if g.student_id == s.student_id
                    and clean_course_name(g.course_name) == cn), None)
        s.credit = (own or by_code.get(s.course_code)
                    or by_name.get(cn) or mode_by_clean.get(cn, 2.0))


# v6.3（2026-09-02）：学期体系 11 个 + 成绩覆盖解析/确认（设计 §2/§4）。
# term_label 有跨届污染（休学/复学/转专业/补修学生页面——2023 级文件含
# "第四学年（2025-2026）" 等错位标签、各 1 名补修生的 "第三学年（2025-2026）
# 第三学期" 记录）。覆盖学期取「全年级整批同步口径」（auto 与 confirm 一致）：
# - 年份归一：只认「学年序号 == 成绩单年份起年 - 入学年 + 1」的标签（设计
#   §4：错位标签不影响最大值），如 第四学年（2025-2026）对 2023 级 = 错位
# - 整批口径（I1 修复）：某学期须同一专业成绩单 ≥2 名学生（归一后）有记录才
#   计入该专业候选——真整批下发每专业数十行，零星补修/复学页 1-2 行，阈值分离
# - 各专业最大学期取最小者：成绩单按专业整批同步下发（3-3 小学期成绩 9-10 月
#   下发时 4 个专业文件同批带 3-3 段）——单专业零星 3-3 不抬升全年级覆盖
SEMESTERS = ["1-1", "1-2", "1-3", "2-1", "2-2", "2-3",
             "3-1", "3-2", "3-3", "4-1", "4-2"]
_COHORT_TERM_RE = re.compile(r"第([一二三四])学年（(\d{4})-\d{4}）第([一二三])学期")


def _cohort_sem(term_label: str, entry_year: int) -> str:
    """term_label → 学期编码（含年份归一）；错位/无法解析 → ""。
    entry_year=0 = 无法校验入学年 → 不做归一（全收，兼容旧数据）。"""
    m = _COHORT_TERM_RE.search(term_label or "")
    if not m:
        return ""
    x = "一二三四".index(m.group(1)) + 1
    y1 = int(m.group(2))
    if entry_year and x != y1 - entry_year + 1:
        return ""   # 错位标签（跨届页面），不影响最大值
    return f"{x}-{'一二三'.index(m.group(3)) + 1}"


def _file_covered_sem(db: WarningDB, sf, entry_year: int) -> str:
    """单份成绩单文件的整批覆盖学期：含该学期（归一后）学生 ≥2 名的最大者；
    无任何学期达到整批 → ""。"""
    per_student: dict[str, set[str]] = {}
    for g in db.get_grades(sf.id):
        sem = _cohort_sem(g.term_label, entry_year)
        if sem:
            per_student.setdefault(g.student_id, set()).add(sem)
    cnt: dict[str, int] = {}
    for sems in per_student.values():
        for sem in sems:
            cnt[sem] = cnt.get(sem, 0) + 1
    cands = [s for s, n in cnt.items() if n >= 2]
    if not cands:
        return ""
    return max(cands, key=lambda s: tuple(map(int, s.split("-"))))


def grade_covered_semester(db: WarningDB, grade: str) -> tuple[str, str]:
    """该年级成绩覆盖学期：(auto 自动解析, 覆盖生效值 max(auto, confirm))。

    auto = 各专业最新成绩单的「整批覆盖学期」（同专业 ≥2 名学生含该学期，
    年份归一）取**最小者**（全年级同步下发口径）；无 → "". covered =
    max(auto, confirm_sem)。"""
    entry = int(grade[:4]) if re.fullmatch(r"20\d\d级", grade or "") else 0
    # v1.10：过滤 queued/failed 占位行——仅已解析成功的成绩单参与学期覆盖判定
    # （占位行子表为空，误读会算成"无覆盖"导致假阴性）
    major_maxes = [_file_covered_sem(db, sf, entry)
                   for sf in db.latest_source_files_by_major("grade", grade).values()
                   if sf.parsed_status == "done"]
    major_maxes = [m for m in major_maxes if m]
    auto = (min(major_maxes, key=lambda s: tuple(map(int, s.split("-"))))
            if major_maxes else "")
    _cur, confirm = db.get_grade_semesters(grade)
    covered = auto
    if confirm and (not covered or tuple(map(int, confirm.split("-"))) >
                    tuple(map(int, covered.split("-")))):
        covered = confirm
    return auto, covered


def confirm_grade_semester(db: WarningDB, name: str, sem: str) -> tuple[bool, str]:
    """人工确认成绩覆盖到 sem（校验成绩单确有该学期整批记录，防误操作）。"""
    cur, _ = db.get_grade_semesters(name)
    auto, _ = grade_covered_semester(db, name)
    if sem not in SEMESTERS:
        return False, "学期格式无效"
    # 校验：确认的学期必须 ≤ 当前学期，且成绩单确实含该学期记录（或 ≤ auto）
    if cur and tuple(map(int, sem.split("-"))) > tuple(map(int, cur.split("-"))):
        return False, f"确认学期 {sem} 晚于当前学期 {cur}"
    if auto and tuple(map(int, sem.split("-"))) <= tuple(map(int, auto.split("-"))):
        # 自动已覆盖 → 无需确认（幂等成功）
        db.set_grade_confirm_sem(name, sem)
        return True, f"成绩覆盖已确认至 {sem}（自动检测已含）"
    # 自动未覆盖 → 须该学期确有整批记录（同一专业成绩单 ≥2 名学生；1 名学生
    # = 转专业/补修个别成绩——2026-09 工商/大数据各仅 1 名补修生有 3-3，不代表
    # 该学期成绩整批下发，防误操作）
    entry = int(name[:4]) if re.fullmatch(r"20\d\d级", name or "") else 0
    has = False
    for sf in db.latest_source_files_by_major("grade", name).values():
        if sf.parsed_status != "done":   # v1.10：占位/失败行子表为空，跳过
            continue
        n_students = len({g.student_id for g in db.get_grades(sf.id)
                          if _cohort_sem(g.term_label, entry) == sem})
        if n_students >= 2:
            has = True
            break
    if not has:
        return False, f"成绩单中未检测到 {sem} 学期记录，无法确认（请先上传含该学期成绩单）"
    db.set_grade_confirm_sem(name, sem)
    return True, f"已确认成绩覆盖至 {sem}"


def _roster_partition(roster, sel_file, db: WarningDB) -> tuple[list, list]:
    """名单按"本选课文件是否有选中记录"二分：(参与计算学生, 未选课名单)。"""
    selected_sids = {r[0] for r in db.conn.execute(
        "SELECT DISTINCT student_id FROM selection WHERE source_file_id=?"
        " AND status='选中' AND student_id != ''", (sel_file.id,))}
    students = [s for s in roster if s.student_id in selected_sids]
    not_selected = [s for s in roster if s.student_id not in selected_sids]
    return students, not_selected


def _fix_grade_ids_by_name(students, grades) -> None:
    """姓名↔名单交叉校正（OCR 学号漏位/错位时按姓名回填，2026-08-27）。"""
    sid_by_name = {s.name: s.student_id for s in students if s.name}
    roster_sids = {s.student_id for s in students}
    for g in grades:
        if g.student_id not in roster_sids and g.student_name in sid_by_name:
            g.student_id = sid_by_name[g.student_name]


def _plan_tables(db: WarningDB, plan_ids: dict) \
        -> tuple[dict[str, list], dict[str, list], dict[str, float]]:
    """方案三表：(课程按专业, 推荐课表按专业, 课程码→学分)。"""
    plan_by_major: dict[str, list] = {}
    sem_by_major: dict[str, list] = {}
    credit_by_code: dict[str, float] = {}
    for major, pid in plan_ids.items():
        pc = db.get_plan_courses(pid)
        plan_by_major[major] = pc
        sem_by_major[major] = db.get_plan_semester_courses(pid)
        for c in pc:
            credit_by_code.setdefault(c.course_code, c.credit)
    return plan_by_major, sem_by_major, credit_by_code


def _sync_gen_ed_credits(db: WarningDB, sel_file, grades, sels) -> None:
    """Task 5：选课文件无学分列，方案外通识课（GNED/CORE/PHED）已选恒 0 →
    模块课程差额虚高误报（孙镜麒）——补全后再算；学分变化落库（展示一致）。"""
    orig = {s.id: s.credit for s in sels if s.id}
    gen_ed_credit_override(grades, sels, db)
    changed = [(s.id, s.credit) for s in sels
               if s.id and s.credit != orig.get(s.id)]   # 仅实际变化的行
    if changed:
        db.refresh_gen_ed_credits(sel_file.id, changed)


def _assemble_check_prep(db: WarningDB, sel_file) -> "Prep | None":
    """装配选课检查 Prep（2026-08-31 精简；v6.5 roster 名单权威基准）。

    名单 = 该年级最新学籍名单（roster：在籍在校）；选课文件仅提供"已选"
    （名单外选课记录不计——按名单学生过滤）。
    名单内无选课记录的学生 → 不参与合理性计算（无已选数据，差额无意义），
    单列为 not_selected 挂在 prep（run 层落选课文件 meta / 提示）。

    返回 None：缺名单（roster 未上传）/方案/学期信息（调用方降级提示）。"""
    g = sel_file.grade or ""   # v6：按选课文件所属年级取数（年级分区隔离）
    roster = db.latest_roster(g)   # v6.5：名单 = 学籍（不再从选课文件提取）
    if not roster:
        return None
    students, not_selected = _roster_partition(roster, sel_file, db)
    # v6.3：年级学期配置 + 成绩覆盖（current_semester 空 = 保持选课文件推断）
    cur_cfg, _ = db.get_grade_semesters(g) if g else ("", "")
    _, covered_cfg = grade_covered_semester(db, g) if g else ("", "")
    plan_ids = db.latest_active_plans(g)
    if not plan_ids:
        return None
    # 2026-08-31：Table 0 专业选修毕业要求（应修口径）
    elective_req = {m: db.conn.execute(
        "SELECT elective_req FROM training_plan WHERE id=?", (pid,)).fetchone()[0]
        for m, pid in plan_ids.items()}
    semester_code = (sel_file.in_file_meta or {}).get("semester_code", "")
    if not semester_code:
        return None
    grade_files = {m: sf for m, sf in db.latest_source_files_by_major("grade", g).items()
                   if sf.parsed_status == "done"}   # L7：仅已解析成功的文件参与计算
    grades = []
    for sf in grade_files.values():
        grades += db.get_grades(sf.id)
    _fix_grade_ids_by_name(students, grades)
    plan_by_major, sem_by_major, credit_by_code = _plan_tables(db, plan_ids)
    # I8：方案后传导致选课学分 0.0 → 按最新方案刷新学分后再组装 Prep
    db.refresh_selection_credits(sel_file.id, credit_by_code)
    sels = db.get_selections(sel_file.id)
    _sync_gen_ed_credits(db, sel_file, grades, sels)
    # M3：名单内但无任何成绩记录的学生 → data_incomplete
    grade_sids = {g.student_id for g in grades}
    ocr_failed = {s.student_id for s in students if s.student_id not in grade_sids}
    # v6 Task4：入学年取选课文件 meta（parse_selection 已算好；无年级列时为 0 → 兜底 2023）
    entry_year = int((sel_file.in_file_meta or {}).get("entry_year") or 2023)
    from .rules import Prep
    # 2026-08-31：学分结构 JSON 毕业要求（模块课程/专业选修）
    credit_req = load_credit_req()
    prep = Prep(students, sels, grades, plan_by_major, sem_by_major,
                [], semester_code, entry_year=entry_year,
                ocr_failed=ocr_failed, elective_req_by_major=elective_req,
                credit_req_by_major=credit_req,
                covered_sem=covered_cfg, current_sem_override=cur_cfg or "")
    prep.not_selected = not_selected   # 未选课名单（roster − 有选课者），run 层落 meta
    return prep


def _apply_waivers(db: WarningDB, prep, grade: str) -> tuple[int, str]:
    """Prep 实例化后应用豁免（v7；W2 注入点——不进 _compute_missing_courses）。

    kind=course → 运行期按 course_code 重解析该生专业当前方案课程（L5 解析
    失败累计跳过提示）→ 构造虚拟成绩行注入 prep.grades（真实同名成绩守卫 L1 +
    覆盖计数排除 R1 由 grade_raw=WAIVER_MARK 标记承担）；kind=credit → 认可
    学分叠加 prep.waiver_credit[sid][category]（多门同类别累加；N1 守卫：旧课
    在成绩单且已匹配方案 → 跳过防双计）。豁免只影响单学生单课程——名单外学生
    跳过不提示。
    返回 (应用条数, 摘要提示文本；跳过原因累计进提示，不静默)。"""
    from .selection_check import WAIVER_MARK, _grade_category
    waivers = db.list_waivers(grade)
    applied, skipped = 0, 0
    notes: list[str] = []
    for w in waivers:
        sid = w["student_id"]
        if sid not in prep.students:
            continue   # 名单外（如已退学）——跳过不提示
        if w["kind"] == "course":
            # L5：运行期按该生专业方案重解析课程（课名/学分以当前方案为准）
            major = prep._major_for(sid)
            pc = next((c for c in prep._plan_by_major.get(major, [])
                       if c.course_code == w["course_code"]), None)
            if pc is None:
                skipped += 1
                notes.append(f"豁免课程《{w['course_name']}》未在当前方案找到（可能方案已更新）")
                continue
            # L1 守卫（2026-09-07 及格制修订）：同课最高分代表行**已及格** →
            # 跳过注入（防虚拟行与真及格行重复计入）；代表行**不及格**（挂科未过，
            # D2 下该课在缺修清单）→ 允许注入——管理员对挂科必修的免修/豁免
            # 特殊处理（用户 2026-09-07 确认：failed 缺修行提供免修入口）
            rep = representative_by_course(prep._student_grades(sid)).get(
                clean_course_name(pc.course_name))
            if rep is not None and rep.pass_flag == 1:
                skipped += 1
                notes.append(f"课程《{w['course_name']}》已有真实及格成绩"
                             "（成绩单优先，豁免跳过）")
                continue
            prep.grades.append(Grade(
                student_id=sid, course_name=pc.course_name, credit=pc.credit,
                grade_raw=WAIVER_MARK, pass_flag=1,
                course_name_clean=clean_course_name(pc.course_name)))
            applied += 1
        elif w["kind"] == "credit":
            # N1 守卫：认可旧课在成绩单且已匹配方案（_grade_category 非 None）→
            # 跳过（防方案重传后 真实成绩归类 + 认可分 双计，与 L1 同款）
            real_cat = next((g for g in prep._student_grades(sid)
                             if clean_course_name(g.course_name)
                             == clean_course_name(w["course_name"])
                             and _grade_category(prep, sid, g) is not None), None)
            if real_cat is not None:
                skipped += 1
                notes.append(f"认可旧课《{w['course_name']}》已匹配方案课程，豁免跳过")
                continue
            prep.waiver_credit.setdefault(sid, {})
            prep.waiver_credit[sid][w["category"]] = \
                prep.waiver_credit[sid].get(w["category"], 0.0) + w["credit"]
            applied += 1
        else:  # kind='grade'（2026-09-07：方案外旧课豁免——纯记录，使该旧课
            # 退出未及格/候选清单；不参与方案学分/缺修，无聚合效果）
            applied += 1
    bits = []
    if applied:
        bits.append(f"豁免 {applied} 条生效")
    if skipped:
        bits.append(f"跳过 {skipped} 条")
    if notes:
        bits.append("；".join(notes))
    return applied, ("（" + "；".join(bits) + "）") if bits else ""


def _precheck_existence_file(db: WarningDB, ftype: str, g: str) \
        -> tuple[bool, bool, str]:
    """单份必需文件（选课/名单）→ (ok, pending, detail 文本)。"""
    sf = db.latest_source_file(ftype, g)
    if sf is None:
        return False, False, "未上传"
    if sf.parsed_status != "done":
        return (False, sf.parsed_status in ("queued", "parsing"),
                f"解析中/失败（{sf.parsed_status}）")
    return True, False, sf.file_name or ""


def _precheck_major_files(db: WarningDB, ftype: str, g: str) \
        -> tuple[bool, bool, str]:
    """4 专业清单（方案/成绩单）→ (ok, pending, detail 文本)。"""
    by_major = db.latest_source_files_by_major(ftype, g)
    missing = [m for m in STANDARD_MAJORS
               if m not in by_major or by_major[m].parsed_status != "done"]
    pending = any(by_major.get(m) is not None
                  and by_major[m].parsed_status in ("queued", "parsing")
                  for m in missing)
    detail = "4 专业齐" if not missing else f"缺 {'、'.join(missing)}"
    return not missing, pending, detail


def precheck_selection(db: WarningDB | None = None, grade: str = "") -> tuple[bool, str]:
    """选课检查数据齐全性预检（2026-09-22，对话触发路径用）。

    在跑 check 前**一次性**列出该年级五类数据的齐备情况，供 skill 直接回报
    （避免逐条降级文本、也避免管理员反复试）。口径与 run_selection_check 的
    守卫同源，但只读、不写库、不产生副作用。

    返回 (ready, pending, text)：
    - ready=True 表示可执行检查（各必需项齐备）
    - pending=True 表示"不齐备的项里至少有一项是解析中/排队中"（而非真缺失）
      —— 供 skill 区分措辞：解析中 → "正在解析，请稍后再试"；
      否则 → "缺少 X，请上传"（缺失/解析失败归入此类）
    - text = 面向用户的齐备清单/缺失说明

    必需项：选课结果（1 份 done）、学籍名单（1 份 done）、培养方案（4 专业齐）、
    成绩单（4 专业齐）；通识表为可选（缺失回落内置/众数，不影响执行）。"""
    owns_db = db is None
    db = db or WarningDB()
    try:
        g = grade or ""
        ok_items, miss_items = [], []
        pending = False   # 任一不齐备项为 queued/parsing → True

        def _mark(label, ok, detail=""):
            (ok_items if ok else miss_items).append(
                f"{label}：{'✓' if ok else '✗'} {detail}".rstrip())

        # 1/2) 选课结果、学籍名单（必需，各 1 份 done）
        for label, ftype in (("选课结果", "selection"), ("学籍名单", "roster")):
            ok, pend, detail = _precheck_existence_file(db, ftype, g)
            pending = pending or pend
            _mark(label, ok, detail)

        # 3/4) 培养方案、成绩单（必需，4 专业齐）
        for label, ftype in (("培养方案", "plan"), ("成绩单", "grade")):
            ok, pend, detail = _precheck_major_files(db, ftype, g)
            pending = pending or pend
            _mark(label, ok, detail)

        # 5) 通识课程信息表（可选）
        gen_ed = db.latest_source_file("gen_ed", "")
        _mark("通识课程信息表", True,
              (gen_ed.file_name if gen_ed and gen_ed.parsed_status == "done"
               else "未上传（可选，将回落到内置/众数口径）"))

        ready = not miss_items
        if ready:
            pending = False
        head = (f"{g}选课检查数据预检：" if g else "选课检查数据预检：")
        if ready:
            body = "数据齐备，可执行选课检查。\n" + "\n".join(ok_items)
        else:
            body = (f"数据不齐备（{len(miss_items)} 项未就绪），暂不能执行选课检查。\n"
                    + "\n".join(miss_items)
                    + ("\n" + "\n".join(ok_items) if ok_items else ""))
        return ready, pending, head + body
    finally:
        if owns_db:
            db.close()


def run_selection_check(db: WarningDB | None = None, grade: str = "") -> tuple[int, str]:
    """执行选课合理性检查（2026-08-31；v6 按年级——上传自动 + 手动重跑共用，锁内调用）。

    返回 (触发人数, 摘要文本)。缺关键数据时降级提示——不产生假阳性提醒。"""
    owns_db = db is None
    db = db or WarningDB()
    try:
        sel_file = db.latest_source_file("selection", grade)
        if sel_file is None:
            return 0, (f"选课检查：尚未上传{grade}选课结果文件" if grade
                       else "选课检查：尚未上传选课结果文件")
        if sel_file.parsed_status != "done":
            return 0, "选课检查：选课结果解析未完成，请稍后重试"
        g = grade or (sel_file.grade or "")   # v6：成绩单守卫按实际取数年级
        # v6.5：名单 = 学籍 roster——缺 roster 时降级提示，不再用选课文件兜底名单
        if not db.latest_roster(g):
            return 0, "选课检查：学籍名单未上传，未执行检查"
        where = " AND grade=?" if g else ""
        n_grade = db.conn.execute(
            "SELECT COUNT(*) FROM source_file WHERE file_type='grade'"
            " AND parsed_status='done'" + where, (g,) if g else ()).fetchone()[0]
        if n_grade == 0:
            return 0, "选课检查：成绩单未上传（已修按 0 计会误判），请先上传成绩单"
        prep = _assemble_check_prep(db, sel_file)
        if prep is None:
            return 0, "选课检查：缺少名单或培养方案，未执行检查"
        from .selection_check import (build_summary, check_selection_rationality,
                                      exempt_courses)
        # v7：豁免装配在 Prep 后、check 前（W2 注入点——虚拟行不进
        # _compute_missing_courses，防单生豁免污染专业级"全员无成绩"判定）
        _, waiver_tip = _apply_waivers(db, prep, g)
        # 毫秒精度：快速连续 run 也形成独立批次（latest_selection_check 按批过滤）
        checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        rows = check_selection_rationality(prep, checked_at)
        for r in rows:
            r.source_file_id = sel_file.id
        db.insert_selection_check_rows(rows, grade)
        # v6：豁免课程清单写入选课文件 meta（前端显著通知管理员核对——
        # 全员无成绩课程从应修/缺修判定中剔除，属数据侧豁免而非学生达标）
        meta = dict(sel_file.in_file_meta or {})
        meta["exempt_courses"] = exempt_courses(prep)
        # v6.5：名单内未选课学生随批次落选课文件 meta（GET /selection-check 读回）
        meta["not_selected"] = [
            {"student_id": s.student_id, "name": s.name,
             "major": s.major, "class_name": s.class_name}
            for s in prep.not_selected]
        db.conn.execute("UPDATE source_file SET in_file_meta=? WHERE id=?",
                        (json.dumps(meta, ensure_ascii=False), sel_file.id))
        db.conn.commit()
        note = build_summary(prep, rows)
        if waiver_tip:   # v7：豁免应用/跳过汇总进 run 摘要（L5 解析失败/N1 跳过不静默）
            note += "\n" + waiver_tip
        if prep.not_selected:   # 未选课 = 名单内无任何已选课程 → 单独提醒（管理员联系核实）
            names = "、".join(s.name for s in prep.not_selected)
            note += (f"\n名单内本学期未选课 {len(prep.not_selected)} 人："
                     f"{names}（未参与合理性计算，请核实）")
        # v6 Task7：检查后自动导出 xlsx 报告（失败不阻断检查——文件写盘异常仅打印）
        try:
            xlsx_path = build_report_xlsx(prep, rows, grade)
            note += f"\n报告已导出：{os.path.basename(xlsx_path)}"
        except Exception as exc:
            logger.warning("报告导出失败: %s", exc, exc_info=True)
        return len(rows), note
    finally:
        if owns_db:
            db.close()


