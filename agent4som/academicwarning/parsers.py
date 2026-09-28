"""三类教务文件解析器（2026-08-31 精简：选课检查只需方案/选课/成绩单）。

- 培养方案 docx：python-docx 解析 Tables 0-5
- 选课结果 xlsx：openpyxl
- 成绩单 docx：python-docx 表格 + 内嵌图片 VL OCR（复用 knowledge_base._parse_with_vl）
"""
import contextlib
import logging
import os
import re
import time

from .models import Grade, PlanCourse, PlanSemesterCourse

logger = logging.getLogger("academicwarning.parsers")

# 培养方案 Table 1 有效课程类型（4 专业样本核对）；"小计/总计/合计/模块课程" 行为非课程行
PLAN_COURSE_TYPES = {
    "公共课程", "数学和基础科学类课程", "专业大类基础课程",
    "专业核心课程", "专业选修课程", "集中实践",
}
_SKIP_TYPE_KEYWORDS = ("小计", "总计", "合计", "模块课程")

# 选课结果/成绩单类别 → 培养方案类别（§2 映射表，可配置）
CATEGORY_MAP = {
    "公共课程": "公共课程",
    "基础通识类核心课": "模块课程·核心课程",
    "基础通识类选修课": "模块课程·选修课程",
    "数学和基础科学类课程": "数学和基础科学类课程",
    "专业大类基础课程": "专业大类基础课程",
    "专业核心课程": "专业核心课程",
    "专业选修课程": "专业选修课程",
    "集中实践": "集中实践",
}
EXCLUDED_CATEGORIES = {"跨选课", "辅修课程"}

# 4 专业标准名（M1 归一化：plan 正文全名 / grade 文件名短名 → 标准名）
MAJOR_CANONICAL = {
    "工商管理": "工商管理",
    "大数据管理与应用": "大数据管理与应用",
    "大数据": "大数据管理与应用",
    "工业工程": "工业工程",
    "会计学（ACCA）": "会计学（ACCA）",
    "会计学": "会计学（ACCA）",
    "ACCA": "会计学（ACCA）",
}

# 推荐课表表头："第X学期：Y-Z"（Tables 2-5 覆盖第一~第八学期；小学期表头不含"第X学期"）
_SEM_HEADER_RE = re.compile(r"第[一二三四五六七八]学期[:：]\d-\d")


def canonical_major(name: str) -> str | None:
    """专业名识别：命中 MAJOR_CANONICAL 关键词返回标准名；未命中返回 None（无法识别）。

    v1.7：detect_major/入库兜底用——"未识别"须与"识别为原样名"区分（如改名文件
    "成绩单.docx"不含任何专业词），否则专业强校验会误拒改名文件。"""
    for key, canonical in MAJOR_CANONICAL.items():
        if key in name:
            return canonical
    return None


def normalize_major(name: str) -> str:
    """专业名归一化（M1）。grade 文件名（"2023级大数据管理与用应专业成绩单"）与
    plan 正文名（"大数据管理与应用专业培养方案"）统一为 4 专业标准名。"""
    return canonical_major(name) or name


def detect_major(path: str, file_type: str, display_name: str | None = None) -> str | None:
    """v1.7 专业预检（与解析同源）：plan 读正文标题正则"X专业培养方案" + 文件名兜底；
    grade 文件名关键词。返回标准专业名；无法识别返回 None（HTTP 层信任 major_hint）。

    display_name（v1.9）：用户原始文件名。微信上传落盘名是临时 hash 名
    （``wx.uploadFile`` 发的临时文件名），用它永远识别不出专业——所以文件名
    维度的识别必须优先用 display_name，否则「防传错」的专业强校验形同虚设。"""
    if file_type == "plan":
        try:
            from docx import Document
            doc = Document(path)
            for para in doc.paragraphs[:10]:
                m = re.search(r"(.+?)专业培养方案", para.text.strip())
                if m:
                    return canonical_major(m.group(1).strip())
        except Exception:
            pass
        base = os.path.basename(display_name or path)
        if "专业培养方案" in base:
            return canonical_major(base.split("版", 1)[-1].replace("专业培养方案.docx", ""))
        return None
    if file_type == "grade":
        return canonical_major(display_name or os.path.basename(path))
    return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _plan_major(doc, path: str) -> str:
    """专业名：正文标题（"工商管理专业培养方案"），缺失时按文件名兜底
    （"2023版X专业培养方案.docx" → "X"）。"""
    for para in doc.paragraphs:
        t = para.text.strip()
        m = re.search(r"(.+?)专业培养方案", t)
        if m:
            return m.group(1).strip()
    return os.path.basename(path).split("版", 1)[-1].replace("专业培养方案.docx", "")


