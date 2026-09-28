"""规则前置推导：成绩换算已入 parsers.grade_to_pass；本模块负责
应修/已获/已选学分聚合、学籍异动叠加（M5）、在修判定。"""
import re

from .models import (Student, Selection, Grade, PlanCourse, PlanSemesterCourse)
from .parsers import (CATEGORY_MAP, EXCLUDED_CATEGORIES, clean_course_name,
                      normalize_major)

# ACCA 2018 课程改名映射（成绩单旧代码 → 方案新代码；2026-08-31 匹配修复）
_ACCA_RENAMED = {"F1": "BT", "F2": "MA", "F3": "FA", "F4": "LW",
                 "F5": "PM", "F6": "TX", "F7": "FR", "F8": "AA", "F9": "FM"}
# 前导英文代码/词 + 可选空格（课程名匹配用；2026-09-04 用户确认：容忍
# "前导英文代码/词"差异，以中文主体匹配为准——金标准学生《业绩管理(ACCA)》应匹配
# 方案《PM业绩管理（ACCA）》）
_LEAD_ENG_RE = re.compile(r"^[A-Za-z0-9]+\s*")


def _strip_lead_eng(name: str) -> str:
    """剥离前导英文代码/词（'PM业绩管理（ACCA）'→'业绩管理（ACCA）'；
    'Python 数据分析-1'→'数据分析-1'）；剥离后须含中文才生效。"""
    m = _LEAD_ENG_RE.match(name)
    if not m:
        return name
    rest = name[m.end():]
    return rest if re.search(r"[一-鿿]", rest) else name


def _best_substring_match(by_name: dict, clean: str) -> tuple:
    """v6.1/2026-09-04 候选收集：双向子串（原始或剥前导英文后）中
    (中文主体全等者, 最长 clean 名者)——主体全等最可信（PM 胜 APM 场景）、
    否则最长 clean 名最具体（防"管理学"吞"管理学新生导论"误归因）。"""
    best = None
    best_len = -1
    body_exact = None
    gs = _strip_lead_eng(clean)
    for name, c in by_name.items():
        ps = _strip_lead_eng(name)
        if not (clean and (clean in name or name in clean
                           or (gs and ps and (ps in gs or gs in ps)))):
            continue
        if body_exact is None and gs and ps and ps == gs:
            body_exact = c
        if len(name) > best_len:
            best, best_len = c, len(name)
    return body_exact, best


def _acca_renamed_match(by_name: dict, clean: str):
    r"""ACCA 2018 改名裁决：成绩单旧代码 F1-F9 ↔ 方案新代码 BT/MA/FA/LW/PM/TX/
    FR/AA/FM（2026-08-31 修复：金标准学生等 ACCA 学生 F 系列成绩未匹配 → 已修
    低估误报）。F 系旧代码 clean 名以 f\d 开头，方案名 startswith 新代码。"""
    m = re.search(r"f(\d)", clean.lower())
    if not m:
        return None
    new_code = _ACCA_RENAMED.get(f"F{m.group(1)}")
    if new_code:
        for name, c in by_name.items():
            if name.lower().startswith(new_code.lower()):
                return c
    return None


# Table 1 课程类型 → 学分结构 JSON 类别名（2026-08-31 以 JSON 为基准）
_CAT_RENAME = {"数学和基础科学类课程": "学科门类基础课程"}
# 选课类别（CATEGORY_MAP 值）→ JSON 类键（2026-09-01 Task 5）：CATEGORY_MAP 给
# 出方案侧类别名，与应修/已修的 JSON 类键不匹配 → 已选恒 0、差额虚高误报。
# 通识两类归并到"模块课程"（孙镜麒误报根因之二）；数学类归并到"学科门类
# 基础课程"（同 _CAT_RENAME 语义，审查 F1）
_SEL_CAT_TO_JSON = {"模块课程·核心课程": "模块课程", "模块课程·选修课程": "模块课程",
                    "数学和基础科学类课程": "学科门类基础课程"}
# 留学生豁免课程（2026-08-31 用户确认：不学英语/思政课/军训——含军事理论/国防教育）
_FOREIGN_EXEMPT_KEYWORDS = ("思想道德", "中国近现代史", "毛泽东", "马克思", "习近平",
                            "形势与政策", "思想政治", "国防教育", "军事理论", "军训")


