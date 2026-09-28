"""选课合理性检查测试（2026-08-31 方向重构）。

口径：应累计（方案专业选修 ≤ 当前学期，剔除全员无成绩）− 已修（及格制：
同课最高分代表行及格才计，挂科/缺考不计）− 已选（本轮选课专业选修）
差额 > 0 学分 → 提醒（2026-09-07 D1/D2/D3）。
v7：details 结构化 JSON（missing 含 code+reason / cats 逐条同循环）+
waiver_credit 类型B 类别加分（N3 源头叠加）+ covered 守卫排除虚拟行（R1）。
"""
import json


from academicwarning.models import Student, Selection, Grade, PlanCourse
from academicwarning.parsers import clean_course_name
from academicwarning.rules import Prep
from academicwarning.selection_check import (_gained_by_category,
                                             _missing_required_courses,
                                             check_selection_rationality, build_summary)


def _prep(students=None, sels=None, grades=None, courses=None, semester="4-1",
          s2_grades=True, waiver_credit=None):
    """默认双学生：S2 修过 EL1+EL2（≤4-1 的选修）→ 应累计不被"全员无成绩"豁免；
    各用例断言 S1 的行为（S2 无触发）。s2_grades=False 时自行控制成绩构造。"""
    students = students or [
        Student(student_id="S1", name="甲", grade="2023级", major="0824工商管理"),
        Student(student_id="S2", name="乙", grade="2023级", major="0824工商管理"),
    ]
    courses = courses or [
        PlanCourse(plan_id=1, course_code="EL1", course_name="选修课A",
                   credit=2.0, course_type="专业选修课程", required_flag="选修", semester="3-1"),
        PlanCourse(plan_id=1, course_code="EL2", course_name="选修课B",
                   credit=2.0, course_type="专业选修课程", required_flag="选修", semester="3-2"),
        PlanCourse(plan_id=1, course_code="EL3", course_name="选修课C",
                   credit=2.0, course_type="专业选修课程", required_flag="选修", semester="4-2"),
    ]
    grades = list(grades or [])
    if s2_grades:
        grades += [_grade("S2", "选修课A", 2.0), _grade("S2", "选修课B", 2.0)]
    return Prep(students, sels or [], grades, {"工商管理": courses}, {},
                [], semester, entry_year=2023, waiver_credit=waiver_credit)


def _grade(sid, name, credit, pass_flag=1, marker=""):
    return Grade(student_id=sid, course_name=name, credit=credit, grade_raw="80",
                 pass_flag=pass_flag, marker=marker,
                 course_name_clean=clean_course_name(name))


def _sel(sid, code, name, category="专业选修课程", credit=2.0):
    return Selection(student_id=sid, semester_label="2026-2027学年 第一学期",
                     semester_code="4-1", course_code=code, course_name=name,
                     credit=credit, nature="选修", category=category,
                     status="选中", retake="初修")


def test_strip_lead_eng():
    """2026-09-04 前导英文剥离工具：只剥"前导英文代码/词+可选空格"，
    且剥离后须含中文才生效（纯英文课名原样保留）。"""
    from academicwarning.rules import _strip_lead_eng
    assert _strip_lead_eng("PM业绩管理（ACCA）") == "业绩管理（ACCA）"
    assert _strip_lead_eng("Python 数据分析-1") == "数据分析-1"
    assert _strip_lead_eng("Python数据分析-1") == "数据分析-1"
    assert _strip_lead_eng("高等数学I-1") == "高等数学I-1"   # 中文开头不动
    assert _strip_lead_eng("Python") == "Python"             # 纯英文不剥离