def _find_plan_table(doc):
    """找课程总表：表头行含"课程编码"（样本在 rows[0]；兼容 rows[1]）。
    返回 (table, hdr_row)，未找到返回 (None, 0)。"""
    for t in doc.tables:
        if len(t.rows) < 3:
            continue
        for ri in (0, 1):
            hdr = [_clean(c.text) for c in t.rows[ri].cells]
            if any("课程编码" in h for h in hdr):
                return t, ri
    return None, 0


def _plan_course_subname(hdr: list[str], cells: list[str], name: str) -> str:
    """课程组名修复：中文课程名称占多列（合并单元格，python-docx 重复表头；
    各专业合并列数不同——工商/工业/会计 2 列，大数据 4 列）。
    首列是组名（如"思想政治理论"），后续列中第一个不同的值是子名
    （如"思想道德与法治"）——学生成绩单记录的是子名，取子名参与匹配
    （2026-08-27 实证全员误报根因）。"""
    name_cols = [i for i, h in enumerate(hdr) if h == "中文课程名称"]
    for i in name_cols[1:]:
        if i < len(cells) and cells[i] and cells[i] != name:
            return cells[i]
    return name


def _plan_course_from_row(cells: list[str], hdr: list[str], col: dict) -> PlanCourse | None:
    """课程总表单行 → PlanCourse；无效行（小计/未知类型/缺编码/0 学分/无学期）
    返回 None。"""
    ctype = cells[0]
    if not ctype or any(k in ctype for k in _SKIP_TYPE_KEYWORDS):
        return None
    if ctype not in PLAN_COURSE_TYPES:
        return None  # 未知类型行（如表头残留）跳过
    def _cell(key):
        i = col.get(key)
        return cells[i] if i is not None and i < len(cells) else ""
    code, name = _cell("课程编码"), _cell("中文课程名称")
    credit_str = _cell("学")
    req, semester, provider = _cell("必修"), _cell("开课学期"), _cell("开课单位")
    if not code or not name:
        return None
    name = _plan_course_subname(hdr, cells, name)
    try:
        credit = float(credit_str)
    except ValueError:
        credit = 0.0
    # 0 学分/无开课学期行（课程组行、四选一体育项目、劳育等）非有效课程行
    if credit <= 0 or not semester:
        return None
    # 必修/选修：单元格可能为 "必修\n15学分" → 取首词
    required = "必修" if "必修" in req else ("选修" if "选修" in req else "必修")
    return PlanCourse(
        plan_id=0,  # 占位：plan_id 由 WarningDB.insert_plan_courses 落库时指定
        course_code=code, course_name=name, credit=credit,
        course_type=ctype, required_flag=required, semester=semester,
        provider=provider,
    )


def _parse_plan_courses(doc, path: str) -> tuple[list[PlanCourse], list[str]]:
    """课程总表 → 课程行 + 未识别学期清单。统一按"表头行 + 首列课程类型"
    容错解析（L4）：各专业 docx 列数/表头合并方式不同（工商 83x16、
    大数据 75x18 等）。"""
    plan_table, hdr_row = _find_plan_table(doc)
    assert plan_table is not None, f"未找到课程总表: {path}"

    hdr = [_clean(c.text) for c in plan_table.rows[hdr_row].cells]
    # 列定位：课程编码/中文课程名称/学分/必修选修/开课学期/开课单位
    col = {key: next((i for i, h in enumerate(hdr) if h.startswith(key) or key in h), None)
           for key in ("课程编码", "中文课程名称", "学", "必修", "开课学期", "开课单位")}

    courses: list[PlanCourse] = []
    bad_semesters: list[str] = []
    for row in plan_table.rows[hdr_row + 1:]:
        cells = [_clean(c.text) for c in row.cells]
        course = _plan_course_from_row(cells, hdr, col)
        if course is None:
            continue
        # L4：学期格式校验（课程行仍保留，异常值计入清单供上游提示）
        if not re.fullmatch(r"\d-\d", course.semester):
            bad_semesters.append(f"{course.course_name}({course.semester})")
        courses.append(course)
    return courses, bad_semesters


