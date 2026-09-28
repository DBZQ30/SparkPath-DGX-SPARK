"""选课合理性检查（2026-08-31 方向重构——系统核心功能）。

根据学生当前选课结果判断选课是否符合培养计划：识别"回避专业选修课、
只攻必修"等选课不合理的钻漏洞行为，选课后及时提醒。

口径（用户确认 + 2026-08-31 扩展为全课程类别 + 2026-09-07 及格制 D1/D2/D3）：
- 应累计 = 学生专业方案中各类别课程（semester ≤ 当前学期）学分合计，
  剔除"全员无成绩"课程（成绩未出/未开课，复用 Prep.missing_courses）
- 已修 = 及格制：同课(clean 名)多行取最高分代表行
  （parsers.representative_by_course，缺考/空=0 档），代表行及格
  (pass_flag=1)才计学分——挂科/缺考不计、补考通过只计一次；
  类别按方案名匹配（_match_plan_course）命中归方案类别，未匹配但
  ◆选修课/◇核心课 标记归并模块课程（JSON 通识教育类）
- 已选 = 当前选课结果中各类别学分（跨选/辅修排除）
- 触发：任一类别 应累计 − 已修 − 已选 > 0 即触发（差额 0.5/1.0 也触发）
- 缺修必修课（2026-09-07 起只看及格）：应修必修课（≤ 当前学期，剔除全员
  无成绩）中"最高分代表行不及格或全无成绩"且当前未选的逐门列出，带
  reason（unattempted=从未修过 / failed=有成绩但最高分不及格）

复用 Prep（rules.py）全部索引/匹配/豁免语义。
"""
import json

from .models import SelectionCheckRow
from .parsers import representative_by_course
from .rules import _CAT_RENAME

# v7：类型A 课程豁免虚拟成绩行的 grade_raw 标记（service 注入方与下方 R1
# 覆盖计数排除共用同一常量——单源防字面量漂移，注入方与守卫不一致会静默失效）
WAIVER_MARK = "特殊豁免"


# 已修归类辅助（2026-08-31 以学分结构 JSON 为基准）：
# - Table 1 匹配课 → 方案类别（"数学和基础科学类课程"归并为"学科门类基础课程"）
# - 未匹配的 ◆选修课/◇核心课 → 模块课程（通识核心/通识选修——JSON 通识教育类）
# - 未匹配的英语类 → 公共课程（JSON 公共课程含大学英语 6）
# - 军训 → 集中实践（JSON 集中实践含军训 2）
_IMPLICIT_CATEGORY = {
    "◆选修课": "模块课程", "◇核心课": "模块课程",
}
_PUBLIC_KEYWORDS = ("英语", "雅思", "托福", "视听说")
# 思政类必修课（留学生豁免缺修检查，2026-08-31 用户确认）
_POLITICAL_KEYWORDS = ("思想道德", "中国近现代史", "毛泽东", "马克思", "习近平",
                       "形势与政策", "思想政治", "国防教育", "军训")


def _grade_category(prep, sid: str, g) -> str | None:
    """成绩课 → JSON 类别（None = 方案外不计）。"""
    pc = prep._match_plan_course(sid, g)
    if pc is not None:
        return _CAT_RENAME.get(pc.course_type, pc.course_type)
    if g.marker in _IMPLICIT_CATEGORY:
        return _IMPLICIT_CATEGORY[g.marker]
    if any(k in g.course_name for k in _PUBLIC_KEYWORDS):
        return "公共课程"
    if "军训" in g.course_name:
        return "集中实践"
    return None


def _gained_by_category(prep, sid: str) -> dict[str, float]:
    """已修 per 类别（2026-09-07 及格制）：同课(clean 名)多行取最高分代表行
    （parsers.representative_by_course），仅计代表行及格(pass_flag=1)的学分
    ——挂科/缺考不计、补考通过只计一次；按 JSON 类别归类。"""
    out: dict[str, float] = {}
    for g in representative_by_course(prep._student_grades(sid)).values():
        if g.pass_flag != 1:   # D2：代表行不及格 → 不计（含挂科/缺考）
            continue
        cat = _grade_category(prep, sid, g)
        if cat:
            out[cat] = out.get(cat, 0.0) + g.credit
    return out


