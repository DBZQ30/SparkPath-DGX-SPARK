"""多路径个性化学业规划 —— 路线图引擎（设计 005 §5/§7）。

- `build_route(major, entry_year, mode)`：四方向四年路线图（逐学期课程 + 学分 + 负荷 + 节奏节点）
- `compare_modes(major, entry_year)`：四方向横向对比
- `stage_actions(...)`：节奏节点（官方硬节点 + 通用建议模板，建议项显式标注来源）

数据来源：`training_plan.db` 的 `plan_semester_course` / `plan_course` /
`plan_prereq_edge` / `plan_mode_*` / `plan_graduation_req`；不臆造，缺失即降级。
"""
from __future__ import annotations

import re
from typing import Any

from . import service
from .db import ALL_GRADES, TrainingPlanDB
from .models import (
    MODE_COMMON,
    MODE_CROSS,
    MODE_INNOVATION,
    MODE_REGULAR,
    MODE_SCIENCE,
    MODES,
    STANDARD_MAJORS,
    STATUS_DONE,
    STATUS_PARSING,
    STATUS_QUEUED,
)

DEFAULT_CREDIT_LIMIT = 25.0

_SEM_RE = re.compile(r"^(\d)-(\d)$")


def _sem_key(sem: str) -> tuple[int, int]:
    m = _SEM_RE.match((sem or "").strip())
    if not m:
        return (9, 9)
    return (int(m.group(1)), int(m.group(2)))


def _load_level(ratio: float) -> str:
    if ratio < 0.60:
        return "轻松"
    if ratio < 0.80:
        return "适中"
    if ratio <= 1.00:
        return "偏紧"
    return "超限"


# ─────────────────────── 节奏节点（官方硬节点 + 通用建议） ───────────────────────

# 通用建议（非官方硬性规定）——按学年给出
_GENERAL_ACTIONS: dict[int, list[tuple[str, str]]] = {
    1: [("竞赛", "数学建模校赛 / 学科入门赛"), ("证书", "英语四级（1-2~2-1）"),
        ("科研", "参加实验室开放日、导师宣讲")],
    2: [("竞赛", "大创申报（春秋两季）/ 专业基础竞赛"), ("证书", "英语六级 / 计算机等级"),
        ("科研", "联系导师、进课题组")],
    3: [("竞赛", "高级别竞赛（榜单内）/ 成果转化"), ("科研", "科研训练与实践、论文/专利起步"),
        ("证书", "专业类证书（按专业）")],
    4: [("科研", "毕业设计与科研衔接"), ("其他", "学分自查 / 考研·保研·就业")],
}

# 各方向在通用节奏之外的补充建议
_MODE_ACTIONS: dict[str, list[tuple[int, str, str]]] = {
    MODE_SCIENCE: [(2, "科研", "联系导师、进课题组（科学研究型建议此时段）"),
                   (3, "学业", "在专业选修学分内修读研究生进阶课程（替代）")],
    MODE_CROSS: [(2, "学业", "确定跨选专业（只交叉融合一个专业）"),
                 (3, "学业", "修读跨选专业课程（替代专业选修课）")],
    MODE_INNOVATION: [(2, "竞赛", "大创/竞赛申报（创新创业型成果来源）"),
                      (3, "其他", "创新创业成果沉淀与结题认定")],
}


def _year_of(sem: str) -> int:
    return _sem_key(sem)[0]


def stage_actions(mode: str, official_nodes: list[dict[str, Any]] | None = None
                  ) -> list[dict[str, Any]]:
    """节奏节点：通用建议（标注"建议"）+ 模式补充 + 官方硬节点（标注"官方"）。"""
    out: list[dict[str, Any]] = []
    for year, items in _GENERAL_ACTIONS.items():
        for stage_type, text in items:
            out.append({"year": year, "stage_type": stage_type, "text": text, "source": "建议"})
    for year, stage_type, text in _MODE_ACTIONS.get(mode, []):
        out.append({"year": year, "stage_type": stage_type, "text": text, "source": "建议"})
    # 去重：模式补充建议覆盖同（学年, 类型, 主文案）的通用建议
    dedup: dict[tuple, dict] = {}
    for a in out:
        dedup[(a["year"], a["stage_type"], a["text"].split("（")[0])] = a
    out = list(dedup.values())
    for node in (official_nodes or []):
        out.append(node)
    return out