def _semester_courses_in_table(t) -> list[PlanSemesterCourse]:
    """单个推荐课表 → 学期课程行。数据自 rows[2:] 起；学期表头跨列合并
    （如"第一学期：1-1"占 3 列）→ 每学期只取首列，避免重复读取；
    合计/总学分/0 学分等非课程行剔除（同课程总表规则）。"""
    hdr0 = [_clean(c.text) for c in t.rows[0].cells]
    sem_cols = {}
    for i, h0 in enumerate(hdr0):
        if _SEM_HEADER_RE.fullmatch(h0) and h0 not in sem_cols.values():
            sem_cols[i] = h0

    sem_courses: list[PlanSemesterCourse] = []
    for row in t.rows[2:]:
        cells = [_clean(c.text) for c in row.cells]
        for i, sem in sem_cols.items():
            sem_code = sem.split("：")[-1].split(":")[-1]
            code, name, credit_str = cells[i], cells[i + 1] if i + 1 < len(cells) else "", \
                cells[i + 2] if i + 2 < len(cells) else ""
            if not code or not name or "合计" in (code + name) or "总学分" in name:
                continue
            try:
                credit = float(credit_str)
            except ValueError:
                credit = 0.0
            if credit <= 0:
                continue
            sem_courses.append(PlanSemesterCourse(
                plan_id=0,  # 占位：同上
                semester=sem_code, course_code=code, course_name=name, credit=credit,
            ))
    return sem_courses


def _parse_semester_courses(doc) -> list[PlanSemesterCourse]:
    """推荐课表：表头为"第X学期：Y-Z" 的表格（Tables 2-5）。"""
    sem_courses: list[PlanSemesterCourse] = []
    for t in doc.tables:
        if len(t.rows) < 3:
            continue
        hdr0 = [_clean(c.text) for c in t.rows[0].cells]
        if not any("学期" in h for h in hdr0):
            continue
        if not any(_SEM_HEADER_RE.fullmatch(h0) for h0 in hdr0):
            continue
        sem_courses.extend(_semester_courses_in_table(t))
    return sem_courses


def _parse_elective_req(doc) -> float | None:
    """Table 0（学分结构要求表）专业选修毕业要求学分——2026-08-31 用户指出的
    口径修正：应修用毕业要求（工商 8/工业 12/大数据 10/会计 15），
    而非 Table 1 逐课程汇总（选修池课程可从中选，非全部必修）。
    未找到/表缺失返回 None。"""
    try:
        t0 = doc.tables[0]
        for row in t0.rows:
            cells = [_clean(c.text) for c in row.cells]
            if any("专业选修" in c for c in cells):
                for i, c in enumerate(cells):
                    if c == "专业选修课程" and i + 1 < len(cells) and cells[i + 1].isdigit():
                        return float(cells[i + 1])
    except Exception:
        pass
    return None


def parse_plan(path: str) -> tuple[dict, list[PlanCourse], list[PlanSemesterCourse], list[str]]:
    """解析培养方案 docx → (元数据, 课程表, 推荐课表, 未识别学期清单)。

    四阶段拆分：_plan_major（专业名）→ _parse_plan_courses（课程总表）→
    _parse_semester_courses（推荐课表）→ _parse_elective_req（毕业要求学分）。
    """
    from docx import Document

    doc = Document(path)
    meta: dict = {"major": _plan_major(doc, path)}
    elective_req = _parse_elective_req(doc)
    if elective_req is not None:
        meta["elective_req"] = elective_req
    courses, bad_semesters = _parse_plan_courses(doc, path)
    sem_courses = _parse_semester_courses(doc)
    return meta, courses, sem_courses, bad_semesters


def _semester_code(semester_label: str, entry_year: int) -> str:
    """学期编码：'2026-2027学年 第一学期' + 入学年 2023 → '4-1'。"""
    m = re.search(r"(\d{4})-(\d{4})学年\s*第([一二三])学期", semester_label)
    if not m:
        return ""
    start_year = int(m.group(1))
    term = {"一": 1, "二": 2, "三": 3}[m.group(3)]
    year_idx = start_year - entry_year + 1
    return f"{year_idx}-{term}"


def _cell(idx: dict, row: tuple, key: str):
    """xlsx 行取列：列缺失或行尾越界 → None。"""
    i = idx.get(key)
    return row[i] if i is not None and i < len(row) else None


def _selection_row(idx: dict, row: tuple) -> dict:
    """选课结果行 → 记录 dict（全字段 str 化；学号去尾部 ".0"，M4 与 roster 同口径）。"""
    def g(key):
        return str(_cell(idx, row, key) or "")
    return {
        "student_id": g("学号").strip().split(".")[0],
        "name": g("姓名"),
        "grade": g("年级"),
        "major": g("专业名称"),
        "course_code": g("课程号").strip(),
        "course_name": g("课程名"),
        "nature": g("课程性质"),
        "category": g("课程类别"),
        "status": g("选课状态"),
        "retake": g("重修重考"),
    }