class Prep:
    """按学生聚合前置数据。semester_code 为当前学期编码（如 '4-1'）。

    C1：plan_by_major/sem_by_major 键为 normalize_major 后的标准专业名，
    所有按学生消费的接口按学生专业取对应方案的应修/推荐课程，消除跨专业误报。
    兼容旧调用：plan_courses/sem_courses 为合并列表时包成单池（键 ""，任何学生均回落）。
    """

    def __init__(self, students: list[Student], selections: list[Selection],
                 grades: list[Grade],
                 plan_by_major: dict[str, list[PlanCourse]] | None = None,
                 sem_by_major: dict[str, list[PlanSemesterCourse]] | None = None,
                 status_changes: list | None = None,
                 semester_code: str = "", entry_year: int = 0,
                 ocr_failed: set[str] | None = None,
                 *, plan_courses: list[PlanCourse] | None = None,
                 sem_courses: list[PlanSemesterCourse] | None = None,
                 elective_req_by_major: dict[str, float] | None = None,
                 credit_req_by_major: dict[str, dict[str, float]] | None = None,
                 covered_sem: str = "", current_sem_override: str = "",
                 waiver_credit: dict[str, dict[str, float]] | None = None):
        self.students = {s.student_id: s for s in students}
        self.selections = [s for s in selections if s.status == "选中"]  # M4
        self.grades = grades
        # v6.3：年级显式当前学期配置优先（2026-09-02）；空 = 保持选课文件推断。
        # 单点替换 semester_code —— 后续 _due_courses_by_major/rec_courses/
        # _effective_change/_compute_missing_courses 等引用点自动生效。
        self.semester_code = current_sem_override or semester_code
        self.current_sem = self.semester_code   # 分级豁免（Step 4）用名
        self.covered_sem = covered_sem          # 成绩覆盖学期（term_label 解析/人工确认）
        self.entry_year = entry_year or 2023  # 单届约束下默认当前届
        self.ocr_failed = ocr_failed or set()
        # 2026-08-31：学分结构毕业要求（模块课程 12/专业选修 8·10·12·15——JSON 权威）
        self.elective_req_by_major = elective_req_by_major or {}
        self.credit_req_by_major = credit_req_by_major or {}
        # v7 类型B 旧课学分认可：sid → {类别: 认可学分}（service 装配时附加，
        # 检查层在 gained 计算源头叠加——N3）
        self.waiver_credit = waiver_credit or {}

        self._init_plan_tables(plan_by_major, sem_by_major,
                               plan_courses, sem_courses)
        self._init_changes(status_changes)
        self._build_plan_indexes()
        self._build_rec_index()
        # 2026-08-30 用户确认：已开课（≤ 当前学期）但该专业全体有成绩学生
        # 成绩单均无记录的课程 → 成绩缺失豁免（不报缺修/缺口/进度落后），
        # 质量清单提示管理员核对（如 3-3 小学期成绩 9-10 月才出）。
        self._missing_courses = self._compute_missing_courses()

    def _init_plan_tables(self, plan_by_major, sem_by_major,
                          plan_courses, sem_courses) -> None:
        """兼容两种入参形态：按专业分表 或 单一列表（列表合并为 "" 键）。"""
        if plan_by_major is None:
            plan_by_major = {"": list(plan_courses or [])}
        if sem_by_major is None:
            sem_by_major = {"": list(sem_courses or [])}
        self._plan_by_major = plan_by_major
        self._sem_by_major = sem_by_major
        # 兼容：合并后的全量列表（旧调用方读取用）
        self.plan_courses = [c for lst in plan_by_major.values() for c in lst]
        self.sem_courses = [c for lst in sem_by_major.values() for c in lst]

    def _init_changes(self, status_changes) -> None:
        """M5：学籍异动按学生索引；del_selection（2026-08-28 补实现）——
        转专业且"是否删除选课结果=是"的学生，原专业选课不计入
        （样本 26 条 del_selection=是）。"""
        self._changes_by_student: dict[str, list] = {}
        for c in status_changes or []:
            self._changes_by_student.setdefault(c.student_id, []).append(c)
        self._del_selection_students = {
            sid for sid, changes in self._changes_by_student.items()
            for c in changes
            if c.change_type == "转专业" and c.del_selection == "是"
        }

    def _build_plan_indexes(self) -> None:
        """C1：按专业建索引——课程号/课程名/应修集（开课学期 ≤ 当前学期，
        学期编码字符串比较，如 "4-1" > "2-2"）。"""
        self._plan_by_code_major = {
            m: {c.course_code: c for c in lst}
            for m, lst in self._plan_by_major.items()}
        self._plan_by_name_major = {
            m: {clean_course_name(c.course_name): c for c in lst}
            for m, lst in self._plan_by_major.items()}
        self._due_courses_by_major = {
            m: [c for c in lst
                if c.semester and self._sem_le(c.semester, self.semester_code)]
            for m, lst in self._plan_by_major.items()}
        # 兼容：合并后的应修集
        self._due_courses = [c for lst in self._due_courses_by_major.values() for c in lst]

    def _build_rec_index(self) -> None:
        """C1：推荐课表按专业→学期分组。"""
        self._rec_courses_by_major: dict[str, dict[str, list[PlanSemesterCourse]]] = {}
        for m, lst in self._sem_by_major.items():
            by_sem: dict[str, list[PlanSemesterCourse]] = {}
            for c in lst:
                by_sem.setdefault(c.semester, []).append(c)
            self._rec_courses_by_major[m] = by_sem

    def _course_missing_for_major(self, c: PlanCourse, graded_cleans: set[str],
                                  sel_codes: set[str]) -> bool:
        """单门方案课"全员无成绩且应豁免"判定（分支语义见 _compute_missing_courses；
        成绩名匹配沿用 v6.1 子串单向口径）。"""
        cn = clean_course_name(c.course_name)
        has_grade = bool(cn) and any(cn in gc for gc in graded_cleans)
        # 2026-09-04：前导英文容忍（防漏）——双方剥前导英文代码/词后再
        # 单向（方案名 ⊂ 成绩单名），如成绩单《业绩管理(ACCA)》可代表
        # 方案《PM业绩管理（ACCA）》有人修过（金标准学生案例同源）
        if not has_grade:
            cn_s = _strip_lead_eng(cn)
            has_grade = bool(cn_s) and any(
                cn_s in _strip_lead_eng(gc) for gc in graded_cleans)
        if has_grade:
            return False
        if "至" in c.semester or "，" in c.semester or "," in c.semester:
            return True   # 复合学期数据源缺失
        if self._sem_le(c.semester, self.covered_sem):
            return False   # 成绩应已出且无记录 = 真缺 → 不豁免
        # 规则 B：semester == current_sem 且该专业有人选 → 本学期在开 → 不豁免
        # （靠选课判断，没选即缺修；选了 = 差 0）；否则（讲座卡/科研型池）→ 豁免
        return not (c.semester == self.current_sem and c.course_code in sel_codes)

    def _compute_missing_courses(self) -> dict[str, set[str]]:
        """按专业统计"全员无成绩"课程（course_code 集合）——分级豁免（2026-09-02）。

        输入：current_sem（年级配置优先，self.current_sem）、covered_sem（成绩单
        实际覆盖学期，self.covered_sem）、选课覆盖（该专业学生选中课程）。
        判定（semester ≤ current_sem 的课程，_course_missing_for_major 逐门裁决）：
        1) 复合学期课（"1-1至4-1"/"1-1，2-1"——每学期开课型）全员无成绩单记录
           → 数据源缺失（形策等）→ 豁免（清单人工核对）
        2) semester ≤ covered_sem → 成绩应已出 → 无记录 = 真缺 → 不豁免
        3) covered_sem < semester ≤ current_sem（成绩未出窗口）：
           - semester == current_sem 且该专业有人选 → 本学期在开 → 不豁免
             （靠选课判断，没选即缺修；选了 = 差 0）——规则 B
           - 否则（讲座卡/科研型池/3-3 未出学期）→ 豁免
        """
        missing: dict[str, set[str]] = {}
        for major, courses in self._plan_by_major.items():
            covered = {g.student_id for g in self.grades
                       if self._major_for(g.student_id) == major}
            if not covered:
                continue
            graded_cleans = {clean_course_name(g.course_name) for g in self.grades
                             if g.student_id in covered}
            sel_codes = {s.course_code for s in self.selections
                         if self._major_for(s.student_id) == major}
            for c in courses:
                if not c.semester or not self._sem_le(c.semester, self.current_sem):
                    continue
                if self._course_missing_for_major(c, graded_cleans, sel_codes):
                    missing.setdefault(major, set()).add(c.course_code)
        return missing

    def missing_courses(self, sid: str) -> set[str]:
        """该生专业的全员无成绩课程（course_code 集合，计算豁免用）。"""
        return self._missing_courses.get(self._major_for(sid), set())

    @staticmethod
    def _sem_le(a: str, b: str) -> bool:
        """学期编码 a ≤ b（"2-2" ≤ "4-1"）。

        复合格式（"1-1至4-1" / "1-1，2-1"）取起始学期比较——如形势与政策/体育
        每学期开课，开课起始 ≤ 当前学期即应修（2026-08-31 修复：此前被剔除导致
        应修低估，公共课差 4 学分的系统性偏差）。"""
        a0 = re.split(r"[至，,]", a)[0].strip()
        try:
            ay, at = map(int, a0.split("-"))
            by, bt = map(int, b.split("-"))
            return (ay, at) <= (by, bt)
        except (ValueError, AttributeError):
            return False

    @staticmethod
    def _sem_tuple(s: str) -> tuple[int, int] | None:
        """学期编码 → (学年, 学期) 元组；异常格式（如"1-1至4-1"）返回 None。"""
        try:
            y, t = map(int, s.split("-"))
            return (y, t)
        except (ValueError, AttributeError):
            return None

    def _effective_change(self, sid: str):
        """该生生效日期 ≤ 当前学期末的最新一条（M5）。"""
        changes = sorted(
            (c for c in self._changes_by_student.get(sid, []) if c.effective_date),
            key=lambda c: c.effective_date,
        )
        if not changes:
            return None
        # 当前学期末：第 X 学年（起始年 = entry_year + X - 1）的第 1/2/3 学期末
        # 第1学期末=次年1-31，第2学期末=次年7-31，第3学期末=次年9-30
        year_idx = int(self.semester_code.split("-")[0])
        term = self.semester_code.split("-")[-1]
        sem_end_month = {"1": "01-31", "2": "07-31", "3": "09-30"}.get(term, "07-31")
        end_str = f"{self.entry_year + year_idx}-{sem_end_month}"
        for c in reversed(changes):
            if c.effective_date[:10] <= end_str:
                return c
        return None

    def student_effective_major(self, sid: str) -> str:
        """M5：转专业生效 → 异动后专业；否则基准名单专业。"""
        base = self.students.get(sid)
        if base is None:
            return ""
        c = self._effective_change(sid)
        if c and c.change_type == "转专业" and c.new_major:
            return c.new_major
        return base.effective_major or base.major

    def student_grade(self, sid: str) -> str:
        """M2：留级/复学 → 异动后年级；否则基准名单年级。"""
        base = self.students.get(sid)
        if base is None:
            return ""
        c = self._effective_change(sid)
        if c and c.change_type in ("留级", "复学") and c.new_grade:
            return c.new_grade
        return base.grade

    def plan_sync(self, sid: str) -> str:
        c = self._effective_change(sid)
        return c.new_plan_sync if c else ""

    def enrolled_status(self, sid: str) -> str:
        """非在籍判定（I4，设计 §2）：仅 退学/休学/保留学籍 → 非在籍；
        退学警告是需预警人群 → 在籍。"""
        base = self.students.get(sid)
        if base is None:
            return "不在名单"
        c = self._effective_change(sid)
        if c and c.change_type in ("退学", "休学", "保留学籍"):
            return c.change_type
        if c and c.change_type == "复学":
            return "在籍"
        return "在籍"

    # ---- C1：按学生专业取方案 ----
    def _major_for(self, sid: str) -> str:
        """学生标准专业名（归一化后；转专业生效 → 异动后专业）。"""
        return normalize_major(self.student_effective_major(sid))

    def _courses_for(self, sid: str) -> list[PlanCourse]:
        """该生专业的方案课程（未知专业回落旧调用单池 ""）。"""
        major = self._major_for(sid)
        if major in self._plan_by_major:
            return self._plan_by_major[major]
        return self._plan_by_major.get("", [])

    def due_required(self, sid: str) -> list[PlanCourse]:
        """应修必修课程集（截至当前学期，按学生专业，规则 1 用）。
        全员无成绩课程豁免（2026-08-30 用户确认）。"""
        missing = self.missing_courses(sid)
        return [c for c in self._due_courses_by_major.get(self._major_for(sid), [])
                if c.required_flag == "必修" and c.course_code not in missing]

    def rec_courses(self, sid: str) -> list[PlanSemesterCourse]:
        """本学期推荐课表（按学生专业，规则 5/6 用）；全员无成绩课程豁免。"""
        missing = self.missing_courses(sid)
        return [c for c in self._rec_courses_by_major.get(self._major_for(sid), {}).get(
            self.semester_code, []) if c.course_code not in missing]

    def due_rec_required(self, sid: str) -> list[PlanSemesterCourse]:
        """规则 6（2026-08-30 用户确认 B 方案）：推荐课表中"已到期且成绩单已覆盖
        学期"的必修课（未修者报进度落后）。

        - 未来推荐课（开课 > 当前学期）不判定
        - 当前学期推荐课成绩未出（sem > grade_max）不判定（如形势与政策 4-1）
        - 全员无成绩课程豁免（missing）
        - 必修过滤：推荐课表无 required_flag 字段，按 course_code 关联方案课程"""
        missing = self.missing_courses(sid)
        grade_max = self.grade_max_sem(sid)
        codes = self._plan_by_code_major.get(self._major_for(sid), {})
        out: list[PlanSemesterCourse] = []
        for lst in self._rec_courses_by_major.get(self._major_for(sid), {}).values():
            for rc in lst:
                sem = self._sem_tuple(rc.semester)
                if not sem or not self._sem_le(rc.semester, self.semester_code):
                    continue   # 未来推荐课
                if grade_max is not None and sem > grade_max:
                    continue   # 成绩待出（当前学期推荐课，如 4-1 形势与政策）
                if rc.course_code in missing:
                    continue
                pc = codes.get(rc.course_code)
                if pc is None or pc.required_flag != "必修":
                    continue   # 仅必修推荐课计入进度落后
                out.append(rc)
        return out

    def future_required(self, sid: str) -> list[PlanCourse]:
        """未来必修课（开课 > 当前学期，未修）——2026-08-30 起仅提示不报预警。"""
        attended = self.attended_or_selected(sid)
        missing = self.missing_courses(sid)
        return [c for c in self._courses_for(sid)
                if c.required_flag == "必修" and c.semester
                and not self._sem_le(c.semester, self.semester_code)
                and c.course_code not in missing
                and clean_course_name(c.course_name) not in attended
                and c.course_code not in attended]

    # ---- 学分聚合 ----
    def _student_selections(self, sid: str) -> list[Selection]:
        """该生选课。若生效转专业记录"是否删除选课结果=是"，原专业选课不计入
        （2026-08-28 补实现，文档 §2 关联规则 4；样本 26 条 del_selection=是）。"""
        if sid in self._del_selection_students:
            return []
        return [s for s in self.selections if s.student_id == sid]

    def _student_grades(self, sid: str) -> list[Grade]:
        return [g for g in self.grades if g.student_id == sid]

    def required_credits_by_category(self, sid: str) -> dict[str, float]:
        """应修 per 类别（2026-08-31 以方案学分结构 JSON 为基准）。

        类别体系 = JSON 8 类（公共/模块/学科门类基础/专业大类基础/专业核心/
        专业选修/集中实践/课外实践）。应修值混合口径：
        - 模块课程、专业选修课程 = JSON 毕业要求全量（4-1 前已全部开课）
        - 其余类别 = Table 1 课程（≤ 当前学期，剔除全员无成绩）——自动排除
          毕业设计（4-2）等未来课程；课外实践无课程，不计。
        - Table 1 "数学和基础科学类课程" 归并为 JSON 类别"学科门类基础课程"。"""
        major = self._major_for(sid)
        out: dict[str, float] = {}
        missing = self.missing_courses(sid)
        is_foreign = "999" in sid
        for c in self._due_courses_by_major.get(major, []):
            if c.course_code in missing:
                continue
            if is_foreign and any(k in c.course_name for k in _FOREIGN_EXEMPT_KEYWORDS):
                continue   # 留学生豁免：军训/国防教育/军事理论/思政（2026-08-31）
            cat = _CAT_RENAME.get(c.course_type, c.course_type)
            out[cat] = out.get(cat, 0.0) + c.credit
        for cat in ("模块课程", "专业选修课程"):
            req = self.credit_req_by_major.get(major, {}).get(cat, 0.0)
            if req > 0:
                out[cat] = req   # JSON 毕业要求覆盖 Table 1 汇总
        # 2026-08-31 留学生豁免：不学英语/思政/军训——学号 999 段 = 国际学生
        if "999" in sid:
            pub = self.credit_req_by_major.get(major, {}).get("公共课程", 0.0)
            exempt = self.credit_req_by_major.get(major, {}).get(
                "公共课程_留学生豁免", 0.0)
            if pub > 0:
                out["公共课程"] = pub - exempt
            # 集中实践应修（Table 1 进度）剔除军训课程（军训已豁免）
            if "集中实践" in out:
                out["集中实践"] -= sum(c.credit for c in
                                       self._due_courses_by_major.get(major, [])
                                       if c.course_type == "集中实践"
                                       and "军训" in c.course_name)
        return out

    def gained_credits_by_category(self, sid: str) -> dict[str, float]:
        """已获 per 类别：及格课程的学分。

        2026-08-30 落实 005 §5.1：匹配不到方案的课程按成绩单标记归并
        （◇核心课→专业核心课程、◆选修课→专业选修课程）；无标记未匹配
        仍进 unmatched（N3 单列）。"""
        out: dict[str, float] = {}
        marker_cat = {"◇核心课": "专业核心课程", "◆选修课": "专业选修课程"}
        for g in self._student_grades(sid):
            if g.pass_flag != 1:
                continue
            plan_course = self._match_plan_course(sid, g)
            if plan_course is not None:
                cat = plan_course.course_type
            else:
                cat = marker_cat.get(g.marker, "")
                if not cat:
                    continue  # N3：无标记未匹配 → 未归类学分单独返回
            out[cat] = out.get(cat, 0.0) + g.credit
        return out

    def unmatched_credits(self, sid: str) -> float:
        """N3：未匹配到方案课程的已修（及格）学分，单列展示。"""
        total = 0.0
        for g in self._student_grades(sid):
            if g.pass_flag == 1 and self._match_plan_course(sid, g) is None:
                total += g.credit
        return total

    def gained_total_credits(self, sid: str) -> float:
        """累计已获学分（全部及格成绩学分合计，I2 达成率分子）。"""
        return sum(g.credit for g in self._student_grades(sid) if g.pass_flag == 1)

    def plan_total_credits(self, major: str) -> float:
        """方案总要求学分（Table 1 全部课程学分合计，I2 达成率分母）。"""
        return sum(c.credit for c in self._plan_by_major.get(normalize_major(major), []))

    def cross_selected(self, sid: str) -> tuple[int, float]:
        """跨选/辅修课程（门数, 学分合计）——明细附注（设计 §2 第 5 条）。"""
        rows = [s for s in self._student_selections(sid)
                if s.category in EXCLUDED_CATEGORIES]
        return len(rows), sum(s.credit for s in rows)

    def selected_summary(self, sid: str) -> tuple[int, float]:
        """本学期选中课程（门数, 学分合计）——课表权威值交叉校验（I2）。"""
        rows = self._student_selections(sid)
        return len(rows), sum(s.credit for s in rows)

    def unmatched_grade_courses(self) -> list[tuple[str, int]]:
        """成绩单未匹配到任何专业方案课程的清单 [(课程名, 人次)]（I2 数据质量）。"""
        counts: dict[str, int] = {}
        for sid in self.students:
            for g in self._student_grades(sid):
                if self._match_plan_course(sid, g) is None:
                    name = clean_course_name(g.course_name) or g.course_name
                    counts[name] = counts.get(name, 0) + 1
        return sorted(counts.items(), key=lambda kv: -kv[1])

    def outside_roster_students(self) -> set[str]:
        """有成绩/选课但不在基准名单的学生（L10，I2 数据质量）。"""
        known = set(self.students)
        outside: set[str] = set()
        for g in self.grades:
            if g.student_id not in known:
                outside.add(g.student_id)
        for s in self.selections:
            if s.student_id not in known:
                outside.add(s.student_id)
        return outside

    def selected_credits_by_category(self, sid: str) -> dict[str, float]:
        """本学期已选 per 类别（仅"选中"，M4；学分已由 service 关联 plan_course）。

        类别键与 required/gained 一致（JSON 8 类：通识归并到"模块课程"）。"""
        out: dict[str, float] = {}
        for s in self._student_selections(sid):
            cat = CATEGORY_MAP.get(s.category)
            if cat is None or s.category in EXCLUDED_CATEGORIES:
                continue
            key = _SEL_CAT_TO_JSON.get(cat, cat)
            out[key] = out.get(key, 0.0) + s.credit
        return out

    def selected_total_credits(self, sid: str) -> float:
        """本学期已选总学分（规则5 用；跨选/辅修不计入，但超选按方案内课程算）。"""
        return sum(s.credit for s in self._student_selections(sid)
                   if s.category not in EXCLUDED_CATEGORIES)

    def _match_plan_course(self, sid: str, g: Grade) -> PlanCourse | None:
        r"""按学生专业的方案匹配成绩课程（C1）。

        子串命中取 **clean 名最长**的候选（v6.1 修复：此前按 dict 顺序取首个
        命中——工业工程方案含"管理学"与"管理学新生导论"，成绩"管理学新生导论课"
        被误归因到"管理学"→ 缺修判定全员假阳性，被豁免修复暴露）。
        2026-09-04：候选命中里优先"剥前导英文代码/词后中文主体全等"者——成绩
        《业绩管理(ACCA)》同时是《PM业绩管理（ACCA）》的主体全等与
        《APM高级业绩管理（ACCA）》的子串（金标准：应归必修 PM 而非
        更长的选修 APM），双向子串（原始或剥前导英文后）收集，主体全等优先、
        否则最长 clean 名（_best_substring_match）。F1-F9 旧代码成绩（clean 以
        f\d 开头，如《F9财务管理》）不进剥离候选竞争（审查 I-1：剥后裸中文主体
        会子串命中更长的同族选修，如 财务管理 ⊂ AFM高级财务管理（ACCA））——
        由 _acca_renamed_match 的 startswith 分支裁决。"""
        by_name = self._plan_by_name_major.get(self._major_for(sid), {})
        # 实时清洗（grade.course_name_clean 为入库时旧版清洗结果，与新归一化不一致——
        # 2026-08-27 ACCA 课程误报根因，须实时计算）
        clean = clean_course_name(g.course_name)
        if clean in by_name:
            return by_name[clean]
        is_f_code = bool(re.match(r"^f\d", clean.lower()))
        if not is_f_code:
            body_exact, best = _best_substring_match(by_name, clean)
            if body_exact is not None:
                return body_exact   # 中文主体全等最可信（PM 胜 APM 场景）
            if best is not None:
                return best
        return _acca_renamed_match(by_name, clean)

    def passed_courses(self, sid: str) -> set[str]:
        """已通过课程名集合（含方案匹配名）。"""
        out = set()
        for g in self._student_grades(sid):
            if g.pass_flag == 1:
                out.add(clean_course_name(g.course_name))
                c = self._match_plan_course(sid, g)
                if c:
                    out.add(c.course_name)
        return out

    def attended_or_selected(self, sid: str) -> set[str]:
        """已修∪已选（"在修"判定，§5.0）：成绩单课程名 + 选课课程名/编码。"""
        out = self.passed_courses(sid)
        out.update(clean_course_name(g.course_name) for g in self._student_grades(sid))
        for s in self._student_selections(sid):
            out.add(clean_course_name(s.course_name))
            out.add(s.course_code)
        return out

    def failed_courses(self, sid: str) -> set[str]:
        """挂科课程名（最后一次成绩为准，▲△ 覆盖初修——按成绩单文档顺序）。"""
        latest: dict[str, Grade] = {}
        for g in self._student_grades(sid):
            latest[clean_course_name(g.course_name)] = g  # 文档顺序后到覆盖
        return {name for name, g in latest.items() if g.pass_flag == 0}

    def failed_grades(self, sid: str) -> list[Grade]:
        latest: dict[str, Grade] = {}
        for g in self._student_grades(sid):
            latest[clean_course_name(g.course_name)] = g
        return [g for g in latest.values() if g.pass_flag == 0]

    def data_incomplete(self, sid: str) -> bool:
        return sid in self.ocr_failed

    def grade_max_sem(self, sid: str) -> tuple[int, int] | None:
        """该生成绩单覆盖的最大学期编码（如 (3,2)）；无成绩返回 None。

        2026-08-27 业务口径：小学期课程（如 3-3）刚结束、成绩 9-10 月才出，
        成绩单未覆盖 ≠ 未修——开课学期在成绩覆盖之后的课程不参与缺修判定。"""
        codes = []
        for g in self._student_grades(sid):
            m = re.search(r"第([一二三四五六])学年[（(]?\s*\d{4}-\d{4}\s*[）)]?\s*第([一二三])学期", g.term_label)
            if m:
                codes.append(("一二三四五六".index(m.group(1)) + 1,
                             "一二三".index(m.group(2)) + 1))
        return max(codes) if codes else None