def test_lead_code_body_exact_pm_over_apm():
    """2026-09-04 金标准：成绩单《业绩管理(ACCA)》应匹配方案必修
    《PM业绩管理（ACCA）》而非同家族更长的选修《APM高级业绩管理（ACCA）》
    ——剥前导英文后中文主体全等优先于"最长 clean 名"子串；其《P5 高级业绩管理》
    归 APM、《F3 财务会计（ACCA）》归 FA（F1-F9 映射语义不回归）。"""
    courses = [
        PlanCourse(plan_id=1, course_code="PM1", course_name="PM业绩管理（ACCA）",
                   credit=4.0, course_type="专业核心课程", required_flag="必修",
                   semester="2-1"),
        PlanCourse(plan_id=1, course_code="APM1", course_name="APM高级业绩管理（ACCA）",
                   credit=3.0, course_type="专业选修课程", required_flag="选修",
                   semester="3-2"),
        PlanCourse(plan_id=1, course_code="FM1", course_name="FM财务管理（ACCA）",
                   credit=4.0, course_type="专业核心课程", required_flag="必修",
                   semester="2-2"),
        PlanCourse(plan_id=1, course_code="FA1", course_name="FA财务会计（ACCA）",
                   credit=3.0, course_type="专业核心课程", required_flag="必修",
                   semester="1-2"),
    ]
    # S2 全修（防全员无成绩豁免）；S1 用金标准学生成绩单形态（无代码《业绩管理(ACCA)》、
    # P5/P4 旧代码、F3 旧代码）
    grades = [_grade("S2", "PM 业绩管理（ACCA）", 4.0),
              _grade("S2", "APM 高级业绩管理（ACCA）", 3.0),
              _grade("S2", "FM 财务管理（ACCA）", 4.0),
              _grade("S2", "FA 财务会计（ACCA）", 3.0),
              _grade("S1", "业绩管理(ACCA)", 4.0),
              _grade("S1", "P5 高级业绩管理", 3.0),
              _grade("S1", "F3 财务会计（ACCA）", 3.0)]
    prep = _prep(s2_grades=False, courses=courses, grades=grades)
    g_by = {clean_course_name(g.course_name): g for g in grades[4:]}
    m = prep._match_plan_course("S1", g_by["业绩管理(ACCA)"])
    assert m is not None and m.course_code == "PM1"    # 主体全等胜最长子串
    m2 = prep._match_plan_course("S1", g_by["P5高级业绩管理"])
    assert m2 is not None and m2.course_code == "APM1"
    m3 = prep._match_plan_course("S1", g_by["F3财务会计(ACCA)"])
    assert m3 is not None and m3.course_code == "FA1"
    # 缺修必修：PM 消除，只剩 FM（金标准学生 3→2 语义的单元形态）
    names = [n for _, n, _, _, _, _ in _missing_required_courses(prep, "S1")]
    assert names == ["FM财务管理（ACCA）"]
    # 已修归类：业绩管理 4 学分入专业核心（PM 必修）、P5 3 学分入专业选修（APM）
    got = _gained_by_category(prep, "S1")
    assert got.get("专业核心课程", 0.0) == 7.0
    assert got.get("专业选修课程", 0.0) == 3.0


def test_f_old_code_bare_body_startswith_decides():
    """2026-09-04 审查 I-1 回归（真实数据形态《F9 财务管理》）：F1-F9 旧代码成绩
    若进剥离候选收集，F 前缀被剥后裸中文主体"财务管理"会双向子串命中更长的同族
    选修《AFM高级财务管理（ACCA）》→ 最长名误归 AFM；须跳过剥离竞争由
    F1-F9 startswith 映射（F9→FM）裁决 → 归必修《FM财务管理（ACCA）》。"""
    courses = [
        PlanCourse(plan_id=1, course_code="FM1", course_name="FM财务管理（ACCA）",
                   credit=4.0, course_type="专业核心课程", required_flag="必修",
                   semester="2-2"),
        PlanCourse(plan_id=1, course_code="AFM1", course_name="AFM高级财务管理（ACCA）",
                   credit=3.0, course_type="专业选修课程", required_flag="选修",
                   semester="3-2"),
    ]
    # S2 全修（防全员无成绩豁免）；S1 用裸主体 F9 行（沈政屹/周兆姿成绩单形态）
    grades = [_grade("S2", "FM 财务管理（ACCA）", 4.0),
              _grade("S2", "AFM 高级财务管理（ACCA）", 3.0),
              _grade("S1", "F9 财务管理", 4.0)]
    prep = _prep(s2_grades=False, courses=courses, grades=grades)
    m = prep._match_plan_course("S1", prep._student_grades("S1")[0])
    assert m is not None and m.course_code == "FM1"   # startswith 裁决，非 AFM
    names = [n for _, n, _, _, _, _ in _missing_required_courses(prep, "S1")]
    assert "FM财务管理（ACCA）" not in names


def test_lead_code_python_chinese_body_anti():
    """2026-09-04 前导英文通用化不误配（真实数据形态）：成绩单《Python 数据处理》
    与方案《Python数据分析-1》中文主体不同（数据处理 vs 数据分析）→ 剥离后
    双向子串不命中；主体一致的全名《Python 数据分析-1》精确命中。"""
    courses = [
        PlanCourse(plan_id=1, course_code="PY1", course_name="Python数据分析-1",
                   credit=2.5, course_type="专业选修课程", required_flag="选修",
                   semester="4-1"),
    ]
    grades = [_grade("S2", "Python 数据分析-1", 2.5),
              _grade("S1", "Python 数据处理", 2.5)]
    prep = _prep(s2_grades=False, courses=courses, grades=grades)
    g_dp = prep._student_grades("S1")[0]
    assert prep._match_plan_course("S1", g_dp) is None   # 不同中文不误配
    assert prep._match_plan_course("S2", prep._student_grades("S2")[0]).course_code == "PY1"