def _official_nodes(db: TrainingPlanDB, doc_id: int, mode: str) -> list[dict[str, Any]]:
    """官方硬节点（来自培养模式规则 / 毕业条件）。"""
    out: list[dict[str, Any]] = []
    for r in db.get_mode_rules(doc_id, mode=MODE_COMMON):
        if r["rule_type"] == "申请节点":
            out.append({"year": 3, "stage_type": "官方节点",
                        "text": f"{r['item']}：{r['value']}（{r['detail']}）" if r["detail"]
                                else f"{r['item']}：{r['value']}",
                        "source": "官方"})
    return out


# ─────────────────────────── 路线图 ───────────────────────────

def _resolve_doc(db: TrainingPlanDB, major: str, entry_year: str):
    major_n = service.normalize_major(major)
    if not major_n:
        return None, {"state": "bad_major", "message": f"无法识别专业「{major}」",
                      "majors": STANDARD_MAJORS}
    entry_n = service.normalize_entry_year(entry_year) if entry_year else ""
    grades, has_all = db.available_grades(major_n)
    year_list = grades + ([ALL_GRADES] if has_all else [])
    if not entry_n:
        return None, {"state": "need_year", "major": major_n, "years": year_list,
                      "message": f"请提供年级（如 2023级）；{major_n} 已收录年级："
                                 + ("、".join(year_list) if year_list else "暂无")}
    doc = service.find_document(db, major_n, entry_n)
    if not doc:
        return None, {"state": "unavailable", "major": major_n, "entry_year": entry_n,
                      "years": year_list, "message": f"{major_n} {entry_n} 的培养方案尚未收录"}
    if doc.parsed_status in (STATUS_QUEUED, STATUS_PARSING):
        return None, {"state": "parsing", "major": major_n, "entry_year": entry_n,
                      "message": f"{major_n} {entry_n} 的培养方案正在解析中，请稍后再问"}
    if doc.parsed_status != STATUS_DONE:
        return None, {"state": "unavailable", "major": major_n, "entry_year": entry_n,
                      "message": "该方案解析失败，请联系管理员重传"}
    return (major_n, entry_n, doc), None


def _credit_limit(db: TrainingPlanDB, doc_id: int) -> tuple[float, str]:
    for r in db.get_mode_rules(doc_id, mode=MODE_COMMON):
        if r["rule_type"] == "学期学分上限":
            try:
                return float(r["value"]), r["detail"]
            except (TypeError, ValueError):
                pass
    return DEFAULT_CREDIT_LIMIT, ""


def _semester_buckets(sem_courses: list[dict]) -> dict[str, list[dict]]:
    """按规范 "X-Y" 学期分桶（非规范学期标签跳过）。"""
    buckets: dict[str, list[dict]] = {}
    for sc in sem_courses:
        sem = (sc["semester"] or "").strip()
        if not _SEM_RE.match(sem):
            continue
        buckets.setdefault(sem, []).append(sc)
    return buckets


def _render_semesters(buckets: dict[str, list[dict]], courses: dict[str, dict],
                      limit: float) -> list[dict[str, Any]]:
    """逐学期行组装：课程项 + 学分小计 + 负荷比与等级。"""
    semesters: list[dict[str, Any]] = []
    for sem in sorted(buckets, key=_sem_key):
        items = []
        total = 0.0
        for sc in buckets[sem]:
            code = sc["course_code"]
            c = courses.get(code, {})
            note = c.get("note", "") or ""
            items.append({"course_code": code,
                          "course_name": service.parsers.display_course_name(sc["course_name"]),
                          "credit": round(sc["credit"] or 0, 1),
                          "course_type": c.get("course_type", ""),
                          "required_flag": c.get("required_flag", ""),
                          "note": note})
            total += sc["credit"] or 0
        ratio = round(total / limit, 3) if limit else 0
        semesters.append({"semester": sem, "courses": items,
                          "credits": round(total, 1), "load_ratio": ratio,
                          "load_level": _load_level(ratio)})
    return semesters


