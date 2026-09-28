"""选课检查名单 xlsx 导出（v6：service 自动导出 + 手动脚本共用）。

报告落盘目录：本包 result/ 子目录（2026-09-07 迁入，见 gitignore academicwarning/result/）。

三个 sheet 与 scripts/export_selection_check.py 原格式一致：
汇总 / 学生类别明细 / 全类别总览。
"""
import json
import os
import re
from datetime import datetime

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill

from .selection_check import (_gained_by_category, _missing_required_courses,
                              _grade_category)
from .rules import _CAT_RENAME
import contextlib

CATS = ["公共课程", "模块课程", "学科门类基础课程", "专业大类基础课程",
        "专业核心课程", "专业选修课程", "集中实践", "课外实践"]

# 报告落盘目录 = 本包 result/ 子目录（2026-09-07；下载端点同目录 glob、
# 测试 conftest 全局把 OUT_DIR 指 tmp 隔离）
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "result")

_HDR_FILL = PatternFill("solid", fgColor="D9E2F3")
_RED_FILL = PatternFill("solid", fgColor="FDE9E9")


def _parse_details(r) -> dict:
    """行 details JSON 解析（损坏/缺失 → {}）。"""
    try:
        return json.loads(r.details or "{}")
    except ValueError:
        return {}


def _gained_with_waivers(prep, sid: str) -> dict:
    """已修学分 + N3 类型B 旧课学分认可叠加——差额与 Sheet1/检查结果同口径。"""
    gained = _gained_by_category(prep, sid)
    for cat, wc in (prep.waiver_credit.get(sid) or {}).items():
        gained[cat] = gained.get(cat, 0.0) + wc
    return gained


def _summary_missing_text(r) -> str:
    """Sheet1"缺修课程（按类别）"列：details 按类别归拢（2026-09-22 起，
    不再有独立"缺修必修课"列）；老批次兜底从 message 提取。"""
    det = _parse_details(r)
    segs = [f"{c['cat']}：" + "、".join(f"《{m['name']}》"
                                        for m in c["missing"])
            for c in det.get("cats", []) if c.get("missing")]
    if det.get("other_missing"):
        segs.append("其他未修/挂科必修课：" + "、".join(
            f"《{m['name']}》" for m in det["other_missing"]))
    if segs:
        return "；".join(segs)
    if "缺修" in r.message:   # 老批次兜底：从 message 提取
        m = re.search(r"缺修 (.*?)，", r.message)
        return m.group(1) if m else "见提醒"
    return ""


def _fill_summary_sheet(ws, rows) -> None:
    """Sheet 1 汇总：触发生每行一条，缺修按类别归拢展示。"""
    ws.title = "汇总"
    ws.append(["学号", "姓名", "专业", "触发类别", "差额(学分)", "缺修课程（按类别）", "提醒内容"])
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = _HDR_FILL
    for r in rows:
        ws.append([r.student_id, r.name, r.major, r.category, r.gap,
                   _summary_missing_text(r), r.message])


def _selection_courses_for(prep, sid: str, cat: str) -> list[str]:
    """该生本学期已选课中归属 *cat* 的明细串（经方案课 course_type 归类）。"""
    s_courses = []
    for s in prep._student_selections(sid):
        pc = prep._plan_by_code_major.get(prep._major_for(sid), {}).get(s.course_code)
        ctype = _CAT_RENAME.get(pc.course_type, pc.course_type) if pc else None
        if ctype == cat:
            s_courses.append(f"{s.course_name}({s.credit}学分)")
    return s_courses


def _student_category_rows(prep, r) -> list[list]:
    """Sheet 2 单生逐类别行：应修/已修/已选/差额 + 已修/已选课程明细。"""
    sid = r.student_id
    expected = prep.required_credits_by_category(sid)
    gained = _gained_with_waivers(prep, sid)
    selected = prep.selected_credits_by_category(sid)
    out = []
    for cat in CATS:
        req = expected.get(cat, 0.0)
        got = gained.get(cat, 0.0)
        sel = selected.get(cat, 0.0)
        g_courses = [f"{g.course_name}({g.credit}学分/{g.grade_raw})"
                     for g in prep._student_grades(sid)
                     if _grade_category(prep, sid, g) == cat]
        s_courses = _selection_courses_for(prep, sid, cat)
        if req or got or sel or g_courses or s_courses:
            out.append([sid, r.name, r.major, cat, req, round(got, 1),
                        round(sel, 1), round(req - got - sel, 1),
                        "；".join(g_courses), "；".join(s_courses)])
    return out


def _detail_missing_segs(prep, r) -> list[str]:
    """Sheet 2"缺修课程（按类别）"行明细段：details 优先；老批次（无
    details 缺修结构）用实时计算兜底。"""
    det = _parse_details(r)
    miss_segs = [f"{c['cat']}：" + "；".join(
                     f"《{m['name']}》({m.get('score') or '未修'})"
                     for m in c["missing"])
                 for c in det.get("cats", []) if c.get("missing")]
    if det.get("other_missing"):
        miss_segs.append("其他未修/挂科必修课：" + "；".join(
            f"《{m['name']}》({m.get('score') or '未修'})"
            for m in det["other_missing"]))
    if not miss_segs:   # 老批次兼容：用实时计算兜底
        missing_req = _missing_required_courses(prep, r.student_id)
        if missing_req:
            miss_segs.append("；".join(
                f"《{n}》" for _, n, _, _, _, _ in missing_req))
    return miss_segs