def test_missing_exempt_strip_lead_variant():
    """2026-09-04 全员无成绩豁免的单向匹配加前导英文剥离变体（防漏）：
    成绩单《业绩管理(ACCA)》视为方案《PM业绩管理（ACCA）》有人修 → 不豁免；
    而《P4 高级财务管理》≠ 方案《FM财务管理（ACCA）》→ FM 仍豁免（无剥离误配）。"""
    from academicwarning.models import PlanCourse, Student
    from academicwarning.rules import Prep
    courses = [
        PlanCourse(plan_id=1, course_code="PM1", course_name="PM业绩管理（ACCA）",
                   credit=4.0, course_type="专业核心课程", required_flag="必修",
                   semester="4-1"),
        PlanCourse(plan_id=1, course_code="FM1", course_name="FM财务管理（ACCA）",
                   credit=4.0, course_type="专业核心课程", required_flag="必修",
                   semester="4-1"),
    ]
    students = [Student(student_id="S1", name="甲", grade="2023级", major="工商管理"),
                Student(student_id="S2", name="乙", grade="2023级", major="工商管理")]
    grades = [_grade("S1", "业绩管理(ACCA)", 4.0),
              _grade("S2", "P4 高级财务管理", 4.0)]
    prep = Prep(students, [], grades, {"工商管理": courses}, {}, [], "4-1",
                entry_year=2023, covered_sem="3-2")   # 4-1 成绩未出窗口
    missing = prep.missing_courses("S1")
    assert "PM1" not in missing   # 剥前导英文后单向命中 → 有成绩 → 不豁免
    assert "FM1" in missing       # 高级财务管理 ≠ 财务管理（ACCA）→ 仍豁免


def test_composite_semester_format():
    """2026-08-31 修复：复合学期格式（'1-1至4-1'/'1-1，2-1'）按起始学期计入应修。"""
    from academicwarning.rules import Prep
    assert Prep._sem_le("1-1至4-1", "4-1") is True    # 形势与政策（每学期开）
    assert Prep._sem_le("1-1，2-1", "4-1") is True    # 体育（复合列表）
    assert Prep._sem_le("1-2，2-2", "4-1") is True
    assert Prep._sem_le("4-2", "4-1") is False        # 未来学期不计


def test_acca_rename_match():
    """2026-08-31 修复：ACCA 2018 课程改名——成绩单旧代码 F1-F8 匹配方案新代码 BT/MA/FA/LW/FR/AA。"""
    courses = [
        PlanCourse(plan_id=1, course_code="ACAU421308", course_name="BT 商业与技术（ACCA）",
                   credit=3.0, course_type="专业核心课程", required_flag="必修", semester="3-1"),
        PlanCourse(plan_id=1, course_code="ACAU421108", course_name="MA 管理会计（ACCA）",
                   credit=3.0, course_type="专业核心课程", required_flag="必修", semester="3-1"),
        PlanCourse(plan_id=1, course_code="ACAU421508", course_name="FM 财务管理（ACCA）",
                   credit=4.0, course_type="专业核心课程", required_flag="必修", semester="4-1"),
    ]
    # S2 修过全部（防豁免）；S1 用 F 旧代码成绩（F1→BT、F2→MA；FM 未修）
    prep = _prep(s2_grades=False, courses=courses,
                 grades=[_grade("S2", "BT 商业与技术（ACCA）", 3.0),
                         _grade("S2", "MA 管理会计（ACCA）", 3.0),
                         _grade("S2", "FM 财务管理（ACCA）", 4.0),
                         _grade("S1", "F1 会计师与企业（ACCA）", 3.0),
                         _grade("S1", "F2 管理会计（ACCA）", 3.0)])
    got = _gained_by_category(prep, "S1")
    assert got.get("专业核心课程", 0.0) == 6.0   # F1/F2 计入（旧代码→新课程）
    rows = check_selection_rationality(prep)
    s1 = [r for r in rows if r.student_id == "S1"]
    assert s1 and s1[0].gap == 4.0   # 只剩 FM 财务管理 4 学分未修