def parse_selection(path: str) -> tuple[dict, list[dict], str]:
    """解析选课结果 xlsx（无学分列，学分由 Task 9 从培养方案关联）。

    B1：样本 sheet1.xml 的 <dimension ref="A1"/> 元数据错误，read_only=True 只返回 1 行
    ——必须用普通模式加载（文件小，直接扫描）。"""
    import openpyxl
    from collections import Counter

    wb = openpyxl.load_workbook(path)   # 不用 read_only（B1）
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter)
    idx = {h: i for i, h in enumerate(header) if h}

    data = []
    grades: set[str] = set()
    semester_label = ""
    status_counter: Counter = Counter()
    for row in rows_iter:
        if row[0] is None:
            continue
        if not semester_label:
            semester_label = str(_cell(idx, row, "学年学期") or "")
        rec = _selection_row(idx, row)
        grades.add(rec["grade"])
        status_counter[rec["status"]] += 1
        data.append(rec)
    wb.close()

    # 年级 → 入学年：取出现年级（如 2023级），若多届则取最小（M1 校验在 service 层做）
    # 守卫用 any 而非集合非空：行存在但年级列全空时 grades={""}，min() 空生成器会炸
    entry_year = min(int(g.replace("级", "")) for g in grades
                     if g.endswith("级")) if any(g.endswith("级") for g in grades) else 0
    meta = {
        "semester_label": semester_label,
        "semester_code": _semester_code(semester_label, entry_year) if entry_year else "",
        "grades": sorted(grades),
        "entry_year": entry_year,   # v6 Task4：入学年（无年级列时 0，service 侧兜底 2023）
    }
    note = "选课状态分布: " + ", ".join(f"{k}={v}" for k, v in status_counter.items())
    return meta, data, note


def parse_roster(path: str) -> tuple[dict, list[dict]]:
    """解析学籍名单 xls（xlrd 读 .xls）→ (meta, 学生行列表)。

    名单过滤：是否在籍=是 且 是否在校=是（休学/离校/退学自动排除，名单权威）；
    专业代码前缀剥除 + 标准名归一："0824工商管理" → "工商管理"。
    meta = {"grade": 名单年级列众数（如 "2023级"）, "majors": 标准专业分布}。"""
    import xlrd
    from collections import Counter

    wb = xlrd.open_workbook(path)
    sh = wb.sheet_by_index(0)
    if sh.nrows < 2:
        return {}, []
    hdr = {str(sh.cell_value(0, c)).strip(): c for c in range(sh.ncols)}

    def _cell(r: int, key: str) -> str:
        i = hdr.get(key)
        if i is None or i >= sh.ncols:
            return ""
        v = sh.cell_value(r, i)
        # 数字单元格（学号存数值型时）转整数字符串，与 parse_selection 同口径
        if isinstance(v, float) and v == int(v):
            return str(int(v))
        return str(v or "").strip()

    rows: list[dict] = []
    for r in range(1, sh.nrows):
        if not _cell(r, "学号"):
            continue   # 尾部空行
        if _cell(r, "是否在籍") != "是" or _cell(r, "是否在校") != "是":
            continue   # 名单 = 在籍且在校学生（休学等自动排除）
        major = re.sub(r"^\d{4}", "", _cell(r, "专业"))
        rows.append({
            "student_id": _cell(r, "学号"),
            "name": _cell(r, "姓名"),
            "grade": _cell(r, "年级"),
            "major": canonical_major(major) or major,
            "class_name": _cell(r, "班级"),
            "status": _cell(r, "学籍状态"),
        })
    if not rows:
        return {}, []
    grade_cnt = Counter(r["grade"] for r in rows)
    main, _ = grade_cnt.most_common(1)[0]
    meta = {
        "grade": main if re.fullmatch(r"20\d\d级", main) else "",
        "majors": dict(Counter(r["major"] for r in rows).most_common()),
    }
    return meta, rows


_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz()",
)
_ROMAN = {"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V",
          "Ⅵ": "VI", "Ⅶ": "VII", "Ⅷ": "VIII", "Ⅸ": "IX", "Ⅹ": "X"}


def clean_course_name(name: str) -> str:
    """清洗成绩单课程名（§5.1 匹配用）：
    去 ◆▲△◇* 符号与空白；全角→半角；罗马数字→拉丁字母
    （2026-08-27 实证：方案"专业实习Ⅱ"与成绩单"专业实习 I"因字符差异匹配失败）。"""
    s = re.sub(r"[◆▲△◇＊*\s]", "", name or "")
    s = s.translate(_FULLWIDTH)
    return re.sub(r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]", lambda m: _ROMAN[m.group(0)], s)