def _fill_detail_sheet(ws2, prep, rows) -> None:
    """Sheet 2 学生类别明细：逐类别行（差额>0 标红）+ 缺修行
    （2026-09-22：缺修课按类别归入"已修课程明细"列，不再单独一行"缺修必修课"）。"""
    ws2.append(["学号", "姓名", "专业", "课程类别", "应修", "已修", "已选",
                "差额", "已修课程明细", "已选课程明细"])
    for c in ws2[1]:
        c.font = Font(bold=True)
        c.fill = _HDR_FILL
    ws2["F1"].comment = Comment(
        "已修=及格制（同课最高分代表行），明细列含未及格/重修行",
        "academic-warning")
    for r in rows:
        for row in _student_category_rows(prep, r):
            ws2.append(row)
            if row[7] > 0:
                for cc in ws2[ws2.max_row]:
                    cc.fill = _RED_FILL
        miss_segs = _detail_missing_segs(prep, r)
        if miss_segs:
            ws2.append([r.student_id, r.name, r.major, "缺修课程（按类别）",
                        "", "", "", "", "；".join(miss_segs), ""])
            for cc in ws2[ws2.max_row]:
                cc.fill = _RED_FILL


def _fill_overview_sheet(ws3, prep, rows) -> None:
    """Sheet 3 全类别总览：全部类别逐行（无空类别过滤——与 Sheet 2 口径差
    异保持原样），差额>0 标红。"""
    ws3.append(["学号", "姓名", "专业", "课程类别", "应修", "已修", "已选", "差额"])
    for c in ws3[1]:
        c.font = Font(bold=True)
        c.fill = _HDR_FILL
    ws3["F1"].comment = Comment(
        "已修=及格制（同课最高分代表行），明细列含未及格/重修行",
        "academic-warning")
    for r in rows:
        sid = r.student_id
        expected = prep.required_credits_by_category(sid)
        gained = _gained_with_waivers(prep, sid)
        selected = prep.selected_credits_by_category(sid)
        for cat in CATS:
            req = expected.get(cat, 0.0)
            got = gained.get(cat, 0.0)
            sel = selected.get(cat, 0.0)
            row = [sid, r.name, r.major, cat, req, round(got, 1),
                   round(sel, 1), round(req - got - sel, 1)]
            ws3.append(row)
            if row[7] > 0:
                for cc in ws3[ws3.max_row]:
                    cc.fill = _RED_FILL


def _style_sheet(sheet) -> None:
    """列宽自适应（10–55）+ 首行冻结。"""
    for col in sheet.columns:
        maxlen = max(len(str(c.value or "")) for c in col) if sheet.max_row > 1 else 10
        sheet.column_dimensions[col[0].column_letter].width = min(max(maxlen + 2, 10), 55)
    sheet.freeze_panes = "A2"


def build_report_xlsx(prep, rows, grade: str = "") -> str:
    """构建三 sheet 报告并保存，返回文件绝对路径。"""
    wb = Workbook()

    # ── Sheet 1 汇总 ──
    ws = wb.active
    _fill_summary_sheet(ws, rows)

    # ── Sheet 2 学生类别明细 ──
    ws2 = wb.create_sheet("学生类别明细")
    _fill_detail_sheet(ws2, prep, rows)

    # ── Sheet 3 全类别总览 ──
    ws3 = wb.create_sheet("全类别总览")
    _fill_overview_sheet(ws3, prep, rows)

    for sheet in (ws, ws2, ws3):
        _style_sheet(sheet)

    g = f"-{grade}" if grade else ""
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"选课检查名单{g}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx")
    wb.save(out)
    _prune_reports(grade)   # 2026-09-04：防止频繁 run 堆积报告文件
    return out


# 同年级前缀（含无年级）仅保留最近 N 份，旧的删除（前端下载端点取最新，不受影响）
_REPORT_KEEP = 5


def _prune_reports(grade: str) -> None:
    """清理旧报告：同年级前缀保留最新 _REPORT_KEEP 份。

    grade="" 时只清理**无年级段**的文件（选课检查名单-YYYYMMDD-*.xlsx，
    排除含 "-XXXX级-" 的年级文件——glob 前缀会误匹配）。"""
    import glob
    files = glob.glob(os.path.join(OUT_DIR, "选课检查名单*.xlsx"))
    if grade:
        prefix = f"选课检查名单-{grade}-"
    else:
        prefix = "选课检查名单-"
        files = [f for f in files
                 if not re.search(r"-\d{4}级-", os.path.basename(f))]
    files = [f for f in files
             if os.path.basename(f).startswith(prefix)]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for f in files[_REPORT_KEEP:]:
        with contextlib.suppress(OSError):
            os.unlink(f)