def test_foreign_student_exempt():
    """2026-08-31 留学生豁免：学号 999 段不学英语/思政/军训——公共课程应修
    减（思政+英语+军事），缺修必修课豁免思政类/军训。"""
    credit_req = {"工商管理": {"模块课程": 12.0, "专业选修课程": 8.0,
                               "公共课程": 25.0, "公共课程_留学生豁免": 23.0}}
    students = [
        Student(student_id="S9991", name="留学生A", grade="2023级", major="0824工商管理"),
        Student(student_id="S2", name="乙", grade="2023级", major="0824工商管理"),
        Student(student_id="S3", name="丙", grade="2023级", major="0824工商管理"),
    ]
    courses = [
        PlanCourse(plan_id=1, course_code="EL1", course_name="选修课A",
                   credit=2.0, course_type="专业选修课程", required_flag="选修", semester="3-1"),
        PlanCourse(plan_id=1, course_code="PUB1", course_name="思想道德与法治",
                   credit=3.0, course_type="公共课程", required_flag="必修", semester="1-1"),
        PlanCourse(plan_id=1, course_code="JX1", course_name="军训",
                   credit=2.0, course_type="集中实践", required_flag="必修", semester="1-1"),
    ]
    grades = [_grade("S2", "选修课A", 2.0), _grade("S2", "思想道德与法治", 3.0),
              _grade("S2", "军训", 2.0),
              _grade("S9991", "选修课A", 2.0),   # 留学生：无思政/军训记录
              _grade("S3", "选修课A", 2.0)]      # 普通生：无思政/军训记录
    prep = Prep(students, [], grades, {"工商管理": courses}, {}, [], "4-1",
                entry_year=2023, credit_req_by_major=credit_req)
    # 留学生公共课应修 = 25-23 = 2（仅体育），集中实践应修剔除军训
    req = prep.required_credits_by_category("S9991")
    assert req.get("公共课程", 0.0) == 2.0
    assert req.get("集中实践", 0.0) == 0.0
    assert _missing_required_courses(prep, "S9991") == []   # 思政/军训豁免
    # 普通生不受豁免影响：S3 无思政/军训成绩 → 缺修 2 门
    assert len(_missing_required_courses(prep, "S3")) == 2


def test_gap_threshold():
    """差额边界（D1）：>0 即触发——差 1.0 触发（旧 ≥2 阈值下不触发）、差 0
    不触发。"""
    courses = [
        PlanCourse(plan_id=1, course_code="EL1", course_name="选修课A",
                   credit=2.0, course_type="专业选修课程", required_flag="选修",
                   semester="3-1"),
        PlanCourse(plan_id=1, course_code="EL2", course_name="选修课B",
                   credit=1.0, course_type="专业选修课程", required_flag="选修",
                   semester="3-2"),
    ]
    s2 = [_grade("S2", "选修课A", 2.0), _grade("S2", "选修课B", 1.0)]
    # 应累计 3（EL1+EL2），已修 2（EL1）→ 差 1.0 → 触发
    prep = _prep(s2_grades=False, courses=courses,
                 grades=[*s2, _grade("S1", "选修课A", 2.0)])
    rows = check_selection_rationality(prep)
    assert len(rows) == 1 and rows[0].gap == 1.0
    # 已修 3.0（EL1+EL2 都修）→ 差 0 → 不触发
    prep2 = _prep(s2_grades=False, courses=courses,
                  grades=[*s2, _grade("S1", "选修课A", 2.0), _grade("S1", "选修课B", 1.0)])
    assert check_selection_rationality(prep2) == []


def test_expected_only_past_semesters():
    """应累计只计 semester ≤ 当前学期：4-2 课程不计入。"""
    # EL1(3-1)+EL2(3-2)=4 应累计；EL3(4-2) 不计 → 已修 0 → 差 4 触发
    prep = _prep()
    rows = check_selection_rationality(prep)
    assert rows and rows[0].expected_credit == 4.0


def test_missing_course_exempted():
    """全员无成绩课程从应累计剔除（成绩未出/未开课）；有人修过则计入。"""
    courses = [
        PlanCourse(plan_id=1, course_code="EL1", course_name="选修课A",
                   credit=2.0, course_type="专业选修课程", required_flag="选修", semester="3-1"),
        PlanCourse(plan_id=1, course_code="EL2", course_name="选修课B",
                   credit=2.0, course_type="专业选修课程", required_flag="选修", semester="3-2"),
    ]
    # 只有 EL1 被 S2 修过 → 应累计 = EL1 2.0；EL2 全员无成绩豁免
    prep = _prep(s2_grades=False,
                 grades=[_grade("S2", "选修课A", 2.0)], courses=courses)
    assert prep.required_credits_by_category("S1")["专业选修课程"] == 2.0
    rows = check_selection_rationality(prep)
    s1 = [r for r in rows if r.student_id == "S1"]
    assert s1 and s1[0].expected_credit == 2.0 and s1[0].gap == 2.0
    # 无人修过任何选修 → 应累计 0 → 不触发
    prep0 = _prep(s2_grades=False, grades=[], courses=courses)
    assert check_selection_rationality(prep0) == []


