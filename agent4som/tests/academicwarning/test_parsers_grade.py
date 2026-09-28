"""成绩单 docx 解析测试。OCR 用注入的 fake 函数（不依赖 VL 服务）。

真实样本与金标准值（课程名/成绩）放在 academicwarning/docs/（gitignored，仅
开发机集成测试，见 docs/02-features/ 测试数据分层设计）：样本缺失时 skip。
样本路径无真实学号/姓名——测试内一律用合成标识（9 开头学号 + 学生X）。
"""
import json
import os
import pytest

from academicwarning.parsers import (parse_grades, parse_grade_table,
                                     grade_to_pass, clean_course_name,
                                     term_label_to_sem, representative_by_course)

PATH = "academicwarning/docs/2023级工商管理成绩单.docx"
GOLDEN_PATH = "academicwarning/docs/grade_golden.json"

pytestmark = pytest.mark.samples


def _golden():
    with open(GOLDEN_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _require_samples():
    if not (os.path.isfile(PATH) and os.path.isfile(GOLDEN_PATH)):
        pytest.skip("样本或金标准缺失")


def _fake_vl(path: str) -> str:
    return "学号：9000000040\n姓名：学生零"


def test_parse_grades_with_fake_vl():
    _require_samples()
    g = _golden()
    students, failed_idx = parse_grades(PATH, vl_func=_fake_vl)
    assert len(students) == g["n_tables"]
    assert failed_idx == []
    sid, name, grades = students[0]
    assert sid == "9000000040" and name == "学生零"
    assert len(grades) > g["first_table"]["min_courses"]
    for gr in grades:
        assert gr.pass_flag in (0, 1)
        assert gr.credit >= 0
    # 真实值核对（评审 I2）：样本 sheet1 人工核对的成绩配对——列组错位会丢失/读错这些值
    pairs = {(gr.course_name_clean, gr.grade_raw) for gr in grades}
    for course, raw in g["first_table"]["probe_pairs"]:
        assert (course, raw) in pairs, f"成绩错位: 未找到 {course}={raw}"
    # 单流拼接模型（2026-09-02）：全部课程继承全局单流学期，term 100% 非空
    assert all(gr.term_label for gr in grades), "单流拼接后不应有孤儿课"


def test_ocr_failure_reported(monkeypatch):
    _require_samples()
    import academicwarning.parsers as ap
    monkeypatch.setattr(ap, "_OCR_RETRY_SLEEP", 0)  # 补跑间隔置 0，避免测试超时
    def bad_vl(path: str) -> str:
        return ""
    students, failed_idx = parse_grades(PATH, vl_func=bad_vl)
    # 全部 OCR 失败 → 全部跳过（failed_idx = 失败表的下标序列）
    assert failed_idx == list(range(_golden()["n_tables"]))
    assert students == []


def _grade_table(idx):
    from docx import Document
    return Document(PATH).tables[idx]


def _one(grades, name):
    """按课名取唯一成绩行（缺失/重复即断言失败）。"""
    hit = [g for g in grades if g.course_name == name]
    assert len(hit) == 1, f"课程 {name} 缺失或重复"
    return hit[0]


def _parse_table_and_drains(idx, sid, name):
    """金标准共通：解析 #idx 表，断言无孤儿（sink）且节序单调（seq）。"""
    sink, seq = [], []
    grades = parse_grade_table(_grade_table(idx), sid, name,
                               orphan_sink=sink, seq_sink=seq)
    assert sink == [] and seq == []
    return grades


def _assert_block_3_2(grades, exp):
    """表 #16 组 3（grid 6-10）= 3-2 块，成绩/学分真值。"""
    for name, raw in exp["courses"]:
        hit = _one(grades, name)
        assert hit.grade_raw == raw
        assert hit.term_label == exp["term"], name
        assert term_label_to_sem(hit.term_label) == "3-2"
    guide = next(g for g in grades if exp["guide_course_keyword"] in g.course_name)
    assert guide.credit == exp["guide_credit"] and guide.grade_raw == "B-"


def _assert_block_2_1(grades, exp):
    """表 #16 2-1 块 = 组1 末尾 4 门 + 组2 顶部 9 门 = 13 门（跨组延续，孤儿课归位）。"""
    two1 = [g for g in grades if g.term_label == exp["term"]]
    assert len(two1) == len(exp["names"]), (
        f"2-1 应 {len(exp['names'])} 门，实得 {len(two1)}")
    assert [g.course_name for g in two1] == exp["names"]
    assert term_label_to_sem(two1[0].term_label) == "2-1"


def test_parse_grade_table_single_stream_golden_16():
    """表 #16 金标准（单流拼接模型，2026-09-02 用户确认）。

    页面真值：组 2 顶部 9 门（此前被当"孤儿课"留空）= 组 1 末尾 2-1 块的跨组
    延续 → 2-1 共 13 门；组 3（grid 6-10）= 3-2 共 5 门。全部课程 term 非空，
    无孤儿、无节序回退。期望值全部来自 grade_golden.json（真实数据不出开发机）。"""
    _require_samples()
    exp = _golden()["tables"]["16"]
    grades = _parse_table_and_drains(16, "9000000016", "学生一六")
    assert len(grades) == exp["count"]
    _assert_block_3_2(grades, exp["block_3_2"])
    _assert_block_2_1(grades, exp["block_2_1"])
    # term 分布（单流全表）锁定
    from collections import Counter
    dist = Counter(term_label_to_sem(g.term_label) for g in grades)
    assert dist == Counter(exp["term_dist"])
    # 同行多节互不串扰：R2 行组1"1-1"节与组3"3-2"节各归其组区间
    lin = _one(grades, exp["multi_section_probe"]["name"])
    assert lin.term_label == exp["multi_section_probe"]["term"]


def _assert_hits_with_term(grades, names, term_label):
    """一组课程应全部落在指定 term。"""
    for name in names:
        hit = _one(grades, name)
        assert hit.term_label == term_label, name


def _sem_sequence(grades):
    """按表序提取出现过的学期序号（相邻去重）。"""
    seen = []
    for g in grades:
        o = term_label_to_sem(g.term_label)
        if not seen or o != seen[-1]:
            seen.append(o)
    return seen


def test_parse_grade_table_resume_student_golden_18():
    """表 #18（2021 级休学复学）单流金标准：孤儿课全部归位 + [休学] 不切学期。

    - 组 1 末尾 3-1 块延续到组 2 顶部 6 门（旧版孤儿中的组 2 部分）→ 3-1；
      组 2 末尾 4-2 延续到组 3 顶部 4 门 → 4-2；
    - R13 节 "第一学年 (2021-2022) 第二学期 [休学]"（半角括号 + [休学] 标注）
      是正常节行：不产生课程、其后的课程继承 1-2——休学标注不影响单流学期推进；
    - 节序按"第X学年"序号单调推进 1-1→1-2→2-2→3-1→…→5-2（年份与 2023 级
      文件不一致，以序号为准），无节序回退报备；
    - 2026-09-26 新样本导出：1-1 新增军训成绩（72 → 73 门，含 term_dist）。"""
    _require_samples()
    exp = _golden()["tables"]["18"]
    grades = _parse_table_and_drains(18, "9000000018", "学生一八")
    assert len(grades) == exp["count"]
    assert all(g.term_label for g in grades)
    for block in exp["blocks"]:
        _assert_hits_with_term(grades, block["names"], block["term"])
    assert _sem_sequence(grades) == exp["sem_seq"]
    from collections import Counter
    dist = Counter(term_label_to_sem(g.term_label) for g in grades)
    assert dist == Counter(exp["term_dist"])


def test_parse_grade_table_seq_regression_reported_not_raised():
    """节序校验：单流节序回退（2-2 后出 1-2）→ seq_sink 报备且不抛错，后续课
    程按回退节继续解析（可能是独立流布局变体，报备人工核对而非中断）。"""
    class _Cell:
        def __init__(self, text=""):
            self.text = text
    class _Row:
        def __init__(self, texts):
            self.cells = [_Cell(t) for t in texts]
    class _StubTable:
        def __init__(self, rows):
            self.rows = rows
    T1 = "第一学年（2023-2024）第一学期"
    T2 = "第二学年（2024-2025）第二学期"
    TR = "第一学年（2023-2024）第二学期"
    table = _StubTable([
        _Row([]),                                   # R0 空行
        _Row(["课程", "学分", "成绩"]),              # R1 表头
        _Row([T1, "", ""]),                          # R2 1-1 节
        _Row(["高数", "4.0", "80"]),                 # R3
        _Row([T2, "", ""]),                          # R4 2-2 节
        _Row(["大物", "3.0", "70"]),                 # R5
        _Row([TR, "", ""]),                          # R6 回退节 1-2
        _Row(["课后课", "1.0", "90"]),               # R7
    ])
    sink, seq = [], []
    grades = parse_grade_table(table, "S1", "", orphan_sink=sink, seq_sink=seq)
    assert [g.course_name for g in grades] == ["高数", "大物", "课后课"]
    assert [g.term_label for g in grades] == [T1, T2, TR]
    assert len(seq) == 1
    gi, ri, prev, regress = seq[0]
    assert gi == 0 and ri == 6 and prev == T2 and regress == TR


def test_parse_grade_table_group_order_and_residual_drop():
    """组序→组内行序 阅读模型（重构回归）：组 2 顶部课程（先扫完组 1 全列）
    继承组 1 末尾学期（单流跨组接续，无节）；无成绩列的表尾残组整组剔除。"""
    class _Cell:
        def __init__(self, text=""):
            self.text = text
    class _Row:
        def __init__(self, texts):
            self.cells = [_Cell(t) for t in texts]
    class _StubTable:
        def __init__(self, rows):
            self.rows = rows
    T1 = "第一学年（2023-2024）第一学期"
    table = _StubTable([
        _Row([]),   # R0 空行
        # R1 表头：组1（0-2）/ 组2（3-5）/ 残组（6-7，无成绩列）
        _Row(["课程", "学分", "成绩", "课程", "学分", "成绩", "课程", "备注"]),
        _Row([T1, "", "", "", "", "", "", ""]),          # R2 组1 的 1-1 节
        _Row(["高数", "4.0", "80", "商务英语", "2.0", "77", "残组课", "x"]),
    ])
    sink = []
    grades = parse_grade_table(table, "S1", "", orphan_sink=sink)
    # 阅读顺序：组 1 全列（高数）→ 组 2（商务英语继承组 1 末尾学期 T1）
    assert [g.course_name for g in grades] == ["高数", "商务英语"]
    assert [g.term_label for g in grades] == [T1, T1]
    assert grades[1].credit == 2.0 and grades[1].grade_raw == "77"
    assert "残组课" not in [g.course_name for g in grades]   # 残组整组剔除
    assert sink == []   # 节先于课程出现 → 无真孤儿


def test_parse_grade_table_wide_groups():
    """宽列流布局（组宽 5：grid 起点 [0,5,10,15]，休学学生表 #5）节归属正确。"""
    _require_samples()
    exp = _golden()["tables"]["5"]
    grades = parse_grade_table(_grade_table(5), "9000000005", "学生零五")
    assert len(grades) == exp["count"]
    assert all(g.term_label for g in grades)
    assert {term_label_to_sem(g.term_label) for g in grades} == set(exp["sems"])


def test_grade_to_pass():
    assert grade_to_pass("80") == (1, "80")
    assert grade_to_pass("59") == (0, "59")
    assert grade_to_pass("0") == (0, "0")
    assert grade_to_pass("D") == (0, "D")
    assert grade_to_pass("D-") == (0, "D-")
    assert grade_to_pass("C-") == (1, "C-")
    assert grade_to_pass("B＋") == (1, "B＋")
    assert grade_to_pass("及格") == (1, "及格")
    assert grade_to_pass("P") == (1, "P")


def test_clean_course_name():
    assert clean_course_name("走向富足：通过科技改变人类未来◆") == "走向富足：通过科技改变人类未来"
    assert clean_course_name("线性代数与解析几何II▲") == "线性代数与解析几何II"
    assert clean_course_name("体育-1　") == "体育-1"


def test_clean_course_name_normalization():
    """字符归一化：罗马数字→拉丁字母、全角→半角（专业实习匹配失败回归）。"""
    assert clean_course_name("专业实习Ⅱ") == "专业实习II"
    assert clean_course_name("专业实习 I") == "专业实习I"
    assert clean_course_name("高等数学Ｉ-2") == "高等数学I-2"
    assert clean_course_name("走向富足：通过科技改变人类未来◆") == "走向富足：通过科技改变人类未来"


def test_clean_course_name_bracket_normalization():
    """括号归一化：方案全角（ACCA）与成绩单半角 (ACCA) 必须一致（SBR 误报回归）。"""
    assert clean_course_name("SBR战略商业报告（ACCA）") == "SBR战略商业报告(ACCA)"
    assert clean_course_name("SBR 战略商业报告 (ACCA)") == "SBR战略商业报告(ACCA)"


def test_sid_regex_allows_letter_prefix():
    """学号正则须支持字母前缀（交流生 JL 学号；纯数字正则漏识回归）。测试用
    合成样例（真实学号/姓名不入库）。"""
    from academicwarning.parsers import _SID_RE
    m = _SID_RE.search("学号：JL90000001 姓名：学生甲")
    assert m and m.group(1) == "JL90000001"
    m2 = _SID_RE.search("学号：9000000040 姓名：学生乙")
    assert m2 and m2.group(1) == "9000000040"


from academicwarning.models import Grade

def _g(name, raw, credit=2.0):
    from academicwarning.parsers import grade_to_pass   # 让 pass_flag 与原文自洽（同入库口径）
    return Grade(student_id="S1", course_name=name, course_name_clean=name,
                 credit=credit, grade_raw=raw, pass_flag=grade_to_pass(raw)[0])

def test_representative_numeric_highest_wins():
    rows = [_g("高等数学", "45"), _g("高等数学", "72")]
    rep = representative_by_course(rows)["高等数学"]
    assert rep.grade_raw == "72" and rep.pass_flag == 1

def test_representative_failed_absent_zero_band():
    rows = [_g("体育", "缺考"), _g("体育", "")]  # 全缺考/空 → 0 档 → 代表=后到
    rep = representative_by_course(rows)["体育"]
    assert rep.pass_flag == 0

def test_representative_letter_band_and_tie_later():
    rows = [_g("英语", "D"), _g("英语", "C-")]
    assert representative_by_course(rows)["英语"].grade_raw == "C-"
    rows2 = [_g("英语", "B"), _g("英语", "B")]
    assert representative_by_course(rows2)["英语"].grade_raw == "B"  # 并列取后到