def _mode_overlay(db: TrainingPlanDB, doc_id: int, mode: str,
                  courses: dict[str, dict]) -> dict[str, Any]:
    """模式 overlay：规则（剔除纯展示项）/ 课程（按学期排序）/ 跨选范围。

    「说明」为纯展示项、「跨选范围」已由 scopes 单独下发（长文本在 .ui-note
    中换行展示）；与 compare_modes/render_route_text 口径一致，避免在规则行里
    用不可换行的 .rule-value 再渲染一次导致溢出。"""
    mode_rules = [r for r in db.get_mode_rules(doc_id, mode=mode)
                  if r["rule_type"] not in ("说明", "跨选范围")]
    mode_courses = db.get_mode_courses(doc_id, mode=mode)
    mode_scopes = db.get_mode_scopes(doc_id, mode=mode)
    overlay: dict[str, Any] = {"mode": mode, "rules": mode_rules, "courses": [],
                               "scopes": [s["allowed_major"] for s in mode_scopes]}
    for mc in mode_courses:
        c = courses.get(mc["course_code"], {})
        overlay["courses"].append({
            "course_code": mc["course_code"], "course_name": mc["course_name"],
            "credit": round(mc["credit"] or 0, 1), "semester": c.get("semester", "")})
    overlay["courses"].sort(key=lambda x: _sem_key(x.get("semester", "")))
    return overlay


def build_route(major: str, entry_year: str = "", mode: str = MODE_REGULAR,
                student_id: str = "") -> dict[str, Any]:
    """生成四年路线图。`student_id` 预留（个性化压力，暂未启用）。"""
    db = TrainingPlanDB(service.DB_PATH)
    try:
        resolved, err = _resolve_doc(db, major, entry_year)
        if err:
            return err
        major_n, entry_n, doc = resolved
        mode = mode or MODE_REGULAR
        if mode not in MODES:
            return {"state": "bad_mode", "message": f"未知方向「{mode}」", "modes": MODES}

        limit, limit_note = _credit_limit(db, doc.id)
        sem_courses = db.get_semester_courses(doc.id)
        courses = {c["course_code"]: c for c in db.get_courses(doc.id)}

        # 逐学期（仅规范 "X-Y" 学期）
        semesters = _render_semesters(_semester_buckets(sem_courses), courses, limit)

        # 模式 overlay
        overlay = _mode_overlay(db, doc.id, mode, courses)

        # 先修（仅已校对）
        edges = db.get_prereq_edges(doc.id, verified_only=True)
        prereq = [{"from": e["from_course_name"], "to": e["to_course_name"]} for e in edges]

        actions = stage_actions(mode, _official_nodes(db, doc.id, mode))
        grad_reqs = db.get_graduation_reqs(doc.id)

        data = {
            "state": "done", "major": major_n, "entry_year": entry_n, "mode": mode,
            "credit_limit": limit, "credit_limit_note": limit_note,
            "semesters": semesters, "mode_overlay": overlay, "prereq": prereq,
            "actions": actions, "graduation_requirements": grad_reqs,
            "apply_node": next((r["value"] for r in db.get_mode_rules(doc.id, mode=MODE_COMMON)
                                if r["rule_type"] == "申请节点"), ""),
            "disclaimer": "本路线图为建议性规划（含通用建议，非官方硬性规定）；"
                          "培养模式申请与学分认定以学院审批为准。",
        }
        data["summary_text"] = render_route_text(data)
        return data
    finally:
        db.close()