def test_gained_excludes_failed():
    """及格制（D2）：挂科（pass_flag=0）不计已修——修过但未及格仍算差额。"""
    # S1 修 EL1 挂科（pass=0）→ 不计已修 → 差 4.0 触发（旧口径差 2.0）
    prep = _prep(grades=[_grade("S1", "选修课A", 2.0, pass_flag=0)])
    rows = check_selection_rationality(prep)
    assert rows[0].gained_credit == 0.0 and rows[0].gap == 4.0


def test_gained_marker_not_merged():
    """2026-08-31 全面复核：未匹配方案的 ◆/◇ 标记课为方案外通识课（影视鉴赏等），
    不属于培养计划专业课程进度——不计入任何类别已修（此前按标记归并导致已修高估漏报）。"""
    prep = _prep(grades=[_grade("S1", "影视鉴赏", 2.0, marker="◆选修课"),
                         _grade("S1", "量子科学与技术革命", 2.0, marker="◇核心课")])
    rows = check_selection_rationality(prep)
    assert rows[0].gained_credit == 0.0   # 方案外通识课不计已修
    assert rows[0].gap == 4.0             # 应修 4 分全部未修


def test_selected_credits_and_cross_excluded():
    """已选 = 本轮选课专业选修；跨选课/辅修课程排除。"""
    sels = [_sel("S1", "EL1", "选修课A"),
            _sel("S1", "X1", "跨选课X", category="跨选课")]
    prep = _prep(sels=sels)
    rows = check_selection_rationality(prep)
    assert rows[0].selected_credit == 2.0   # 跨选不计入


def test_message_and_summary():
    """结果字段完整性与摘要文本。"""
    prep = _prep(grades=[_grade("S1", "选修课A", 2.0)])
    rows = check_selection_rationality(prep, checked_at="2026-08-31 22:00:00")
    r = rows[0]
    assert r.checked_at and r.message.startswith("选课不符合培养计划")
    assert "专业选修课程差 2.0" in r.message and "应4" in r.message
    s = build_summary(prep, rows)
    assert "检查 2 人" in s and "选课不合理 1 人" in s


def test_match_helpers_direct():
    """_best_substring_match / _acca_renamed_match 直测（第十五批拆出后的
    契约钉子；端到端口径由 test_lead_code_* / test_acca_rename_match 覆盖）。"""
    from academicwarning.rules import _best_substring_match, _acca_renamed_match
    pm = PlanCourse(plan_id=1, course_code="PM1", course_name="PM业绩管理（ACCA）",
                    credit=3.0, course_type="专业核心课程", required_flag="必修",
                    semester="2-1")
    apm = PlanCourse(plan_id=1, course_code="APM1", course_name="APM高级业绩管理（ACCA）",
                     credit=3.0, course_type="专业选修课程", required_flag="选修",
                     semester="3-1")
    by_name = {clean_course_name(c.course_name): c for c in (pm, apm)}
    # 主体全等（业绩管理）优先于更长子串（业绩管理 ⊂ 高级业绩管理）
    body, best = _best_substring_match(by_name, "业绩管理(ACCA)")
    assert body is pm and best is apm
    # 无主体全等 → 最长 clean 名
    body2, best2 = _best_substring_match(by_name, "业绩管理")
    assert body2 is None and best2 is apm
    # ACCA 旧代码：F9 ↔ 方案 FM 开头课；非 f 数字开头 → None
    assert _acca_renamed_match(by_name, "F9财务管理") is None
    fm = PlanCourse(plan_id=1, course_code="FM1", course_name="FM财务管理",
                    credit=3.0, course_type="专业核心课程", required_flag="必修",
                    semester="2-1")
    assert _acca_renamed_match({clean_course_name(fm.course_name): fm},
                               "F9财务管理") is fm
    assert _acca_renamed_match(by_name, "高等数学I-2") is None


def test_not_enrolled_student_skipped():
    """非在籍学生（休学）不参与检查：即使有缺口也不产出行；在籍同缺口照常触发。"""
    students = [
        Student(student_id="S1", name="甲", grade="2023级", major="0824工商管理"),
        Student(student_id="S3", name="丙", grade="2023级", major="0824工商管理",
                enrolled_status="休学"),
    ]
    prep = _prep(students=students, grades=[_grade("S3", "选修课A", 2.0)])
    rows = check_selection_rationality(prep)
    sids = [r.student_id for r in rows]
    assert "S3" not in sids          # 休学 → 跳过
    assert "S1" in sids              # 在籍 → 缺口照常触发