def _missing_required_courses(prep, sid: str) -> list[tuple[str, str, float, str, str, str]]:
    """缺修必修课（2026-08-31 用户指出的设计缺口；2026-09-07 起只看及格）：
    应修必修课（≤ 当前学期，剔除全员无成绩）中"最高分代表行不及格或全无
    成绩"且当前选课未选的逐门列出，
    返回 [(课程代码, 课程名, 学分, reason, score, category), ...]（v7：含 code——前端
    豁免提交以 code 为键；2026-09-07 C1：reason = unattempted(从未修过)/failed(有
    成绩但最高分不及格)——挂科必修课同样报缺修，口径一致：挂科=课程未完成；
    score = failed 时代表行成绩原文（旁注展示具体分数），unattempted 为空串。

    2026-09-22：新增 category（按方案 course_type 经 _CAT_RENAME 归并到八大类）——
    调用方按类别归拢展示（专业核心课程的挂科列在"专业核心课程"下、高数列在
    "学科门类基础课程"下），不再堆在不分类别的"缺修必修课"筐里。

    类别汇总差额看不到"缺一门必修"——必修课必须修，单独逐门检查。
    已修判定（D2 及格制）：成绩单同课最高分代表行**及格**者，经
    _match_plan_course（含 ACCA 新旧代码映射/子串/标记归并）命中的方案课程
    代码 + 当前选课课程代码——代码级比对最可靠（2026-08-31 修复：clean 名
    比对会漏 ACCA 映射如 F7→FR）。
    军训等必修课成绩单无记录即视为缺修（用户确认：军训确有未修者，判定成立）。
    留学生豁免（2026-08-31 用户确认：不学军训/思政课）——学号 999 段。"""
    reps = representative_by_course(prep._student_grades(sid))
    matched = {m.course_code for g in reps.values() if g.pass_flag == 1
               if (m := prep._match_plan_course(sid, g)) is not None}
    matched.update(s.course_code for s in prep._student_selections(sid))
    is_foreign = "999" in sid
    out: list[tuple[str, str, float, str, str, str]] = []
    for c in prep.due_required(sid):
        if is_foreign and any(k in c.course_name for k in _POLITICAL_KEYWORDS):
            continue   # 留学生豁免：军训/国防教育/军事理论 + 思政类必修
        if c.course_code in matched:
            continue
        rep = next((g for g in reps.values()
                    if (m := prep._match_plan_course(sid, g)) is not None
                    and m.course_code == c.course_code), None)
        reason = "failed" if rep is not None else "unattempted"
        score = rep.grade_raw or "" if rep is not None else ""
        cat = _CAT_RENAME.get(c.course_type, c.course_type)
        out.append((c.course_code, c.course_name, c.credit, reason, score, cat))
    return out


def _category_gaps(expected: dict, gained: dict, selected: dict) -> dict[str, float]:
    """类别差额表：应累计 − 已修（含 N3 认可）− 已选，round 后 >0 即计入
    （D1，2026-09-07：差额 >0 即触发，不再有 2 学分阈值）。"""
    gaps: dict[str, float] = {}
    for cat, req in expected.items():
        gap = round(req - gained.get(cat, 0.0) - selected.get(cat, 0.0), 1)
        if gap > 0.0:
            gaps[cat] = gap
    return gaps


def _split_missing_by_category(
        missing_req, gaps: dict[str, float]) -> tuple[dict[str, list], list]:
    """逐门缺修课按类别归拢：有差额的类别 → 该类 missing；差额=0 的类别 →
    other_missing（类别学分已达标但课挂科/未修，方案2 补充展示，防漏修）。"""
    miss_by_cat: dict[str, list[dict]] = {}
    other_missing: list[dict] = []
    for code, cname, cr, reason, score, mcat in missing_req:
        item = {"code": code, "name": cname, "credit": cr,
                "reason": reason, "score": score}
        if mcat in gaps:
            miss_by_cat.setdefault(mcat, []).append(item)
        else:
            item["cat"] = mcat
            other_missing.append(item)
    return miss_by_cat, other_missing


def _gap_details(gaps, expected, gained, selected, miss_by_cat) -> tuple[list, list]:
    """v7：message 文本片段与 details JSON 的 cats 逐条在同一循环构建——
    复用同一份 expected/gained/selected/gaps，杜绝二次计算数字漂移（L7）。"""
    parts, cat_items = [], []
    for cat, gap in sorted(gaps.items()):
        parts.append(
            f"{cat}差 {gap:.1f}（应{expected[cat]:.0f}/已修{gained.get(cat, 0.0):.1f}"
            f"/已选{selected.get(cat, 0.0):.1f}）")
        cat_missing = miss_by_cat.get(cat, [])
        # gap_ex_missing：剔除已计入 missing 的课后，类别还差的净额
        # （差额可小于缺课学分——该类另有已修/已选抵扣；防前端相加算错）
        gap_ex = round(gap - sum(m["credit"] for m in cat_missing), 1)
        cat_items.append({"cat": cat, "expected": expected[cat],
                          "gained": round(gained.get(cat, 0.0), 1),
                          "selected": round(selected.get(cat, 0.0), 1),
                          "gap": gap,
                          "gap_ex_missing": gap_ex,
                          "missing": cat_missing})
    return parts, cat_items


def _missing_summary(missing_req) -> tuple[str, float]:
    """C4：message 只列课名（reason 旁注由前端按 details 渲染；2026-09-22 起
    按类别归拢，不用"缺修必修课"统称）+ 缺修课学分合计（无差额时四要素兜底）。"""
    named = "、".join(f"《{n}》" for _, n, _, _, _, _ in missing_req)
    credit = round(sum(cr for _, _, cr, _, _, _ in missing_req), 1)
    return named, credit