def _avg_load(semesters: list[dict[str, Any]]) -> float:
    vals = [s["credits"] for s in semesters]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def compare_modes(major: str, entry_year: str = "") -> dict[str, Any]:
    """四方向横向对比（替代规则 / 平均负荷 / 适合人群）。"""
    db = TrainingPlanDB(service.DB_PATH)
    try:
        resolved, err = _resolve_doc(db, major, entry_year)
        if err:
            return err
        major_n, entry_n, doc = resolved
        limit, _ = _credit_limit(db, doc.id)
        sem_courses = db.get_semester_courses(doc.id)
        total_by_sem: dict[str, float] = {}
        for sc in sem_courses:
            sem = (sc["semester"] or "").strip()
            if _SEM_RE.match(sem):
                total_by_sem[sem] = total_by_sem.get(sem, 0) + (sc["credit"] or 0)
        base_avg = round(sum(total_by_sem.values()) / len(total_by_sem), 1) if total_by_sem else 0

        rows = []
        for mode in MODES:
            rules = db.get_mode_rules(doc.id, mode=mode)
            summary = "；".join(
                (f"{r['item']} {r['value']}{r['unit']}".strip() +
                 (f"（{r['detail']}）" if r["detail"] else ""))
                for r in rules if r["rule_type"] not in ("说明", "跨选范围"))
            scopes = [s["allowed_major"] for s in db.get_mode_scopes(doc.id, mode=mode)]
            rows.append({"mode": mode, "summary": summary, "scopes": scopes,
                         "avg_credits": base_avg, "credit_limit": limit,
                         "avg_load_ratio": round(base_avg / limit, 3) if limit else 0})
        return {"state": "done", "major": major_n, "entry_year": entry_n, "modes": rows,
                "credit_limit": limit,
                "note": "平均负荷按培养方案推荐课表估算；各方向替代课程计入后略有差异。"}
    finally:
        db.close()


# ─────────────────────────── 文本渲染（聊天/CLI） ───────────────────────────

def render_route_text(data: dict[str, Any]) -> str:
    lines = [f"【{data['major']} · {data['entry_year']} · {data['mode']} 四年路线图】（可参考方案）"]

    ov = data["mode_overlay"]
    rule_bits = []
    for r in ov["rules"]:
        if r["rule_type"] in ("说明", "跨选范围"):
            continue
        bit = f"{r['item']} {r['value']}{r['unit']}".strip()
        if r["detail"]:
            bit += f"（{r['detail']}）"
        rule_bits.append(bit)
    if rule_bits:
        lines.append("一、路径规则：" + "；".join(rule_bits))
    if ov["scopes"]:
        lines.append("    跨选专业范围：" + "、".join(ov["scopes"]))
    if ov["courses"]:
        lines.append("    模式课程清单：" + "、".join(
            f"{c['course_name']}({c['credit']:g}" + (f"·{c['semester']}" if c["semester"] else "")
            + ")" for c in ov["courses"]))

    lines.append("二、逐年节奏（按培养方案推荐课表）：")
    by_year: dict[int, list[dict]] = {}
    for s in data["semesters"]:
        by_year.setdefault(_year_of(s["semester"]), []).append(s)
    for year in sorted(by_year):
        for s in by_year[year]:
            names = "、".join(c["course_name"] for c in s["courses"][:6])
            more = f" 等{len(s['courses'])}门" if len(s["courses"]) > 6 else ""
            lines.append(f"    {s['semester']}：{names}{more}（{s['credits']:g} 学分 · "
                         f"负荷 {s['load_level']}）")

    lines.append("三、节奏节点（官方硬节点 + 通用建议）：")
    for a in data["actions"]:
        tag = "★官方" if a.get("source") == "官方" else "建议"
        lines.append(f"    大{a['year']}｜{a['stage_type']}：{a['text']}（{tag}）")

    lines.append(f"四、压力提示：每学期上限 {data['credit_limit']:g} 学分"
                 + (f"（{data['credit_limit_note']}）" if data["credit_limit_note"] else "")
                 + f"；平均 {_avg_load(data['semesters']):g} 学分。")
    lines.append("※ " + data["disclaimer"])
    return "\n".join(lines)