def test_multiple_categories_triggered():
    """2026-08-31 扩展：全课程类别排查——多个类别不足时 message 全部列出。"""
    courses = [
        PlanCourse(plan_id=1, course_code="EL1", course_name="选修课A",
                   credit=2.0, course_type="专业选修课程", required_flag="选修", semester="3-1"),
        PlanCourse(plan_id=1, course_code="EL2", course_name="选修课B",
                   credit=2.0, course_type="专业选修课程", required_flag="选修", semester="3-2"),
        PlanCourse(plan_id=1, course_code="CORE1", course_name="核心必修课",
                   credit=4.0, course_type="专业核心课程", required_flag="必修", semester="2-1"),
    ]
    # S2 修过全部课程（防豁免）；S1 一门未修 → 专业选修差 4 + 核心差 4
    prep = _prep(s2_grades=False, courses=courses,
                 grades=[_grade("S2", "选修课A", 2.0), _grade("S2", "选修课B", 2.0),
                         _grade("S2", "核心必修课", 4.0)])
    rows = check_selection_rationality(prep)
    s1 = [r for r in rows if r.student_id == "S1"]
    assert s1 and "专业选修课程差 4.0" in s1[0].message
    assert "专业核心课程差 4.0" in s1[0].message
    assert s1[0].gap == 4.0


def test_gongshang_41_no_recommended_elective():
    """工商 4-1 场景：方案选修均 ≤3-2、已修充足 → 不触发；已修不足 → 触发。"""
    # 已修 4.0（EL1+EL2 都修）→ 差 0 → 不触发
    prep = _prep(grades=[_grade("S1", "选修课A", 2.0),
                         _grade("S1", "选修课B", 2.0)])
    assert check_selection_rationality(prep) == []
    # 已修 0 → 差 4 → 触发
    assert check_selection_rationality(_prep()) != []


# ---- v7：details 结构化 + 类型B 类别加分 + R1 覆盖排除（2026-09-04 双类型豁免 Task 2）----

def test_missing_req_quad_and_details_code():
    """v7 缺修必修课六元组 (code, name, credit, reason, score, category)；
    details JSON 的 cats[].missing 条目含 code+reason（前端豁免提交以 code 为键、
    reason 供旁注，name 跨专业非唯一）；2026-09-22 起按类别归拢（不再有顶层
    missing / 缺修必修课垃圾筐）。"""
    courses = [
        PlanCourse(plan_id=1, course_code="REQ1", course_name="必修高等数学",
                   credit=4.0, course_type="专业核心课程", required_flag="必修",
                   semester="1-1"),
        PlanCourse(plan_id=1, course_code="REQ2", course_name="必修大学物理",
                   credit=3.0, course_type="专业核心课程", required_flag="必修",
                   semester="2-1"),
    ]
    grades = [_grade("S2", "必修高等数学", 4.0), _grade("S2", "必修大学物理", 3.0)]
    prep = _prep(s2_grades=False, courses=courses, grades=grades)
    assert _missing_required_courses(prep, "S1") == [
        ("REQ1", "必修高等数学", 4.0, "unattempted", "", "专业核心课程"),
        ("REQ2", "必修大学物理", 3.0, "unattempted", "", "专业核心课程")]
    rows = check_selection_rationality(prep)
    s1 = [r for r in rows if r.student_id == "S1"]
    assert s1 and s1[0].gap == 7.0
    d = json.loads(s1[0].details)
    assert d["other_missing"] == []   # 两门均在"专业核心课程"（有差额）内
    assert d["cats"] == [{"cat": "专业核心课程", "expected": 7.0, "gained": 0.0,
                          "selected": 0.0, "gap": 7.0, "gap_ex_missing": 0.0,
                          "missing": [
                              {"code": "REQ1", "name": "必修高等数学", "credit": 4.0,
                               "reason": "unattempted", "score": ""},
                              {"code": "REQ2", "name": "必修大学物理", "credit": 3.0,
                               "reason": "unattempted", "score": ""}]}]
    assert "缺修 《必修高等数学》、《必修大学物理》" in s1[0].message
    # 修过 REQ1（及格）→ 缺修只剩 REQ2（code 级消除），details/差额同步变化
    prep2 = _prep(s2_grades=False, courses=courses,
                  grades=[*grades, _grade("S1", "必修高等数学", 4.0)])
    s1b = [r for r in check_selection_rationality(prep2) if r.student_id == "S1"]
    d2 = json.loads(s1b[0].details)
    assert d2["cats"] == [{"cat": "专业核心课程", "expected": 7.0, "gained": 4.0,
                           "selected": 0.0, "gap": 3.0, "gap_ex_missing": 0.0,
                           "missing": [
                               {"code": "REQ2", "name": "必修大学物理", "credit": 3.0,
                                "reason": "unattempted", "score": ""}]}]
    assert "缺修 《必修大学物理》" in s1b[0].message


