"""专业选择 / 转专业 / 辅修 模拟（设计 005 §6）。

- `simulate_transfer(from_major, to_major, entry_year, student_id="")`：转专业模拟
- `simulate_major_selection(entry_year, student_id="", choices=[])`：专业选择（分流）模拟
- `simulate_minor(...)`：辅修（待教务提供教学计划，本期降级）

口径：**可抵扣/需补修**优先用学生已修课程（`warning.db`，本人只读）；
无学号时退化为**方案级 diff**（两专业课程交集/差集，标注"理论值"）。
不臆造：政策/成绩缺失即降级提示。
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Any, Optional

from . import service
from .db import ALL_GRADES, TrainingPlanDB
from .models import STATUS_DONE
from .parsers import normalize_course_name
from .route import _load_level

# 转入会计学（ACCA）须降级（2026 转专业细则）
_DOWNGRADE_MAJORS = {"会计学（ACCA）"}

# 课程类别：视为"核心/必修"的类别（方案级 diff 用）
_CORE_TYPES = ("专业核心课程", "专业大类基础课程", "数学和基础科学类课程", "公共课程")


# ─────────────────────────── 学期位置推算 ───────────────────────────

def _entry_year_num(entry_year: str) -> Optional[int]:
    m = re.search(r"(20\d\d)", str(entry_year or ""))
    return int(m.group(1)) if m else None


def current_semester_index(entry_year: str, now_year: int = 2026) -> int:
    """当前学期序号（1-based：1=1-1，3=2-1，6=3-2，8=4-2）。

    9 月入学：入学年为第 1 学年。`now_year` 为当前学年起始年（默认 2026）。
    """
    y = _entry_year_num(entry_year)
    if not y:
        return 1
    return max(1, min(8, (now_year - y) * 2 + 1))


def _remaining_semesters(entry_year: str, now_year: int = 2026) -> int:
    return max(0, 8 - (current_semester_index(entry_year, now_year) - 1))


def _deadline_semesters(entry_year: str, now_year: int = 2026) -> int:
    """到「第三学年结束（3-2，序号 6）」还剩的学期数。"""
    return max(0, 6 - (current_semester_index(entry_year, now_year) - 1))


# ─────────────────────────── 课程匹配 / 抵扣 ───────────────────────────

def _course_key(name: str) -> str:
    return normalize_course_name(name)


def _index_courses(courses: list[dict]) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for c in courses:
        k = _course_key(c.get("course_name", ""))
        if k:
            idx.setdefault(k, c)
    return idx


def _plan_diff(source_courses: list[dict], target_courses: list[dict]) -> tuple[list, list]:
    """方案级 diff：target 中与 source 同名的 → 可抵扣；否则 → 需补修。"""
    src_idx = _index_courses(source_courses)
    creditable, must = [], []
    for c in target_courses:
        if c.get("credit", 0) <= 0:
            continue
        k = _course_key(c.get("course_name", ""))
        row = {"course_name": c["course_name"], "credit": round(c.get("credit", 0), 1),
               "course_type": c.get("course_type", "")}
        if k and k in src_idx:
            row["from"] = src_idx[k]["course_name"]
            creditable.append(row)
        else:
            must.append(row)
    return creditable, must


def _student_grades(student_id: str, warning_db: Optional[str] = None) -> list[dict]:
    """读学生已修课程（只读 warning.db）。"""
    from .identity import WARNING_DB_PATH
    path = warning_db or WARNING_DB_PATH
    if not student_id or not os.path.isfile(path):
        return []
    try:
        conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT course_name, course_name_clean, credit, grade_raw, pass_flag"
            " FROM grade WHERE student_id=?", (student_id,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out = []
    for r in rows:
        out.append({"course_name": r["course_name_clean"] or r["course_name"] or "",
                    "credit": r["credit"] or 0, "grade_raw": r["grade_raw"] or "",
                    "pass_flag": int(r["pass_flag"] or 0)})
    return out


def _student_diff(grades: list[dict], target_courses: list[dict]) -> tuple[list, list, list]:
    """学生已修 vs 目标方案：可抵扣（已修且及格）/ 需补修 / 已修但挂科。"""
    idx = _index_courses(grades)
    creditable, must, failed = [], [], []
    for c in target_courses:
        if c.get("credit", 0) <= 0:
            continue
        k = _course_key(c.get("course_name", ""))
        row = {"course_name": c["course_name"], "credit": round(c.get("credit", 0), 1),
               "course_type": c.get("course_type", "")}
        g = idx.get(k) if k else None
        if g and g.get("pass_flag"):
            row["grade"] = g.get("grade_raw", "")
            creditable.append(row)
        elif g and not g.get("pass_flag"):
            row["grade"] = g.get("grade_raw", "")
            failed.append(row)
        else:
            must.append(row)
    return creditable, must, failed


# ─────────────────────────── 转专业模拟 ───────────────────────────

def _find_parsed_doc(db: TrainingPlanDB, major: str, entry_year: str):
    """找专业年级的培养方案，仅 STATUS_DONE 视为可用（否则返回 None）。"""
    doc = service.find_document(db, major, entry_year)
    return doc if doc and doc.parsed_status == STATUS_DONE else None


def _transfer_quota(db: TrainingPlanDB, to_major_n: str, entry_n: str,
                    policy_year: Optional[str]) -> tuple[dict, bool]:
    """接收计划：quota 按 scope 汇总；eligible = 存在正向名额。"""
    quota_rows = db.get_transfer_plans(policy_year=policy_year, major=to_major_n,
                                       entry_year=entry_n)
    quota = {r["scope"]: r["quota"] for r in quota_rows}
    eligible = bool(quota) and sum(quota.values()) > 0
    return quota, eligible


def _diff_for(student_id: str, source_courses: list[dict],
              target_courses: list[dict]) -> tuple[str, list, list, list]:
    """抵扣/补修口径选择：有学号且读到成绩 → 学生级 diff（含挂科），否则方案级 diff。"""
    failed: list[dict] = []
    if student_id:
        gr = _student_grades(student_id)
        if gr:
            creditable, must, failed = _student_diff(gr, target_courses)
            return "student", creditable, must, failed
    creditable, must = _plan_diff(source_courses, target_courses)
    return "plan", creditable, must, failed


def _pressure(db: TrainingPlanDB, doc_id: int, make_up_credits: float,
              entry_n: str) -> dict[str, Any]:
    """压力与红线评估（学期学分上限 + 第三学年末红线 + 负荷等级）。"""
    remaining = _remaining_semesters(entry_n)
    deadline = _deadline_semesters(entry_n)
    avg = round(make_up_credits / deadline, 1) if deadline else make_up_credits
    limit = _credit_limit(db, doc_id)
    deadline_risk = "第三学年末前可完成"
    if deadline == 0:
        deadline_risk = "已过大三（须按学院安排，可能影响推免）"
    elif avg > limit:
        deadline_risk = f"按剩余 {deadline} 学期需每学期约 {avg:g} 学分，超上限 {limit:g}，有风险"
    return {"remaining_semesters": remaining, "deadline_semesters": deadline,
            "avg_load": avg, "credit_limit": limit,
            "load_level": _load_level(avg / limit) if limit else "",
            "deadline_risk": deadline_risk}


def simulate_transfer(from_major: str, to_major: str, entry_year: str = "",
                      student_id: str = "") -> dict[str, Any]:
    from_major_n = service.normalize_major(from_major)
    to_major_n = service.normalize_major(to_major)
    if not to_major_n:
        return {"state": "bad_major", "message": f"无法识别目标专业「{to_major}」"}
    entry_n = service.normalize_entry_year(entry_year) if entry_year else ""

    db = TrainingPlanDB(service.DB_PATH)
    try:
        grades, has_all = db.available_grades(to_major_n)
        year_list = grades + ([ALL_GRADES] if has_all else [])
        if not entry_n:
            return {"state": "need_year", "major": to_major_n, "years": year_list,
                    "message": f"请提供年级（如 2023级）；{to_major_n} 已收录年级："
                               + ("、".join(year_list) if year_list else "暂无")}
        target_doc = _find_parsed_doc(db, to_major_n, entry_n)
        if not target_doc:
            return {"state": "unavailable", "major": to_major_n, "entry_year": entry_n,
                    "years": year_list, "message": f"{to_major_n} {entry_n} 的培养方案尚未收录"}

        target_courses = db.get_courses(target_doc.id)
        source_courses = []
        if from_major_n:
            src_doc = _find_parsed_doc(db, from_major_n, entry_n)
            if src_doc:
                source_courses = db.get_courses(src_doc.id)

        # 接收计划
        policy_year = db.latest_transfer_policy_year()
        quota, eligible = _transfer_quota(db, to_major_n, entry_n, policy_year)

        # 抵扣 / 补修
        source, creditable, must, failed = _diff_for(student_id, source_courses,
                                                     target_courses)
        make_up_credits = round(sum(x["credit"] for x in must), 1)

        # 压力与红线
        pressure = _pressure(db, target_doc.id, make_up_credits, entry_n)

        rules = db.get_transfer_rules(policy_year=policy_year)
        show_types = ("补修规则", "考核科目", "考核方式", "考核安排", "录取规则")
        if to_major_n in _DOWNGRADE_MAJORS:
            show_types = (*show_types, "降级规则")
        return {
            "state": "done", "from_major": from_major_n or "（未指定）",
            "to_major": to_major_n, "entry_year": entry_n, "policy_year": policy_year,
            "quota": quota, "eligible": eligible,
            "source": source,
            "creditable": creditable, "must_make_up": must, "failed": failed,
            "make_up_credits": make_up_credits,
            "pressure": pressure,
            "downgrade": to_major_n in _DOWNGRADE_MAJORS,
            "rules": [r for r in rules if r["rule_type"] in show_types],
            "disclaimer": "本模拟仅供参考；录取按考核成绩择优，学分认定以学院「应修课程表」与"
                          "公示为准。",
        }
    finally:
        db.close()


def _credit_limit(db: TrainingPlanDB, doc_id: int) -> float:
    from .route import DEFAULT_CREDIT_LIMIT
    from .models import MODE_COMMON
    for r in db.get_mode_rules(doc_id, mode=MODE_COMMON):
        if r["rule_type"] == "学期学分上限":
            try:
                return float(r["value"])
            except (TypeError, ValueError):
                pass
    return DEFAULT_CREDIT_LIMIT


# ─────────────────────────── 专业选择（分流）模拟 ───────────────────────────

def _resolve_selection_year(db: TrainingPlanDB, entry_n: str) -> tuple[str, Optional[dict]]:
    """解析专业选择年级：显式年级，否则最新收录。返回 (year, 错误返回 or None)。"""
    year = entry_n or db.latest_major_selection_entry_year()
    if not year:
        return "", {"state": "unavailable", "message": "尚无专业选择方案（请管理员导入）"}
    return year, None


def _selection_strategy(plans: list[dict], choices: Optional[list[str]]) -> list[dict]:
    """志愿策略参考：按接收计划从大到小标注 保/稳/冲（取前 3，超出标"备选"）。

    choices 归一后过滤到已收录专业；为空时用全部已收录专业兜底。"""
    chosen = [service.normalize_major(c) or c for c in (choices or [])] or \
             [p["major"] for p in plans]
    plan_map = {p["major"]: p["quota"] for p in plans}
    ranked = sorted([c for c in chosen if c in plan_map],
                    key=lambda m: plan_map[m], reverse=True)
    tiers = ["保", "稳", "冲"]
    strategy = []
    for i, major in enumerate(ranked[:3]):
        strategy.append({"major": major, "quota": plan_map[major],
                         "tier": tiers[i] if i < len(tiers) else "备选"})
    return strategy


def simulate_major_selection(entry_year: str = "", student_id: str = "",
                             choices: Optional[list[str]] = None) -> dict[str, Any]:
    entry_n = service.normalize_entry_year(entry_year) if entry_year else ""
    db = TrainingPlanDB(service.DB_PATH)
    try:
        year, err = _resolve_selection_year(db, entry_n)
        if err:
            return err
        plans = db.get_major_selection_plans(year)
        rules = db.get_major_selection_rules(year)
        if not plans:
            years = sorted({p["entry_year"] for p in db.get_major_selection_plans() if p.get("entry_year")},
                           reverse=True)
            hint = f"；已收录年级：{'、'.join(years)}" if years else ""
            return {"state": "unavailable", "entry_year": year, "years": years,
                    "message": f"{year} 专业选择方案尚未收录{hint}"}

        formula = next((r["value"] for r in rules if r["item"] == "综合成绩"), "")
        academic = None
        if student_id:
            grades = _student_grades(student_id)
            academic = _academic_score(grades)

        # 志愿策略参考：按接收计划从大到小 → 保/稳/冲
        strategy = _selection_strategy(plans, choices)

        return {
            "state": "done", "entry_year": year, "plans": plans,
            "score_formula": formula or "70%学业成绩 + 20%专项考查 + 10%综合素质测评",
            "academic_score": academic,
            "strategy": strategy,
            "rules": [r for r in rules if r["rule_type"] in
                      ("志愿规则", "录取规则", "学业成绩口径", "时间安排", "学生范围")],
            "routes": [{"major": s["major"],
                        "route_hint": f"route --major {s['major']} --entry-year {year}"}
                       for s in strategy],
            "disclaimer": "志愿策略为参考（未含实时填报热度）；录取按「专业志愿优先 + 综合成绩」，"
                          "以学院公示为准。",
        }
    finally:
        db.close()


def _academic_score(grades: list[dict]) -> dict[str, Any]:
    """学业成绩估算（官方口径：补考/重修及格按 60 计；应修未修按 0）——仅对已修课程估算。"""
    total_w = 0.0
    weighted = 0.0
    for g in grades:
        cr = g.get("credit") or 0
        raw = str(g.get("grade_raw") or "").strip()
        m = re.search(r"(\d+(?:\.\d+)?)", raw)
        if not m or cr <= 0:
            continue
        score = float(m.group(1))
        if g.get("pass_flag") and ("重修" in raw or "补考" in raw):
            score = 60.0
        weighted += score * cr
        total_w += cr
    if total_w <= 0:
        return {"estimated": None, "note": "无可用成绩，无法估算"}
    return {"estimated": round(weighted / total_w, 1),
            "credits": round(total_w, 1),
            "note": "按已修课程学分加权估算（补考/重修及格按 60 计）；不含未修课程与面试/综测"}


def simulate_minor(major: str, minor: str, entry_year: str = "",
                   student_id: str = "") -> dict[str, Any]:
    """辅修模拟（本期预留：待教务提供辅修教学计划）。"""
    return {"state": "unavailable",
            "message": "辅修教学计划尚未收录，暂无法模拟；待教务提供后开放。"}