# 成绩单学期标签（如 "第一学年（2023-2024）第一学期"）；跨组状态延续用。
# 形态容忍（2026-09-02 实证）：休学学生用半角括号+空格 "第一学年 (2021-2022)
# 第二学期 [休学]"；休学复学学生学年序号最长到第六学年 → 全/半角括号与空白
# 均可选、学年一~六；尾部 [休学] 等标注由 search 命中标签主体自然忽略。
_TERM_RE = re.compile(
    r"第([一二三四五六])学年\s*[（(]?\s*\d{4}-\d{4}\s*[）)]?\s*第([一二三])学期",
)
_YEAR_CHARS = "一二三四五六"


def _term_ordinal(term_label: str) -> tuple[int, int] | None:
    """'第三学年（2025-2026）第二学期' → (3, 2)；无法解析返回 None。

    按"第X学年"序号与"第Y学期"换算（不校验年份——休学/复学/转专业页面的
    跨届年份如 2021 级生"第五学年（2025-2026）"以序号为准）。"""
    m = _TERM_RE.search(term_label or "")
    if not m:
        return None
    return (_YEAR_CHARS.index(m.group(1)) + 1, "一二三".index(m.group(2)) + 1)


def term_label_to_sem(term_label: str) -> str:
    """'第三学年（2025-2026）第二学期' → '3-2'；无法解析返回 ''。

    学年第 N 学年 → N（一~六）；第Y学期：一二三→1/2/3（三=小学期）。"""
    o = _term_ordinal(term_label)
    return f"{o[0]}-{o[1]}" if o else ""


def grade_to_pass(raw: str) -> tuple[int, str]:
    """§5.0 成绩换算表 → (pass_flag, 成绩原文)。0=挂科, 1=及格。

    换算（全角→半角等）仅用于判定 pass_flag，第二元素原样返回，
    保留成绩单原始书写形态（如 "B＋"）。"""
    v = (raw or "").strip()
    if not v or v in ("缺考",):
        return 0, v or "缺考"
    if re.fullmatch(r"\d+(\.\d+)?", v):
        return (1 if float(v) >= 60 else 0), v
    upper = v.upper().replace("＋", "+").replace("－", "-")
    if upper in ("A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-"):
        return 1, v
    if upper in ("D+", "D", "D-", "F"):
        return 0, v
    if upper in ("P", "PASS"):
        return 1, v
    if v in ("及格", "优秀", "良好", "中等", "合格"):
        return 1, v
    if v in ("不及格", "不合格"):
        return 0, v
    return 1, v  # 未知形态按及格（报告数据质量列出，不误报挂科）


_GRADE_BAND = {"A+":95,"A":90,"A-":87,"B+":84,"B":80,"B-":77,"C+":74,"C":70,
               "C-":67,"D+":64,"D":60,"D-":57,"F":50,"优秀":95,"良好":85,
               "中等":75,"及格":65,"不合格":50,"不及格":50}

def grade_band(raw: str) -> tuple[int, float]:
    """成绩原文 → 排序键 (档位, 值)：0=无有效成绩(空/缺考)，1=数值，2=字母/中文。"""
    v = (raw or "").strip().upper().replace("＋", "+").replace("－", "-")
    if not v or v in ("缺考",):
        return (0, 0.0)
    if re.fullmatch(r"\d+(\.\d+)?", v):
        return (1, float(v))
    return (2, float(_GRADE_BAND.get(v, 85)))

def representative_by_course(grades: list[Grade]) -> dict[str, Grade]:
    """同课(clean 名)多行取最高分代表行（0 档=空/缺考；并列取文档后到）。"""
    out = {}
    for g in grades:
        key = g.course_name_clean or g.course_name
        prev = out.get(key)
        if prev is None or grade_band(g.grade_raw) >= grade_band(prev.grade_raw):
            out[key] = g   # 并列也覆盖 → 后到者胜
    return out


def extract_docx_images(path: str) -> list[tuple[bytes, str]]:
    """按文档内顺序提取 docx 内嵌图片（学号图）。返回 [(字节, 文件名)]。

    L5：同时匹配 .png/.jpe?g（生产教务 docx 可能内嵌 jpg）；返回带文件名供告警定位。"""
    import zipfile
    import re as _re

    with zipfile.ZipFile(path) as z:
        names = sorted(
            (n for n in z.namelist()
             if _re.fullmatch(r"word/media/image\d+\.(png|jpe?g)", n)),
            key=lambda n: int(_re.search(r"image(\d+)", n).group(1)),
        )
        return [(z.read(n), n) for n in names]


_MARKER_MAP = {"▲": "▲重修", "△": "△补考", "◇": "◇核心课", "◆": "◆选修课"}

# 学号正则：允许字母前缀（交流生学号如 JL20249012；2026-08-27 实证纯数字正则漏识）
_SID_RE = re.compile(r"学号[:：]?\s*([A-Za-z]{0,4}\d{6,12})")