def test_waiver_credit_boosts_gained():
    """v7 类型B 旧课学分认可（N3）：waiver_credit 在 gained 计算源头叠加——
    row/message/details 数字均含认可分（前端不二次叠加）；认可足额 → 差额 0
    → 掉出名单。"""
    # 认可 2.0（S1 无真实成绩）→ 差额 4-2=2.0 仍触发，数字含认可分
    prep = _prep(waiver_credit={"S1": {"专业选修课程": 2.0}})
    rows = check_selection_rationality(prep)
    assert len(rows) == 1 and rows[0].gap == 2.0
    r = rows[0]
    assert r.expected_credit == 4.0 and r.gained_credit == 2.0
    assert "专业选修课程差 2.0（应4/已修2.0/已选0.0）" in r.message
    d = json.loads(r.details)
    assert d["cats"] == [{"cat": "专业选修课程", "expected": 4.0, "gained": 2.0,
                          "selected": 0.0, "gap": 2.0, "gap_ex_missing": 2.0,
                          "missing": []}]
    assert d["other_missing"] == []
    # 对照无认可：message 已修 0.0（源头一处叠加生效，数字整体一致）
    base = check_selection_rationality(_prep())[0]
    assert "已修0.0" in base.message and base.gap == 4.0
    # 认可足额 4.0 → 差额 0 → 掉出名单
    assert check_selection_rationality(
        _prep(waiver_credit={"S1": {"专业选修课程": 4.0}})) == []
    # 认可与他人无关：waiver_credit 键控单生，S2 不受 S1 认可影响
    rows2 = check_selection_rationality(_prep(
        waiver_credit={"S2": {"专业选修课程": 2.0}}))
    assert [r.student_id for r in rows2] == ["S1"] and rows2[0].gained_credit == 0.0


def test_covered_by_major_excludes_virtual_row():
    """v7 R1 回归：专业覆盖守卫只计真实成绩——0 真实行 + grade_raw='特殊豁免'
    虚拟行（类型A 注入形态，service 层 Prep 后追加）→ covered 仍 0 → 跳过检查
    （成绩单删除/重传窗口期豁免注入不得把覆盖 0→1 绕过守卫导致整专业假阳性）。"""
    students = [Student(student_id="S1", name="甲", grade="2023级", major="0824工商管理"),
                Student(student_id="S2", name="乙", grade="2023级", major="0824工商管理")]
    courses = [
        PlanCourse(plan_id=1, course_code="REQ1", course_name="必修高数A",
                   credit=4.0, course_type="专业核心课程", required_flag="必修",
                   semester="1-1"),
        PlanCourse(plan_id=1, course_code="REQ2", course_name="必修物理B",
                   credit=4.0, course_type="专业核心课程", required_flag="必修",
                   semester="2-1"),
    ]
    prep = Prep(students, [], [], {"工商管理": courses}, {}, [], "4-1",
                entry_year=2023)
    assert check_selection_rationality(prep) == []   # 该专业 0 成绩行 → 守卫跳过
    # 类型A 豁免注入形态：向 prep.grades 追加 grade_raw='特殊豁免' 的虚拟成绩行
    prep.grades.append(Grade(student_id="S1", course_name="必修高数A", credit=4.0,
                             grade_raw="特殊豁免", pass_flag=1,
                             course_name_clean=clean_course_name("必修高数A")))
    assert check_selection_rationality(prep) == []   # R1：虚拟行不计覆盖 → 仍跳过
    # 有 1 条真实成绩行（S2）→ 覆盖恢复，检查正常执行
    prep.grades.append(Grade(student_id="S2", course_name="必修高数A", credit=4.0,
                             grade_raw="88", pass_flag=1,
                             course_name_clean=clean_course_name("必修高数A")))
    rows = check_selection_rationality(prep)
    assert [r.student_id for r in rows] == ["S1", "S2"]


# ---- 2026-09-07 口径修订 Task 2：及格制（D2/D3）+ 差额>0 触发（D1）+ reason（C1）----