def check_selection_rationality(prep, checked_at: str = "") -> list[SelectionCheckRow]:
    """执行选课合理性检查（纯内存计算，2026-08-31 全课程类别 + 必修课逐门）。

    触发：任一类别差额 > 0 学分（2026-09-07 D1，不再有 2 学分阈值），或存在
    缺修必修课（≥1 门）。
    返回触发学生结果行；每学生一行，expected/gained/selected/gap 取**差额最大的
    类别**，message 列出全部不足类别。
    非在籍学生不参与；某专业无任何成绩记录 → 跳过（防假阳性）。

    2026-09-22（用户口径）：逐门缺修必修课**按方案类别归拢**——details.cats 每类
    新增 missing（该类别下缺修/挂科的课）；所在类别差额=0 但仍缺修的课进
    other_missing（如公共课程学分已达标、但体育-4 挂科未过，仍需重修）；
    gap_ex_missing = 类别差额剔除"已计入该类 missing 的课"后的净额（供前端说明
    "差 5.5 中已含《高等数学I-2》6.5"——差额可小于缺课学分，因该类另有已修/已选）。
    不再产出不分专业的"缺修必修课"垃圾筐 category。"""
    from collections import Counter
    # R1：类型A 虚拟行不计专业覆盖（防 0 真实行窗口假阳性）
    covered_by_major = Counter(
        prep._major_for(g.student_id) for g in prep.grades
        if g.grade_raw != WAIVER_MARK)
    rows: list[SelectionCheckRow] = []
    for sid in prep.students:
        if prep.enrolled_status(sid) != "在籍":
            continue
        if covered_by_major.get(prep._major_for(sid), 0) == 0:
            continue   # 该专业成绩单缺失 → 不检查
        expected = prep.required_credits_by_category(sid)
        gained = _gained_by_category(prep, sid)
        # N3：类型B 旧课学分认可在计算源头叠加——details/row/message 数字均含认可分
        for cat, wc in (prep.waiver_credit.get(sid) or {}).items():
            gained[cat] = gained.get(cat, 0.0) + wc
        selected = prep.selected_credits_by_category(sid)
        gaps = _category_gaps(expected, gained, selected)
        missing_req = _missing_required_courses(prep, sid)
        if not gaps and not missing_req:
            continue
        miss_by_cat, other_missing = _split_missing_by_category(missing_req, gaps)
        parts, cat_items = _gap_details(gaps, expected, gained, selected, miss_by_cat)
        missing_credit = 0.0
        if missing_req:
            named, missing_credit = _missing_summary(missing_req)
            parts.append(f"缺修 {named}")
        worst = (max(gaps, key=gaps.get) if gaps
                 else (missing_req[0][5] if missing_req else ""))
        st = prep.students[sid]
        # 2026-08-31 展示修复：缺修课触发（无类别差额）时四要素填缺修课学分合计
        # 2026-09-22：逐门缺修课均归入 categories/other_missing，四要素兜底取
        # 最差类别（其 missing 已在 details）；无差额时取缺修课合计
        rows.append(SelectionCheckRow(
            source_file_id=0,   # 由调用方（service 层）回填
            student_id=sid,
            name=st.name or "",
            major=prep._major_for(sid),
            class_name=st.class_name or "",
            category=worst or "缺修必修课",
            expected_credit=expected.get(worst, 0.0) or missing_credit,
            gained_credit=gained.get(worst, 0.0),
            selected_credit=selected.get(worst, 0.0),
            gap=gaps.get(worst, 0.0) or missing_credit,
            message=f"选课不符合培养计划：{'；'.join(parts)}，请及时补选相应课程",
            details=json.dumps({"cats": cat_items, "other_missing": other_missing},
                               ensure_ascii=False),
            checked_at=checked_at,
        ))
    return rows


def build_summary(prep, rows: list[SelectionCheckRow]) -> str:
    """检查摘要文本（含分专业分布）。"""
    from collections import Counter
    total = len([s for s in prep.students
                 if prep.enrolled_status(s) == "在籍"])
    by_major = Counter(r.major for r in rows)
    parts = "、".join(f"{m} {by_major.get(m, 0)} 人" for m in by_major)
    return (f"选课检查完成：检查 {total} 人，选课不合理 {len(rows)} 人"
            f"（{parts}）；任一类别差额>0 学分即触发提醒")


def exempt_courses(prep) -> dict[str, list[str]]:
    """豁免课程清单（v6 前端显著通知管理员核对）：{专业: [课程名, ...]}。

    数据源 = Prep._missing_courses（全员无成绩课程 course_code 集合），
    换算回课程名并按名排序——前端展示比 code 可读。
    2026-09-01 用户反馈收敛：只列**影响应修口径**的豁免（Table 1 进度口径
    类别——公共/学科门类/专业大类/专业核心/集中实践）；专业选修课程与模块
    课程的应修 = JSON 毕业要求全量，豁免不参与任何计算（纯清单噪音）。"""
    _JSON_FULL_CATS = ("专业选修课程", "模块课程")
    out: dict[str, list[str]] = {}
    for major, codes in prep._missing_courses.items():
        by_code = {c.course_code: c for c in prep._plan_by_major.get(major, [])}
        names = sorted(
            by_code[c].course_name for c in codes
            if c in by_code and by_code[c].course_type not in _JSON_FULL_CATS)
        if names:
            out[major] = names
    return out