# OCR 补跑轮间间隔（秒）；测试可 monkeypatch 为 0
_OCR_RETRY_SLEEP = 5

def _grade_table_groups(hdr: list[str]) -> list[tuple[int, int, int | None, int]]:
    """表头行 → 组区间 [(组起点, 区间终点, 学分列, 成绩列)]。

    成绩/学分列按组边界探测（docx 合并单元格使 python-docx 重复文本，固定
    偏移不可靠——组内第一个含"成绩"/"学"的列即该组成绩列/学分列）；组内无
    成绩列（表尾残列）→ 该组不解析。"""
    group_starts = [i for i, h in enumerate(hdr) if "课程" in h]
    groups: list[tuple[int, int, int | None, int]] = []
    for gi in group_starts:
        end = next((g for g in group_starts if g > gi), len(hdr))
        credit_col = next((i for i in range(gi, end) if "学" in hdr[i]), None)
        grade_col = next((i for i in range(gi, end) if "成绩" in hdr[i]), None)
        if grade_col is None:
            continue
        groups.append((gi, end, credit_col, grade_col))
    return groups


def _advance_term(cells: list[str], gi: int, end: int,
                  current_term: str, last_sem: tuple[int, int] | None,
                  seq_sink: list | None, row_idx: int) \
        -> tuple[str, tuple[int, int] | None]:
    """本组区间扫描学期节 → 推进全局单流当前学期。

    节文本展开重复在本组各格，取首个命中；同行他组的节在区间外互不串扰。
    节序回退（如 2-2 后出 2-1，独立流布局变体嫌疑）→ 追加 seq_sink 由调用
    方报备人工核对，不抛错。"""
    label = ""
    for ci in range(gi, min(end, len(cells))):
        if _TERM_RE.search(cells[ci]):
            label = cells[ci]
            break
    if not label:
        return current_term, last_sem
    sem = _term_ordinal(label)
    if last_sem is not None and sem < last_sem and seq_sink is not None:
        seq_sink.append((gi, row_idx, current_term, label))
    return label, sem


def _course_marker(course: str) -> str:
    """课程名内标记符（◆/◇ 等）→ 语义标记（后命中优先，与原遍历一致）。"""
    marker = ""
    for k, v in _MARKER_MAP.items():
        if k in course:
            marker = v
    return marker


def parse_grade_table(table, student_id: str, name: str = "",
                      orphan_sink: list | None = None,
                      seq_sink: list | None = None) -> list[Grade]:
    """解析单份成绩单表格 → 课程成绩列表（学号/姓名由调用方给定，免 OCR 重跑）。

    模型：单一时间流分段排版（2026-09-02 用户确认，取代上一版"组级独立学期跟踪
    + 孤儿课留空"——孤儿课其实是跨组延续的学期块）。成绩单每生一张表（行 0-31 =
    视觉接续），4 个课程列组 = 同一时间流的连续片段：阅读顺序 = 组 1（行 0-31
    全列）→ 组 2 → 组 3 → 组 4：

    - 表头含"课程"的网格列 = 组起点（如 [0,3,6,11]，组宽可变 3/5/6 格），
      组网格区间 = [起点, 下一起点)；组分块纵向排期，但**学期状态全局单流
      共享**——组末尾的学期块在组内行空间用尽处截断，由下一组顶部直接接续
      课程（无节），这些课继承上一组末尾的学期（"孤儿课"概念消失）；
    - 学期节是组内横向合并单元格（跨 grid 0-2 / 6-10），python-docx 展开后
      同文本重复在 span 的每个 grid 位；按组区间扫描，命中节即推进全局
      current_term（同行不同组的节各归其组区间扫描，互不串扰）；
    - 节序校验：沿单流记录节序 (学年序号, 学期序号)，出现**回退**（如 2-2 后
      出 2-1）说明该表可能真是独立流布局变体 → 追加 (组起点, 行号, 前一节,
      回退节) 到 seq_sink（None 则不收集），不抛错，由调用方报备人工核对；
    - 课程行：课程名只取本组课程格 cells[gi]，学期 = 全局 current_term；
    - 真孤儿（整表首个节出现前已有的课程，仅可能出现在首组顶部无节时）：
      term_label 留空，逐条 (course_name, credit_s, grade_s) 追加到
      orphan_sink（None 则不收集），由调用方报备。
    """
    # 成绩/学分列按表头行组边界探测（组内第一个含"成绩"/"学"的列）
    hdr = [c.text.strip() for c in table.rows[1].cells]
    groups = _grade_table_groups(hdr)
    current_term = ""   # 全局单流当前学期标签
    last_sem: tuple[int, int] | None = None   # 单流最近节序（校验回退）
    grades: list[Grade] = []
    for gi, end, credit_col, grade_col in groups:  # 阅读顺序 = 组序 → 组内行序
        for ri, row in enumerate(table.rows[2:], start=2):
            cells = [c.text.strip() for c in row.cells]
            # 1) 学期节推进：本组区间内任一格命中节文本 → 推进全局学期
            current_term, last_sem = _advance_term(
                cells, gi, end, current_term, last_sem, seq_sink, ri)
            # 2) 课程行：课程格 cells[gi] 继承全局当前学期；节文本行不产生课程
            course = cells[gi] if gi < len(cells) else ""
            if not course or course in ("课程",) or _TERM_RE.search(course):
                continue
            credit_s = cells[credit_col] if credit_col is not None and credit_col < len(cells) else ""
            grade_s = cells[grade_col] if grade_col < len(cells) else ""
            marker = _course_marker(course)
            pass_flag, _norm = grade_to_pass(grade_s)
            try:
                credit = float(credit_s)
            except ValueError:
                credit = 0.0
            if not current_term and orphan_sink is not None:
                orphan_sink.append((course, credit_s, grade_s))
            grades.append(Grade(
                student_id=student_id, student_name=name, term_label=current_term,
                course_name=course, course_name_clean=clean_course_name(course),
                credit=credit, grade_raw=grade_s, pass_flag=pass_flag,
                marker=marker,
            ))
    return grades