def test_gained_excludes_failed_rep():
    """及格制：同课多行取最高分代表行——挂科+补考通过 → 只计一次且来自及格行
    （2.0 而非逐行累加 4.0）。"""
    s1 = [_grade("S1", "选修课A", 2.0, pass_flag=0),
          _grade("S1", "选修课A", 2.0, pass_flag=1)]
    prep = _prep(grades=s1)   # 装配方式照抄 test_gained_excludes_failed 的既有写法
    gained = _gained_by_category(prep, "S1")
    assert gained.get("专业选修课程", 0.0) == 2.0   # 只计一次且来自及格行


def test_missing_reason_failed_vs_unattempted():
    """缺修 reason：有成绩但最高分不及格（REQ1 挂科无重考）→ failed；
    完全无成绩（REQ2）→ unattempted；details cats[].missing 同带 reason。"""
    courses = [
        PlanCourse(plan_id=1, course_code="REQ1", course_name="必修高等数学",
                   credit=4.0, course_type="专业核心课程", required_flag="必修",
                   semester="1-1"),
        PlanCourse(plan_id=1, course_code="REQ2", course_name="必修大学物理",
                   credit=3.0, course_type="专业核心课程", required_flag="必修",
                   semester="2-1"),
    ]
    # S2 全修（防全员无成绩豁免）；S1：REQ1 挂科无重考、REQ2 完全无成绩
    grades = [_grade("S2", "必修高等数学", 4.0),
              _grade("S2", "必修大学物理", 3.0),
              _grade("S1", "必修高等数学", 4.0, pass_flag=0)]
    prep = _prep(s2_grades=False, courses=courses, grades=grades)
    assert _missing_required_courses(prep, "S1") == [
        ("REQ1", "必修高等数学", 4.0, "failed", "80", "专业核心课程"),
        ("REQ2", "必修大学物理", 3.0, "unattempted", "", "专业核心课程")]
    rows = check_selection_rationality(prep)
    s1 = [r for r in rows if r.student_id == "S1"]
    d = json.loads(s1[0].details)
    assert d["cats"][0]["missing"] == [
        {"code": "REQ1", "name": "必修高等数学", "credit": 4.0,
         "reason": "failed", "score": "80"},
        {"code": "REQ2", "name": "必修大学物理", "credit": 3.0,
         "reason": "unattempted", "score": ""}]
    assert "缺修 《必修高等数学》、《必修大学物理》" in s1[0].message


def test_other_missing_when_category_gap_zero():
    """2026-09-22 方案2：所在类别差额=0 但必修课仍挂科/未修 → 进 other_missing
    （类别学分已达标仍提示重修，防漏修）。构造：公共课程两门满学分且及格达标，
    第三门学分 0 的必修挂科 → 公共课程差额 0，但挂科课须进 other_missing。"""
    courses = [
        PlanCourse(plan_id=1, course_code="PUB1", course_name="体育-1",
                   credit=1.0, course_type="公共课程", required_flag="必修",
                   semester="1-1"),
        PlanCourse(plan_id=1, course_code="PUB2", course_name="体育-2",
                   credit=1.0, course_type="公共课程", required_flag="必修",
                   semester="1-2"),
        PlanCourse(plan_id=1, course_code="PUB3", course_name="体育-3",
                   credit=0.0, course_type="公共课程", required_flag="必修",
                   semester="2-1"),
    ]
    # S2 全修（防全员无成绩豁免）；S1：PUB1/PUB2 及格（公共课程应修 2 已修 2 → 差 0），
    # PUB3 学分 0 挂科 → 不占差额但须报缺修
    grades = [_grade("S2", "体育-1", 1.0), _grade("S2", "体育-2", 1.0),
              _grade("S2", "体育-3", 0.0),
              _grade("S1", "体育-1", 1.0), _grade("S1", "体育-2", 1.0),
              _grade("S1", "体育-3", 0.0, pass_flag=0)]
    prep = _prep(s2_grades=False, courses=courses, grades=grades)
    miss = _missing_required_courses(prep, "S1")
    assert miss == [("PUB3", "体育-3", 0.0, "failed", "80", "公共课程")]
    rows = check_selection_rationality(prep)
    s1 = [r for r in rows if r.student_id == "S1"]
    assert s1, "缺修课触发应有结果行"
    d = json.loads(s1[0].details)
    # 公共课程差额=0 → 不进 cats；挂科课进 other_missing（带 cat 字段）
    assert d["cats"] == []
    assert d["other_missing"] == [{"code": "PUB3", "name": "体育-3", "credit": 0.0,
                                   "reason": "failed", "score": "80",
                                   "cat": "公共课程"}]
    assert s1[0].category == "公共课程"   # 无差额时 category 取首门缺修课类别