_SID_PROMPT = (
    "图片中是学生成绩单顶部的学号与姓名区域。"
    "请严格只输出两行：学号：<数字> 姓名：<姓名>。若无法识别输出：无法识别"
)


def _ocr_student_id(img_bytes: bytes, vl_func) -> tuple[str, str]:
    """单张学号图片 → (学号, 姓名)。

    OCR 多策略保障（2026-08-27 全链路实证，目标 100% 不漏）：
    策略1 放大4x+增强 → 策略2 原图 → 策略3 3x+二值化；每策略独立 VL 调用，
    任一次命中即成功；全部失败后整轮补跑（最多 3 轮，间隔 _OCR_RETRY_SLEEP）。
    异常一律返回 ("", "")，由调用方计入 failed_idx。
    """
    from io import BytesIO
    from PIL import Image, ImageOps
    import tempfile

    student_id, name = "", ""
    tmp_path = None
    try:
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        text = ""
        for round_no in range(3):
            if round_no:
                time.sleep(_OCR_RETRY_SLEEP)
            strategies = [
                (ImageOps.autocontrast(img).resize(
                    (img.width * 4, img.height * 4), Image.LANCZOS), False),
                (img, False),
                (img.convert("L").point(lambda x: 255 if x > 140 else 0)
                 .convert("RGB").resize((img.width * 3, img.height * 3), Image.LANCZOS), False),
            ]
            for probe, _ in strategies:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    probe.save(tmp, format="PNG")
                    tmp_path = tmp.name
                text = vl_func(tmp_path) or ""
                if tmp_path:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp_path)
                    tmp_path = None
                if "学号" in text or re.search(_SID_RE, text):
                    break
            if "学号" in text or re.search(_SID_RE, text):
                break
        m = re.search(_SID_RE, text)
        if m:
            student_id = m.group(1)
        m2 = re.search(r"姓名[:：]?\s*([一-鿿·]{1,20})", text)
        if m2:
            name = m2.group(1)
    except Exception:
        student_id, name = "", ""
    finally:   # I7：OCR 抛异常也确保临时 PNG 清理（小资源泄漏）
        if tmp_path:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
    return student_id, name


def _ocr_max_workers() -> int:
    """OCR 并发度（环境变量 OCR_MAX_WORKERS，默认 8）。"""
    try:
        return max(1, int(os.environ.get("OCR_MAX_WORKERS", "8")))
    except (TypeError, ValueError):
        return 8


def _ocr_prefetch(ocr_inputs: list, vl_func) -> list[tuple[str, str]]:
    """学号 OCR 并发预取 → 每表 (学号, 姓名)（无图/失败为空串）。

    每份成绩单一张学号图；逐张串行时 40+ 学生要数分钟。OCR 是 HTTP 调 VL 的
    IO 密集操作，vLLM 侧会连续批处理并发请求，故并发收益接近线性。表格解析仍
    串行（纯 CPU、无 IO）；_OP_LOCK 的文件级串行入库语义不变，选择检查/删除
    不会读到半入库数据。"""
    results: list[tuple[str, str]] = [("", "") for _ in range(len(ocr_inputs))]
    todo = [ti for ti, blob in enumerate(ocr_inputs) if blob]
    if not todo:
        return results
    from concurrent.futures import ThreadPoolExecutor, as_completed
    workers = min(_ocr_max_workers(), len(todo))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_ocr_student_id, ocr_inputs[ti], vl_func): ti for ti in todo}
        for fut in as_completed(futures):
            ti = futures[fut]
            try:
                results[ti] = fut.result()
            except Exception:
                results[ti] = ("", "")
    return results


def _report_seq_anomalies(seq_anomalies_by_table) -> None:
    """单流节序回退报备（疑似独立流布局变体，人工核对，不中断解析）。"""
    if not seq_anomalies_by_table:
        return
    logger.warning("节序异常：%d 份成绩单的组间/组内节序出现回退"
                   "（单流节序须单调递增，回退可能属独立流布局变体），报备人工核对：",
                   len(seq_anomalies_by_table))
    for ti, entries in seq_anomalies_by_table:
        for gi, ri, prev, regress in entries:
            logger.warning("  表#%s：组起点 %s 行 R%s——前一节 [%s] 之后回退到 "
                           "[%s]（term 按回退节继续解析，归属待人工裁决）",
                           ti, gi, ri, prev, regress)


def _report_orphans(orphans_by_table) -> None:
    """孤儿课报备（整表首个学期节之前的课程，term_label 留空，人工核对）。"""
    if not orphans_by_table:
        return
    total = sum(len(s) for _, s in orphans_by_table)
    logger.warning("孤儿课：%d 门课程出现在整表首个学期节之前（无节可继承），"
                   "涉及 %d 份成绩单；term_label 已留空，报备人工核对：",
                   total, len(orphans_by_table))
    for ti, sink in orphans_by_table:
        names = "、".join(course for course, _credit, _grade in sink)
        logger.warning("  表#%s（%d 门）：%s", ti, len(sink), names)


def parse_grades(path: str, vl_func=None) -> tuple[list[tuple[str, str, list[Grade]]], list[int]]:
    """解析成绩单 docx → (学生列表, OCR失败索引)。

    - 表格：每个 table = 一份成绩单（课程/学分/成绩 四列组 × 学期行）
    - 学号：表格上方内嵌图片，按文档内顺序与表格一一对应（§2 关联规则2）
    - vl_func(path_to_png) -> str，缺省复用 knowledge_base 的 VL 模型
    """
    from docx import Document

    if vl_func is None:
        from knowledge_base.ingestion.parsers import parse_with_vl
        # 成绩单学号提取必须用专用提示词（通用描述提示词下 qwen3-vl 会省略学号，
        # 导致正则匹配失败误判 OCR 失败——2026-08-27 实证根因）
        vl_func = lambda p: parse_with_vl(p, prompt=_SID_PROMPT)

    doc = Document(path)
    images = extract_docx_images(path)
    # L5：图片数 ≠ 表格数时告警（页脚细条图等干扰会被 OCR 空结果自然过滤，但仍提示）
    if len(images) != len(doc.tables):
        logger.warning("%s: 图片 %d 张 ≠ 成绩单 %d 份（图片数/表格数不一致）",
                       os.path.basename(path), len(images), len(doc.tables))
    students: list[tuple[str, str, list[Grade]]] = []
    failed_idx: list[int] = []
    orphans_by_table: list[tuple[int, list[tuple[str, str, str]]]] = []
    # 节序异常报备：单流节序回退（组起点, 行号, 前一节, 回退节）——疑似独立流变体
    seq_anomalies_by_table: list[tuple[int, list[tuple[int, int, str, str]]]] = []

    n_tables = len(doc.tables)
    ocr_inputs = [images[ti][0] if ti < len(images) else None for ti in range(n_tables)]
    ocr_results = _ocr_prefetch(ocr_inputs, vl_func)

    for ti, table in enumerate(doc.tables):
        # 1) 学号取自上面的并发 OCR 预取（图片缺失 / OCR 失败 → 计入 failed_idx）
        student_id, name = ocr_results[ti]
        if not student_id:
            failed_idx.append(ti)
            continue

        # 2) 解析成绩单表格（单流拼接模型；节序异常/真孤儿集中在函数尾报备）
        orphan_sink: list[tuple[str, str, str]] = []
        seq_sink: list[tuple[int, int, str, str]] = []
        grades = parse_grade_table(table, student_id, name, orphan_sink=orphan_sink,
                                   seq_sink=seq_sink)
        if orphan_sink:
            orphans_by_table.append((ti, orphan_sink))
        if seq_sink:
            seq_anomalies_by_table.append((ti, seq_sink))
        students.append((student_id, name, grades))

    _report_seq_anomalies(seq_anomalies_by_table)
    _report_orphans(orphans_by_table)
    return students, failed_idx
