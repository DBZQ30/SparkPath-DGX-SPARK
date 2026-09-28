"""豁免测试（v7）：db 层表/读写 + service 层 run 装配（Task 3）+ api 层端点
（Task 4：waiver POST/GET/DELETE、student 详情分组、selection-check 行 details）。

（Task 1 建表先行；Task 2 建模后 SelectionCheckRow.details 为正规字段——
写侧 r.details 直落、读侧 ctor 传参。Task 3：_apply_waivers 在 Prep 后装配——
kind=course 虚拟行注入 / kind=credit 类别叠加 + L1/N1/L5 守卫，
全链路经 run_selection_check 验证。）"""
import json
import pytest
import sqlite3

from academicwarning.db import WarningDB
from academicwarning.models import (SourceFile, SelectionCheckRow, Selection,
                                    Grade, PlanCourse)
from academicwarning.parsers import clean_course_name, grade_to_pass


def _miss_codes(details):
    """从新契约 details 取全部缺修条目（cats[].missing ∪ other_missing）。

    details 可为 JSON 字符串（DB 行 r.details）或已解析的 dict（api 出参）。"""
    d = json.loads(details) if isinstance(details, str) else (details or {})
    items = [m for c in d.get("cats", []) for m in (c.get("missing") or [])]
    items += d.get("other_missing", []) or []
    return items


def _db(tmp_path):
    db = WarningDB(str(tmp_path / "t.db"))
    db.init_schema()
    return db


def _waiver(**kw):
    w = {"grade": "2023级", "student_id": "S1", "kind": "course",
         "course_code": "", "course_name": "", "grade_source": "",
         "category": "", "credit": 0.0, "note": "", "created_by": "admin",
         "created_at": "2026-09-04 10:00:00"}
    w.update(kw)
    return w


def test_waiver_and_details_schema(tmp_path):
    """建表/幂等：waiver 表列齐；selection_check 有 details 列且默认 '{}'；
    user_version=7；init_schema 再跑不报错（表已存在 / ALTER 列已存在静默）。"""
    db = _db(tmp_path)
    wcols = {r[1] for r in db.conn.execute("PRAGMA table_info(waiver)")}
    for c in ("id", "grade", "student_id", "kind", "course_code", "course_name",
              "grade_source", "category", "credit", "note", "created_by", "created_at"):
        assert c in wcols
    scol = {r[1]: r[4] for r in db.conn.execute("PRAGMA table_info(selection_check)")}
    assert scol["details"] == "'{}'"
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 7
    db.init_schema()
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 7


def test_add_list_waivers(tmp_path):
    """add/list 往返：字段完整落库；kind 两类均可；grade 过滤；缺省键兜底。"""
    db = _db(tmp_path)
    id1 = db.add_waiver(_waiver(course_code="COMP201127",
                                course_name="大学计算机-算法编程",
                                grade_source="大学计算机IV", note="平替"))
    id2 = db.add_waiver(_waiver(student_id="S2", kind="credit",
                                course_name="旧版国际商务", category="专业选修课程",
                                credit=2.5))
    rows = db.list_waivers()
    assert [w["id"] for w in rows] == [id1, id2]
    w1 = rows[0]
    assert (w1["grade"], w1["student_id"], w1["kind"]) == ("2023级", "S1", "course")
    assert w1["course_code"] == "COMP201127" and w1["course_name"] == "大学计算机-算法编程"
    assert w1["grade_source"] == "大学计算机IV" and w1["credit"] == 0.0
    assert w1["note"] == "平替" and w1["created_by"] == "admin"
    w2 = rows[1]
    assert w2["kind"] == "credit" and w2["category"] == "专业选修课程"
    assert w2["credit"] == 2.5 and w2["course_name"] == "旧版国际商务"
    # grade 过滤：无该年级 → []
    assert db.list_waivers("2023级") == rows
    assert db.list_waivers("2026级") == []
    # 未提供的可选键落库为空串/0.0（fixture 已含全部键，直传最小 dict 验证兜底）
    id3 = db.add_waiver({"grade": "2023级", "student_id": "S3", "kind": "course"})
    raw = db.conn.execute("SELECT course_code, grade_source, category, credit,"
                          " note, created_by, created_at FROM waiver WHERE id=?",
                          (id3,)).fetchone()
    assert raw == ("", "", "", 0.0, "", "", "")


def test_delete_waiver(tmp_path):
    """撤销：按 id 物理删除；不存在 id 静默无操作。"""
    db = _db(tmp_path)
    wid = db.add_waiver(_waiver(course_name="大学计算机-算法编程"))
    assert len(db.list_waivers()) == 1
    db.delete_waiver(wid)
    assert db.list_waivers() == []
    db.delete_waiver(wid)   # 重复删不报错


def test_waiver_credit_dup_unique(tmp_path):
    """部分唯一索引（P1 + credit 先例）：kind='credit' 同 (grade, sid,
    course_name) 重复 → IntegrityError；kind='course' 同 (grade, sid,
    course_code) 重复 → IntegrityError（idx_waiver_course_dup）；不同年级/
    学生/课名/课程码互不冲突；跨 kind 同课名不互斥（credit 索引按 kind 过滤）。"""
    db = _db(tmp_path)
    db.add_waiver(_waiver(kind="credit", course_name="旧版大学计算机IV",
                          category="专业选修课程", credit=3.0))
    with pytest.raises(sqlite3.IntegrityError):
        db.add_waiver(_waiver(kind="credit", course_name="旧版大学计算机IV",
                              category="专业选修课程", credit=3.0))
    # 不同年级 / 不同学生 / 不同课名 → 允许
    db.add_waiver(_waiver(grade="2024级", kind="credit",
                          course_name="旧版大学计算机IV"))
    db.add_waiver(_waiver(student_id="S2", kind="credit",
                          course_name="旧版大学计算机IV"))
    db.add_waiver(_waiver(kind="credit", course_name="旧版高等数学"))
    # kind='course' 同 (grade, sid, course_code) 重复 → IntegrityError
    db.add_waiver(_waiver(kind="course", course_code="COMP201127",
                          course_name="大学计算机-算法编程"))
    with pytest.raises(sqlite3.IntegrityError):
        db.add_waiver(_waiver(kind="course", course_code="COMP201127",
                              course_name="大学计算机-算法编程"))
    # 不同年级 / 不同学生 / 不同课程码 → 允许
    db.add_waiver(_waiver(grade="2024级", kind="course", course_code="COMP201127"))
    db.add_waiver(_waiver(student_id="S2", kind="course", course_code="COMP201127"))
    db.add_waiver(_waiver(kind="course", course_code="OTHER999"))
    # 跨 kind 同旧课名不互斥（credit 认可与 course 平替引用同课名仍由应用层 N2 裁决）
    db.add_waiver(_waiver(kind="course", course_name="旧版大学计算机IV",
                          course_code="COMP201126", grade_source="旧版大学计算机IV"))
    assert len(db.list_waivers()) == 9


def test_selection_check_details_write_read(tmp_path):
    """details 显式列名读写：模型字段默认 '{}'；带 details 的行经 insert 落库原文、
    latest_selection_check ctor 传参读回（v7 建模后正规字段，无桥接）。"""
    db = _db(tmp_path)
    fid = db.insert_source_file(SourceFile(
        file_type="selection", file_name="s.xlsx", file_hash="h1",
        file_path="", upload_time="2026-09-01 00:00:00", uploader="t",
        parsed_status="done", grade="2023级",
        in_file_meta={"semester_label": "2026-2027学年 第一学期",
                      "semester_code": "4-1", "entry_year": 2023}))
    ct = "2026-09-04 10:00:00"
    det = '{"missing": [{"code": "COMP201127", "name": "大学计算机-算法编程", "credit": 3.0}]}'
    db.insert_selection_check_rows([
        SelectionCheckRow(source_file_id=fid, student_id="S1", name="甲",
                          major="工商管理", message="缺修 2 门", checked_at=ct),
        SelectionCheckRow(source_file_id=fid, student_id="S2", name="乙",
                          major="工商管理", category="专业核心课程",
                          expected_credit=24.0, gained_credit=20.0, gap=4.0,
                          message="差 4 学分", checked_at=ct, details=det)],
        "2023级")
    got = db.conn.execute(
        "SELECT student_id, details FROM selection_check ORDER BY student_id").fetchall()
    assert got == [("S1", "{}"), ("S2", det)]
    sf_id, checked_at, rows = db.latest_selection_check("2023级")
    assert sf_id == fid and checked_at == ct
    by_sid = {r.student_id: r for r in rows}
    assert set(by_sid) == {"S1", "S2"}
    assert by_sid["S2"].details == det
    assert by_sid["S1"].details == "{}"


# ============ service 层 run 装配（Task 3）：A 虚拟行注入 / B 类别叠加 ============

T0 = "2026-09-01 00:00:00"
_GRADE = "2023级"


def _g(sid, name, credit, pass_flag=1, marker="", term_label=""):
    """成绩行（真实成绩形态：与方案课同名 → clean 名匹配）。"""
    return Grade(student_id=sid, course_name=name, credit=credit,
                 grade_raw="85" if pass_flag else "55", pass_flag=pass_flag,
                 marker=marker, term_label=term_label,
                 course_name_clean=clean_course_name(name))


def _gr(sid, name, credit, raw, term_label=""):
    """成绩行（raw 驱动：成绩原文与 pass_flag 经 grade_to_pass 自洽——
    failed 节 / POST 及格源校验用例需指定分数原文如 45/59/75）。"""
    p, _ = grade_to_pass(raw)
    return Grade(student_id=sid, course_name=name, credit=credit,
                 grade_raw=raw, pass_flag=p, marker="", term_label=term_label,
                 course_name_clean=clean_course_name(name))


def _lib(sid):
    """该生修满除 REQ1 外全部应修：专业选修 8（选修课A-D ×2）+ 模块 12
    （未匹配 通识课◆ 标记课 ×2×6 → _grade_category 隐式归模块课程）。"""
    return ([_g(sid, f"选修课{n}", 2.0) for n in "ABCD"]
            + [_g(sid, f"通识课{i}", 2.0, marker="◆选修课") for i in range(1, 7)])


def _seed(db, s1_grades, plan_extra=()):
    """工商管理 2023级 全链路种子（S1 成绩按用例、S2 全修——防"全员无成绩"
    豁免与专业覆盖守卫干扰）。方案：REQ1 必修(专业核心 4) + 选修课A-D(专业选修
    8，JSON 毕业要求)；当前学期 4-1、成绩覆盖空；两生各选一门公共课 C1。
    plan_extra: (code, name, credit, type, required_flag, semester) 元组列表。"""
    db.insert_plan_meta("工商管理", "v1", "p.docx", T0, grade=_GRADE,
                        elective_req=8.0)
    pid = db.latest_active_plans(_GRADE)["工商管理"]
    rows = [("REQ1", "必修高数A", 4.0, "专业核心课程", "必修", "2-1"), ("EL1", "选修课A", 2.0, "专业选修课程", "选修", "3-1"), ("EL2", "选修课B", 2.0, "专业选修课程", "选修", "3-2"), ("EL3", "选修课C", 2.0, "专业选修课程", "选修", "3-1"), ("EL4", "选修课D", 2.0, "专业选修课程", "选修", "3-2"), *list(plan_extra)]
    db.insert_plan_courses(pid, [PlanCourse(
        plan_id=pid, course_code=c, course_name=n, credit=cr, course_type=ct,
        required_flag=f, semester=s) for c, n, cr, ct, f, s in rows])
    rid = db.insert_source_file(SourceFile(
        file_type="roster", file_name="r.xls", file_hash="r1", file_path="",
        upload_time=T0, uploader="t", parsed_status="done", grade=_GRADE))
    db.insert_roster([{"student_id": s, "name": n, "grade": _GRADE,
                       "major": "工商管理", "class_name": "工商2301",
                       "status": "正常", "source_file_id": rid}
                      for s, n in (("S1", "甲"), ("S2", "乙"))])
    fid = db.insert_source_file(SourceFile(
        file_type="selection", file_name="s.xlsx", file_hash="s1", file_path="",
        upload_time=T0, uploader="t", parsed_status="done", grade=_GRADE,
        in_file_meta={"semester_label": "2026-2027学年 第一学期",
                      "semester_code": "4-1", "grades": [_GRADE],
                      "entry_year": 2023}))
    db.insert_selections([
        Selection(student_id=s, name=n, major="工商管理", grade=_GRADE,
                  semester_label="2026-2027学年 第一学期", semester_code="4-1",
                  course_code="C1", course_name="公共课A", credit=2.0,
                  nature="必修", category="公共课程", status="选中",
                  retake="初修", source_file_id=fid)
        for s, n in (("S1", "甲"), ("S2", "乙"))])
    grades = list(s1_grades) + _lib("S2") + [_g("S2", "必修高数A", 4.0)]
    gid = db.insert_source_file(SourceFile(
        file_type="grade", file_name="g.docx", file_hash="g1", file_path="",
        upload_time=T0, uploader="t", parsed_status="done", grade=_GRADE,
        in_file_meta={"major": "工商管理"}))
    for g in grades:
        g.source_file_id = gid
    db.insert_grades(grades)


def _run(db):
    from academicwarning.service import run_selection_check
    return run_selection_check(db, _GRADE)


def _latest(db):
    _, _, rows = db.latest_selection_check(_GRADE)
    return {r.student_id: r for r in rows}


def test_kind_course_waiver_clears_missing_and_drops_out(tmp_path):
    """类型A 免修（grade_source 空）：缺修 REQ1 学生豁免 → 虚拟行注入消除缺修 +
    专业核心类别差额同步减 → 掉出名单（run 装配全链路）。"""
    db = _db(tmp_path)
    _seed(db, _lib("S1"))   # S1 除 REQ1 外全修
    n1, _ = _run(db)
    assert n1 == 1
    r1 = _latest(db)["S1"]
    assert "缺修 《必修高数A》" in r1.message
    assert "专业核心课程差 4.0（应4/已修0.0/已选0.0）" in r1.message
    assert [m["code"] for m in _miss_codes(r1.details)] == ["REQ1"]
    d1 = json.loads(r1.details)
    assert d1["cats"] == [{"cat": "专业核心课程", "expected": 4.0, "gained": 0.0,
                           "selected": 0.0, "gap": 4.0, "gap_ex_missing": 0.0,
                           "missing": [{"code": "REQ1", "name": "必修高数A",
                                        "credit": 4.0, "reason": "unattempted",
                                        "score": ""}]}]
    assert d1["other_missing"] == []
    db.add_waiver(_waiver(student_id="S1", kind="course", course_code="REQ1",
                          course_name="必修高数A", note="免修"))
    n2, note = _run(db)
    # 0 触发 run 不落库新批次（latest_selection_check 保留上一批有触发的行），
    # 掉出名单以 run 返回值 + 摘要人数为准
    assert n2 == 0
    assert "豁免 1 条生效" in note and "跳过" not in note
    assert "选课检查完成：检查 2 人，选课不合理 0 人" in note


def test_kind_course_substitute_grade_source_same_injection_path(tmp_path):
    """类型A 平替（grade_source 非空）：装配路径与免修一致——grade_source 只做
    结构化记录，run 注入不读它；同样缺修消除、掉出名单。"""
    db = _db(tmp_path)
    _seed(db, _lib("S1"))
    assert _run(db)[0] == 1
    db.add_waiver(_waiver(student_id="S1", kind="course", course_code="REQ1",
                          course_name="必修高数A", grade_source="大学计算机IV",
                          note="平替"))
    n2, note = _run(db)
    assert n2 == 0
    assert "豁免 1 条生效" in note
    assert "选课不合理 0 人" in note


def test_l1_skip_when_real_pass_exists(tmp_path):
    """L1 守卫（2026-09-07 及格制修订）：豁免课该生已有**真实及格**成绩 → 跳过
    注入（防虚拟行与真及格行重复计入）；摘要计 skipped 且带原因。"""
    db = _db(tmp_path)
    s1 = [g for g in _lib("S1") if g.course_name != "选修课D"]  # 少一门选修保持触发
    s1.append(_g("S1", "必修高数A", 4.0, pass_flag=1, marker="▲"))  # 重修及格（代表行及格）
    _seed(db, s1)
    assert _run(db)[0] == 1   # 专业选修差 2.0 触发（REQ1 已及格 → 无缺修）
    before = _latest(db)["S1"]
    db.add_waiver(_waiver(student_id="S1", kind="course", course_code="REQ1",
                          course_name="必修高数A"))
    n2, note = _run(db)
    assert n2 == 1
    assert "跳过 1 条" in note and "生效" not in note
    assert "课程《必修高数A》已有真实及格成绩（成绩单优先，豁免跳过）" in note
    assert "已匹配方案" not in note and "未在当前方案找到" not in note
    after = _latest(db)["S1"]
    for f in ("category", "expected_credit", "gained_credit", "gap", "message",
              "details"):
        assert getattr(after, f) == getattr(before, f)


def test_l1_allow_waiver_on_failed_required(tmp_path):
    """L1 守卫（2026-09-07 用户确认）：挂科必修（代表行不及格、在缺修清单
    reason=failed）→ 免修豁免**允许注入**（管理员特殊处理，豁免视为修过）：
    下次 run 缺修消除、类别差额同步减；非跳过。"""
    db = _db(tmp_path)
    s1 = [g for g in _lib("S1") if g.course_name != "选修课D"]  # 少一门选修保持触发
    s1.append(_g("S1", "必修高数A", 4.0, pass_flag=0))          # 挂科（45 形态 → grade_raw=55）
    _seed(db, s1)
    assert _run(db)[0] == 1
    before = _latest(db)["S1"]
    assert any(m["code"] == "REQ1" and m["reason"] == "failed"
               for m in _miss_codes(before.details))
    db.add_waiver(_waiver(student_id="S1", kind="course", course_code="REQ1",
                          course_name="必修高数A"))
    n2, note = _run(db)
    assert n2 == 1   # 专业选修差 2.0 仍触发
    assert "豁免 1 条生效" in note and "跳过" not in note
    after = _latest(db)["S1"]
    miss = _miss_codes(after.details)
    assert not any(m["code"] == "REQ1" for m in miss)   # REQ1 缺修消除（虚拟行命中）
    assert "必修高数A" not in after.message


def test_l5_skip_course_code_not_in_current_plan(tmp_path):
    """L5 运行期重解析失败：豁免 course_code 不在该生当前方案 → 跳过 + 摘要
    原因提示（不静默）；该生触发状态不变。"""
    db = _db(tmp_path)
    _seed(db, _lib("S1"))
    assert _run(db)[0] == 1
    before = _latest(db)["S1"]
    db.add_waiver(_waiver(student_id="S1", kind="course", course_code="OBS99",
                          course_name="已停课课程"))
    n2, note = _run(db)
    assert n2 == 1
    assert "跳过 1 条" in note
    assert "豁免课程《已停课课程》未在当前方案找到（可能方案已更新）" in note
    assert _latest(db)["S1"].details == before.details


def test_kind_credit_boosts_category_and_accumulates(tmp_path):
    """类型B 类别叠加：未匹配旧课真实成绩在（无类别分）→ 认可分在计算源头入
    已修——差额逐次减、details cats 含认可分、多门同类别累加；认可足额 → 掉出
    名单（N3：数字源头一致，前端不二次叠加）。"""
    db = _db(tmp_path)
    s1 = [_g("S1", f"选修课{n}", 2.0) for n in "AB"]            # 专业选修 4/8
    s1 += [_g("S1", f"通识课{i}", 2.0, marker="◆选修课") for i in range(1, 7)]
    s1 += [_g("S1", "必修高数A", 4.0)]                          # 专业核心 4/4
    s1 += [_g("S1", "旧版国际商务", 1.0), _g("S1", "旧版管理学", 1.0),
           _g("S1", "旧版金融", 3.0)]   # 未匹配旧课行（认可对象，自身无类别分）
    _seed(db, s1)
    n1, _ = _run(db)
    assert n1 == 1
    r1 = _latest(db)["S1"]
    assert "专业选修课程差 4.0（应8/已修4.0/已选0.0）" in r1.message
    db.add_waiver(_waiver(student_id="S1", kind="credit", course_name="旧版国际商务",
                          category="专业选修课程", credit=1.0))
    _, note = _run(db)
    assert "豁免 1 条生效" in note
    d2 = json.loads(_latest(db)["S1"].details)
    assert d2["cats"] == [{"cat": "专业选修课程", "expected": 8.0, "gained": 5.0,
                           "selected": 0.0, "gap": 3.0, "gap_ex_missing": 3.0,
                           "missing": []}]   # 差额 4→3（认可分含入）
    db.add_waiver(_waiver(student_id="S1", kind="credit", course_name="旧版管理学",
                          category="专业选修课程", credit=1.0))
    _, note = _run(db)
    assert "豁免 2 条生效" in note
    d3 = json.loads(_latest(db)["S1"].details)
    assert d3["cats"][0]["gained"] == 6.0 and d3["cats"][0]["gap"] == 2.0   # 多门累加
    db.add_waiver(_waiver(student_id="S1", kind="credit", course_name="旧版金融",
                          category="专业选修课程", credit=3.0))
    n4, note = _run(db)
    assert n4 == 0   # 已修 9 > 应修 8 → 认可溢出掉出名单（0 触发批次不落库）
    assert "豁免 3 条生效" in note


def test_n1_skip_credit_old_course_matched_to_plan(tmp_path):
    """N1 守卫：认可旧课在成绩单且已匹配方案（方案重传后场景）→ 跳过 + 摘要
    原因提示；真实成绩已归类计分，认可分不双计——结果与豁免前一致。"""
    db = _db(tmp_path)
    extra = [("COMP100", "大学计算机IV", 3.0, "专业选修课程", "选修", "3-1")]
    s1 = [*_lib("S1"), _g("S1", "大学计算机IV", 3.0)]   # 旧课现匹配方案 COMP100
    _seed(db, s1, plan_extra=extra)
    assert _run(db)[0] == 1    # 缺修 REQ1（专业选修 11≥8 无差额；模块 12/12）
    before = _latest(db)["S1"]
    db.add_waiver(_waiver(student_id="S1", kind="credit", course_name="大学计算机IV",
                          category="专业选修课程", credit=3.0))
    n2, note = _run(db)
    assert n2 == 1
    assert "跳过 1 条" in note
    assert "认可旧课《大学计算机IV》已匹配方案课程，豁免跳过" in note
    after = _latest(db)["S1"]
    assert after.details == before.details and after.gained_credit == before.gained_credit


def test_w2_missing_window_waiver_keeps_major_exemptions(tmp_path):
    """W2：豁免课处于全员无成绩窗口（4-1 未出成绩课，方案级豁免中）→ 虚拟行
    注入不得改变 prep._missing_courses——其他学生豁免清单/应修不变、无整专业
    放大误报（注入点在 Prep 之后、_compute_missing_courses 定格）。"""
    db = _db(tmp_path)
    extra = [("NEW901", "4-1新课高数B", 4.0, "专业核心课程", "必修", "4-1")]
    _seed(db, _lib("S1"), plan_extra=extra)   # S1 缺 REQ1；NEW901 全员无成绩
    n1, _ = _run(db)
    assert n1 == 1
    assert "4-1新课高数B" not in _latest(db)["S1"].message   # 全员无成绩 → 不报缺修
    meta1 = db.latest_source_file("selection", _GRADE).in_file_meta
    assert meta1["exempt_courses"]["工商管理"] == ["4-1新课高数B"]
    db.add_waiver(_waiver(student_id="S1", kind="course", course_code="NEW901",
                          course_name="4-1新课高数B"))
    _n2, note = _run(db)
    assert "豁免 1 条生效" in note
    latest = _latest(db)
    assert set(latest) == {"S1"}                     # S2 不被豁免放大拖入名单
    assert "必修高数A" in latest["S1"].message
    assert "4-1新课高数B" not in latest["S1"].message   # 该课豁免清单保持
    meta2 = db.latest_source_file("selection", _GRADE).in_file_meta
    assert meta2["exempt_courses"] == meta1["exempt_courses"]
    # prep 层不变式（W2 注入点契约）：豁免装配后 _missing_courses 仍含 NEW901
    from academicwarning.service import (_apply_waivers, _assemble_check_prep)
    prep = _assemble_check_prep(db, db.latest_source_file("selection", _GRADE))
    assert prep._missing_courses["工商管理"] == {"NEW901"}
    exp_s2 = prep.required_credits_by_category("S2")
    n3, _ = _apply_waivers(db, prep, _GRADE)
    assert n3 == 1
    assert prep._missing_courses["工商管理"] == {"NEW901"}   # 注入后仍不变
    assert prep.required_credits_by_category("S2") == exp_s2  # 他人应修不变
    from academicwarning.selection_check import check_selection_rationality
    rows = check_selection_rationality(prep)
    assert [r.student_id for r in rows] == ["S1"]
    assert all("4-1新课高数B" not in r.message for r in rows)


def test_upload_selection_auto_run_applies_waiver(tmp_path, monkeypatch):
    """端到端：上传选课触发自动 run（_upload_impl 内同步检查）→ 豁免装配生效：
    回复摘要含"（豁免 N 条生效）"、免修学生不再触发。"""
    import academicwarning.service as svc
    monkeypatch.setattr(svc, "is_admin", lambda p, u: True)
    db = _db(tmp_path)
    _seed(db, _lib("S1"))
    assert _run(db)[0] == 1   # 豁免前 S1 触发（基准确认）
    db.add_waiver(_waiver(student_id="S1", kind="course", course_code="REQ1",
                          course_name="必修高数A"))
    p = tmp_path / "2026-2027选课结果.xlsx"
    p.write_bytes(b"x")   # 占位：解析被 monkeypatch，仅 md5/类型识别读文件
    monkeypatch.setattr(svc, "parse_selection", lambda path: (
        {"semester_label": "2026-2027学年 第一学期", "semester_code": "4-1",
         "grades": [], "entry_year": 2023},
        [{"student_id": "S1", "name": "甲", "major": "工商管理",
          "course_code": "C1", "course_name": "公共课A", "nature": "必修",
          "category": "公共课程", "status": "选中", "retake": "初修"},
         {"student_id": "S2", "name": "乙", "major": "工商管理",
          "course_code": "C1", "course_name": "公共课A", "nature": "必修",
          "category": "公共课程", "status": "选中", "retake": "初修"}],
        ""))
    reply = svc.upload_file(str(p), uploader="admin", platform="wecom", db=db)
    assert "选课结果入库" in reply
    assert "豁免 1 条生效" in reply          # run 摘要带豁免提示
    assert "选课不合理 0 人" in reply        # S1 免修生效不再触发
    sf_id, _, rows = db.latest_selection_check("")
    assert sf_id is not None and rows == []


# ============ api 层（Task 4）：豁免端点 + student 详情 + details 出参 ============
from fastapi.testclient import TestClient
from academicwarning import api as warning_api

WAIVER_KEY = "test-waiver-key"


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    """api 测试环境：固定 _API_KEY + api/service 两模块的 WarningDB 指向临时库
    （照 test_api.py 同款隔离模式）。"""
    monkeypatch.setattr(warning_api, "_API_KEY", WAIVER_KEY)
    db_path = str(tmp_path / "waiver_api.db")
    import academicwarning.service as svc
    monkeypatch.setattr(warning_api, "WarningDB", lambda: WarningDB(db_path))
    monkeypatch.setattr(svc, "WarningDB", lambda: WarningDB(db_path))
    db = WarningDB(db_path)
    db.init_schema()
    db.close()
    return db_path


def _post_waiver(client, **body):
    return client.post("/api/warning/waiver", json=body,
                       headers={"X-API-Key": WAIVER_KEY})


def _delete_waiver(client, payload):
    """DELETE 带 body：httpx 版 TestClient.delete 不接受 json，走 request 原语。"""
    import json as _json
    return client.request(
        "DELETE", "/api/warning/waiver", content=_json.dumps(payload),
        headers={"X-API-Key": WAIVER_KEY, "Content-Type": "application/json"})


def test_api_waiver_auth_kind_roster_guard(api_env):
    """鉴权 401；非法 kind 拒绝；非名单学生拒绝；credit 课名不在成绩单拒绝。"""
    db = WarningDB(api_env)
    _seed(db, _lib("S1"))
    client = TestClient(warning_api.app)
    r = client.post("/api/warning/waiver", json={"grade": _GRADE,
                    "student_id": "S1", "kind": "course", "course_code": "REQ1"})
    assert r.status_code == 401
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="别的")
    assert r.json()["ok"] is False and "未知豁免类型" in r.json()["message"]
    r = _post_waiver(client, grade=_GRADE, student_id="S99", kind="course",
                     course_code="REQ1")
    assert r.json()["ok"] is False and "学籍名单" in r.json()["message"]
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="credit",
                     course_name="不存在的课", category="专业选修课程")
    assert r.json()["ok"] is False and "成绩单中未找到课程" in r.json()["message"]


def test_api_waiver_course_plan_code_and_success(api_env):
    """kind=course：缺 code / code 不在该生专业方案拒绝（按 roster 专业解析防
    错配）；平替源不在成绩单拒绝；平替与免修成功入库——课名服务端取方案值。"""
    db = WarningDB(api_env)
    _seed(db, [*_lib("S1"), _g("S1", "大学计算机IV", 3.0)])
    client = TestClient(warning_api.app)
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course")
    assert r.json()["ok"] is False and "course_code" in r.json()["message"]
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="NO900")
    assert r.json()["ok"] is False and "不在该生专业" in r.json()["message"]
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ1", grade_source="不存在课X")
    assert r.json()["ok"] is False and "成绩单中未找到平替课程" in r.json()["message"]
    # 平替成功（grade_source = 该生成绩单未匹配旧课）
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ1", grade_source="大学计算机IV", note="平替")
    assert r.json()["ok"] is True
    rows = WarningDB(api_env).list_waivers(_GRADE)
    assert [(w["kind"], w["course_code"], w["course_name"],
             w["grade_source"], w["note"]) for w in rows] == \
        [("course", "REQ1", "必修高数A", "大学计算机IV", "平替")]
    # 免修成功（grade_source 空；S2 已有真实 REQ1 成绩——L1 为 run 期守卫，
    # POST 不拦截，豁免按原样记录）
    r = _post_waiver(client, grade=_GRADE, student_id="S2", kind="course",
                     course_code="REQ1")
    assert r.json()["ok"] is True
    assert [w["student_id"] for w in WarningDB(api_env).list_waivers(_GRADE)] \
        == ["S1", "S2"]


def test_api_waiver_course_dup_rejected(api_env):
    """P1：kind='course' 同 (grade, sid, course_code) 重复拒绝——免修重复 POST、
    免修后同课平替均拒（改豁免须先撤销）；平替在案后同课免修也拒；不同课程码
    可并存（DB 唯一索引 idx_waiver_course_dup 兜底竞态）。"""
    db = WarningDB(api_env)
    _seed(db, [*_lib("S1"), _g("S1", "大学计算机IV", 3.0)],
          plan_extra=[("REQ2", "必修高数B", 4.0, "专业核心课程", "必修", "2-1")])
    client = TestClient(warning_api.app)
    # 免修成功 → 同课重复（免修+免修）拒绝
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ1")
    assert r.json()["ok"] is True
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ1")
    assert r.json()["ok"] is False and "豁免已存在" in r.json()["message"] \
        and "免修" in r.json()["message"]
    # 撤销后改平替 → 成功；平替在案 → 同课免修被拒（含平替来源标注）
    wid = WarningDB(api_env).list_waivers(_GRADE)[0]["id"]
    db.delete_waiver(wid)
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ1", grade_source="大学计算机IV")
    assert r.json()["ok"] is True
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ1")
    assert r.json()["ok"] is False and "豁免已存在" in r.json()["message"] \
        and "平替《大学计算机IV》" in r.json()["message"]
    # 不同课程码（REQ2 免修）可并存
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ2")
    assert r.json()["ok"] is True
    assert len(WarningDB(api_env).list_waivers(_GRADE)) == 2


def test_api_waiver_grade_source_matched_to_plan_rejected(api_env):
    """grade_source 已匹配方案（方案重传后该旧课现为方案课 COMP100）→ 拒绝：
    成绩单真实成绩已计入应修，无需再作平替来源（与 N1 对象域一致）。"""
    db = WarningDB(api_env)
    _seed(db, [*_lib("S1"), _g("S1", "大学计算机IV", 3.0)],
          plan_extra=[("COMP100", "大学计算机IV", 3.0,
                       "专业选修课程", "选修", "3-1")])
    client = TestClient(warning_api.app)
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ1", grade_source="大学计算机IV")
    assert r.json()["ok"] is False and "已匹配当前培养方案课程" in r.json()["message"]


def test_api_waiver_grade_source_referenced_rejected(api_env):
    """grade_source N2 互斥（防同一旧课双份计入）：已被 credit 认可豁免 → 平替
    拒绝；已被其他 course 豁免平替引用 → 平替拒绝。"""
    db = WarningDB(api_env)
    _seed(db, [*_lib("S1"), _g("S1", "大学计算机IV", 3.0), _g("S1", "旧版管理学", 2.0)],
          plan_extra=[("REQ2", "必修高数B", 4.0, "专业核心课程", "必修", "2-1")])
    db.add_waiver(_waiver(student_id="S1", kind="credit", course_name="大学计算机IV",
                          category="专业选修课程", credit=3.0))
    client = TestClient(warning_api.app)
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ1", grade_source="大学计算机IV")
    assert r.json()["ok"] is False and "已有学分认可豁免" in r.json()["message"]
    # 已被其他 course 平替引用
    db.add_waiver(_waiver(student_id="S1", kind="course", course_code="REQ1",
                          course_name="必修高数A", grade_source="旧版管理学"))
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ2",
                     course_name="必修高数B", grade_source="旧版管理学")
    assert r.json()["ok"] is False and "已被其他课程豁免用作平替来源" \
        in r.json()["message"]


def test_api_waiver_credit_validations_and_server_credit(api_env):
    """kind=credit：成绩单无此课 / 空课名 / 类别白名单外（含课外实践）拒绝；
    已匹配方案课拒绝；成功入库 credit 服务端取**最新学期**记录学分（body 值
    不信任）；重复键 (grade, sid, course_name) 拒绝。"""
    db = WarningDB(api_env)
    s1 = [*_lib("S1"), _g("S1", "旧版国际商务", 1.0), _g("S1", "旧版国际贸易实务", 1.0, term_label="第二学年（2024-2025）第二学期"), _g("S1", "旧版国际贸易实务", 2.5, term_label="第三学年（2025-2026）第一学期")]
    _seed(db, s1)
    client = TestClient(warning_api.app)
    # 成绩单无此课 / 缺课名
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="credit",
                     course_name="不存在的课", category="专业选修课程")
    assert r.json()["ok"] is False and "成绩单中未找到课程" in r.json()["message"]
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="credit")
    assert r.json()["ok"] is False and "course_name" in r.json()["message"]
    # 类别白名单外（8 类中的"课外实践"不可认可 + 乱写）
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="credit",
                     course_name="旧版国际商务", category="课外实践")
    assert r.json()["ok"] is False and "认可类别无效" in r.json()["message"]
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="credit",
                     course_name="旧版国际商务", category="随便写")
    assert r.json()["ok"] is False
    # 已匹配方案课（成绩单真实成绩已计入应修）→ 拒绝
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="credit",
                     course_name="选修课A", category="专业选修课程")
    assert r.json()["ok"] is False and "已匹配当前培养方案课程" in r.json()["message"]
    # 成功：多学期同名旧课 → credit = 最新学期（第三学年）记录 2.5
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="credit",
                     course_name="旧版国际贸易实务", category="专业选修课程",
                     credit=9.0, note="认可")
    assert r.json()["ok"] is True
    row = WarningDB(api_env).list_waivers(_GRADE)[0]
    assert row["kind"] == "credit" and row["credit"] == 2.5
    assert row["category"] == "专业选修课程"
    assert row["course_name"] == "旧版国际贸易实务"
    # 重复键 (grade, sid, course_name) 拒绝
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="credit",
                     course_name="旧版国际贸易实务", category="专业选修课程")
    assert r.json()["ok"] is False and "已存在" in r.json()["message"]


def test_api_waiver_credit_rejected_when_course_source(api_env):
    """N2 反向互斥：旧课已被 course 豁免用作平替来源（grade_source）→ credit
    认可拒绝。"""
    db = WarningDB(api_env)
    _seed(db, [*_lib("S1"), _g("S1", "大学计算机IV", 3.0)])
    db.add_waiver(_waiver(student_id="S1", kind="course", course_code="REQ1",
                          course_name="必修高数A", grade_source="大学计算机IV"))
    client = TestClient(warning_api.app)
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="credit",
                     course_name="大学计算机IV", category="专业选修课程")
    assert r.json()["ok"] is False and "用作平替来源" in r.json()["message"]


def test_api_waiver_list_and_delete(api_env):
    """GET 列表（grade 过滤、鉴权）+ DELETE 物理撤销（缺 id → 422，不存在 id
    幂等）。"""
    db = WarningDB(api_env)
    _seed(db, _lib("S1"))
    id1 = db.add_waiver(_waiver(course_code="REQ1", course_name="必修高数A"))
    id2 = db.add_waiver(_waiver(student_id="S2", kind="credit",
                                course_name="旧版国际商务",
                                category="专业选修课程", credit=1.0))
    client = TestClient(warning_api.app)
    assert client.get("/api/warning/waiver").status_code == 401
    r = client.get("/api/warning/waiver", headers={"X-API-Key": WAIVER_KEY})
    assert [w["id"] for w in r.json()["waivers"]] == [id1, id2]
    r = client.get("/api/warning/waiver?grade=2024级",
                   headers={"X-API-Key": WAIVER_KEY})
    assert r.json()["waivers"] == []
    r = _delete_waiver(client, {"id": id1})
    assert r.json()["ok"] is True and "已撤销豁免" in r.json()["message"]
    assert [w["id"] for w in
            client.get("/api/warning/waiver",
                       headers={"X-API-Key": WAIVER_KEY}).json()["waivers"]] \
        == [id2]
    # 不存在 id 幂等（物理删除无审计）；缺 id → 422
    r = _delete_waiver(client, {"id": 999})
    assert r.json()["ok"] is True
    assert _delete_waiver(client, {}).status_code == 422


def _student_detail(client, sid="S1"):
    """student 端点 GET + 200 断言（标准鉴权头）。"""
    r = client.get(f"/api/warning/selection-check/student?grade={_GRADE}&sid={sid}",
                   headers={"X-API-Key": WAIVER_KEY})
    assert r.status_code == 200
    return r.json()


def _seed_credit_waiver_env(db):
    """groups/waiver 用例共境：S1 两门旧课成绩，credit 认可豁免《旧版国际商务》。"""
    _seed(db, [*_lib("S1"), _g("S1", "旧版国际商务", 1.0), _g("S1", "旧版管理学", 2.0)])
    _run(db)
    db.add_waiver(_waiver(student_id="S1", kind="credit", course_name="旧版国际商务",
                          category="专业选修课程", credit=1.0, note="认可"))


def test_api_student_detail_groups_basic(api_env):
    """student 端点基础分组：details 与列表行同源（REQ1 unattempted）；已修两
    组分——matched 按 JSON 类别（含 ◆ 隐式归并模块课程）；v9 每课一行（行字段
    = 代表行 best_raw/ok，无成绩行级 grade_raw）；selections = 本学期选课。"""
    db = WarningDB(api_env)
    _seed_credit_waiver_env(db)
    client = TestClient(warning_api.app)
    d = _student_detail(client)
    assert d["student"] == {"student_id": "S1", "name": "甲", "major": "工商管理",
                            "class_name": "工商2301"}
    assert d["details"] == {"cats": [
        {"cat": "专业核心课程", "expected": 4.0, "gained": 0.0,
         "selected": 0.0, "gap": 4.0, "gap_ex_missing": 0.0,
         "missing": [{"code": "REQ1", "name": "必修高数A", "credit": 4.0,
                      "reason": "unattempted", "score": ""}]}],
        "other_missing": []}
    matched = {c["course_name"]: c for c in d["courses"]["matched"]}
    assert matched["选修课A"]["category"] == "专业选修课程"
    assert matched["通识课1"]["category"] == "模块课程"   # ◆ 隐式归并 → matched 侧
    assert matched["选修课A"]["credit"] == 2.0 and matched["选修课A"]["best_raw"] == "85"
    assert matched["选修课A"]["ok"] is True and matched["选修课A"]["attempts"] == 1
    assert "grade_raw" not in matched["选修课A"]
    assert "旧版国际商务" not in matched and "旧版管理学" not in matched
    assert d["courses"]["failed"] == []   # S1 无挂科课 → 未及格节空
    # 本学期选课（student 端点 selections）：种子 S1 选 C1 公共课A（选中）
    assert d["selections"] == [{"course_code": "C1", "course_name": "公共课A",
                                "credit": 2.0, "nature": "必修",
                                "category": "公共课程", "retake": "初修"}]


def test_api_student_detail_waiver_state_and_unmatched_candidates(api_env):
    """豁免状态与候选池：unmatched = 未被豁免消耗的未计入旧课（平替/认可候选，
    clean 名）；已认可（credit）旧课退出候选，waivers = 该生豁免状态；平替消耗
    （course grade_source）同样退出，撤销豁免后恢复（前端候选池/平替列表与该
    响应同源）。"""
    db = WarningDB(api_env)
    _seed_credit_waiver_env(db)
    client = TestClient(warning_api.app)
    d = _student_detail(client)
    unmatched = {c["course_name"]: c for c in d["courses"]["unmatched"]}
    # 已认可旧课（kind=credit 旧版国际商务）退出候选——只剩未被豁免消耗的旧课
    assert set(unmatched) == {"旧版管理学"}
    assert unmatched["旧版管理学"]["credit"] == 2.0
    assert unmatched["旧版管理学"]["best_raw"] == "85"
    assert unmatched["旧版管理学"]["ok"] is True
    assert unmatched["旧版管理学"]["usable"] is True
    assert "usable_reason" not in unmatched["旧版管理学"]
    assert d["waivers"] == [{"id": d["waivers"][0]["id"], "grade": _GRADE,
                             "student_id": "S1", "kind": "credit",
                             "course_code": "", "course_name": "旧版国际商务",
                             "grade_source": "", "category": "专业选修课程",
                             "credit": 1.0, "note": "认可", "created_by": "admin",
                             "created_at": "2026-09-04 10:00:00"}]
    # 平替消耗的旧课同样退出候选：REQ1 以《旧版管理学》为平替源 → unmatched 空
    db.add_waiver(_waiver(student_id="S1", kind="course", course_code="REQ1",
                          course_name="必修高数A", grade_source="旧版管理学"))
    assert _student_detail(client)["courses"]["unmatched"] == []
    db.delete_waiver(db.list_waivers(_GRADE)[-1]["id"])
    assert {c["course_name"] for c
            in _student_detail(client)["courses"]["unmatched"]} == {"旧版管理学"}


def test_api_student_detail_untriggered_and_offlist(api_env):
    """空态：名单内未触发学生（S2）details 空结构 + 无豁免；名单外学生（S99）
    student=None + 空分组。"""
    db = WarningDB(api_env)
    _seed(db, [*_lib("S1"), _g("S1", "旧版国际商务", 1.0), _g("S1", "旧版管理学", 2.0)])
    _run(db)
    client = TestClient(warning_api.app)
    d2 = _student_detail(client, sid="S2")
    assert d2["student"]["student_id"] == "S2"
    assert d2["details"] == {"cats": [], "other_missing": []}
    assert d2["waivers"] == []
    d3 = _student_detail(client, sid="S99")
    assert d3["student"] is None
    assert d3["courses"] == {"matched": [], "unmatched": [], "failed": []}


# ── failed 节 / usable 逐课语义（D4+C2+F）共境种子与工具 ──


def _seed_failed_scenario(db):
    """D4+C2+F 种子：选修课A 初修 85 + 重修 75（同课两行聚合）、必修 REQ1
    挂科 55、旧版国际商务两次挂科（45/55，最高分代表）、旧版管理学及格。"""
    s1 = [g for g in _lib("S1") if g.course_name != "选修课A"]
    s1 += [_g("S1", "选修课A", 2.0,
              term_label="第二学年（2024-2025）第二学期"),      # 初修 85
           _gr("S1", "选修课A", 2.0, "75",
               term_label="第三学年（2025-2026）第一学期")]     # 重修 75
    s1 += [_gr("S1", "必修高数A", 4.0, "55",
               term_label="第三学年（2025-2026）第一学期")]     # 方案必修 REQ1 挂科
    s1 += [_gr("S1", "旧版国际商务", 1.0, "45",
               term_label="第二学年（2024-2025）第二学期"),
           _gr("S1", "旧版国际商务", 1.0, "55",
               term_label="第三学年（2025-2026）第一学期")]     # 重修仍挂科
    s1 += [_g("S1", "旧版管理学", 2.0)]                        # 及格旧课
    _seed(db, s1)
    _run(db)


def _enroll_req1(db, source_file_id):
    """给 S1 补选必修 REQ1（重修选中状态），挂到指定选课批次。"""
    db.insert_selections([Selection(
        student_id="S1", name="甲", major="工商管理", grade=_GRADE,
        semester_label="2026-2027学年 第一学期", semester_code="4-1",
        course_code="REQ1", course_name="必修高数A", credit=4.0,
        nature="必修", category="专业核心课程", status="选中",
        retake="重修", source_file_id=source_file_id)])


def test_api_student_failed_aggregation_per_course(api_env):
    """D4+F（student 端点）：failed 节 = 代表行不及格每课一条（code 经
    _match_plan_course 取、reported_missing = 必修缺修在报、grade_raw = 最高分
    代表行）；matched 同课多行聚合每课一行（attempts/best_raw=最高分、
    term_label=代表行）；挂科方案课 ok=false 与 failed 节同现（互为印证）；
    details.missing reason=failed 透传。"""
    db = WarningDB(api_env)
    _seed_failed_scenario(db)
    client = TestClient(warning_api.app)
    d = _student_detail(client)
    assert _miss_codes(d["details"]) == [{"code": "REQ1", "name": "必修高数A",
                                          "credit": 4.0, "reason": "failed",
                                          "score": "55"}]
    # F：匹配但挂科的方案课同现 matched（行内 ok=false）与 failed 节
    matched = {c["course_name"]: c for c in d["courses"]["matched"]}
    assert matched["必修高数A"]["category"] == "专业核心课程"
    assert matched["必修高数A"]["ok"] is False
    assert matched["必修高数A"]["best_raw"] == "55"
    # 每课一行：初修+重修两行合并为一行，attempts=2，取最高分代表行字段
    assert matched["选修课A"]["attempts"] == 2
    assert matched["选修课A"]["best_raw"] == "85"
    assert matched["选修课A"]["ok"] is True
    assert matched["选修课A"]["term_label"] == "第二学年（2024-2025）第二学期"
    assert matched["选修课A"]["marker"] == ""
    # failed 节：每课一条（代表行字段），含必修在报与未匹配旧课两类
    failed = {c["course_name"]: c for c in d["courses"]["failed"]}
    assert set(failed) == {"必修高数A", "旧版国际商务"}
    f1 = failed["必修高数A"]
    assert f1["code"] == "REQ1" and f1["reported_missing"] is True
    assert f1["credit"] == 4.0 and f1["grade_raw"] == "55"
    assert f1["term_label"] == "第三学年（2025-2026）第一学期"
    assert f1["marker"] == ""
    f2 = failed["旧版国际商务"]
    assert f2["code"] == "" and f2["reported_missing"] is False
    assert f2["credit"] == 1.0 and f2["grade_raw"] == "55"   # 最高分代表（非 45）
    assert f2["term_label"] == "第三学年（2025-2026）第一学期"


def test_api_student_failed_usable_flags(api_env):
    """C2：unmatched 逐课 usable——挂科旧课保留候选但禁用（usable=false +
    usable_reason"挂科未过"），及格课 usable=true 无 reason。"""
    db = WarningDB(api_env)
    _seed_failed_scenario(db)
    client = TestClient(warning_api.app)
    unmatched = {c["course_name"]: c
                 for c in _student_detail(client)["courses"]["unmatched"]}
    u2 = unmatched["旧版国际商务"]
    assert u2["ok"] is False and u2["usable"] is False
    assert u2["usable_reason"] == "挂科未过"
    assert u2["attempts"] == 2 and u2["best_raw"] == "55"
    u3 = unmatched["旧版管理学"]
    assert u3["ok"] is True and u3["usable"] is True
    assert "usable_reason" not in u3


def test_api_student_failed_enrolled_not_reported(api_env):
    """I1"在修不报"（批次语义）：补选必修课后**未重跑** reported_missing 仍
    True（判定源 = 已加载批次缺修清单，不再实时近似当前选课）；同文件重跑
    0 触发不落新批次仍 True；上传新选课文件重跑形成新批次后翻 False。"""
    db = WarningDB(api_env)
    _seed_failed_scenario(db)
    client = TestClient(warning_api.app)
    # 补选 REQ1 到既有批次 → 未重跑：chip 仍 true（与同屏缺修清单双口径一致，
    # I1 生产实据同款——旧实现按当前选课实时近似，此处即翻 false）
    fid = db.latest_source_file("selection", _GRADE).id
    _enroll_req1(db, fid)
    d2 = _student_detail(client)
    f1b = {c["course_name"]: c for c in d2["courses"]["failed"]}["必修高数A"]
    assert f1b["reported_missing"] is True
    assert _miss_codes(d2["details"]) == [{"code": "REQ1", "name": "必修高数A",
                                           "credit": 4.0, "reason": "failed",
                                           "score": "55"}]
    # 同文件重跑 0 触发不落新批次（既有批次设计：latest_selection_check 保留
    # 上一批有触发行）→ 页面清单仍含 REQ1，chip 与清单同源保持一致（true）
    n2, _ = _run(db)
    assert n2 == 0   # 在修后 S1 已无触发（补选消除缺修 + 核心类别差额）
    f1c = {c["course_name"]: c
           for c in _student_detail(client)["courses"]["failed"]}["必修高数A"]
    assert f1c["reported_missing"] is True
    # 上传新选课文件（快照：S1 补选后全量）→ 触发重跑 → 新批次缺修清单不再含
    # REQ1 → reported_missing 翻 false（"在修不报"随批次刷新生效）
    fid2 = db.insert_source_file(SourceFile(
        file_type="selection", file_name="s2.xlsx", file_hash="s2", file_path="",
        upload_time=T0, uploader="t", parsed_status="done", grade=_GRADE,
        in_file_meta={"semester_label": "2026-2027学年 第一学期",
                      "semester_code": "4-1", "grades": [_GRADE],
                      "entry_year": 2023}))
    _enroll_req1(db, fid2)
    _run(db)
    d4 = _student_detail(client)
    assert _miss_codes(d4["details"]) == []
    f1d = {c["course_name"]: c for c in d4["courses"]["failed"]}["必修高数A"]
    assert f1d["reported_missing"] is False


def test_api_student_detail_legacy_missing_and_corrupt_json(api_env):
    """student_detail 重构回归（helper 拆分补盲）：老批次 details.missing 键
    （并集兼容）驱动 reported_missing；details JSON 损坏 → 空结构兜底不 500；
    kind=grade 豁免消耗挂科旧课 → failed/unmatched 双剔除。"""
    db = WarningDB(api_env)
    s1 = [g for g in _lib("S1") if g.course_name != "选修课A"]
    s1 += [_gr("S1", "必修高数A", 4.0, "55"), _gr("S1", "旧版国际商务", 1.0, "45")]
    _seed(db, s1)
    _run(db)
    client = TestClient(warning_api.app)
    url = f"/api/warning/selection-check/student?grade={_GRADE}&sid=S1"
    hdrs = {"X-API-Key": WAIVER_KEY}
    # details JSON 损坏（人为写坏库）→ 空结构兜底；failed 的必修行判定集为
    # 空 → reported_missing=False
    db.conn.execute("UPDATE selection_check SET details='{oops'"
                    " WHERE student_id='S1'")
    db.conn.commit()
    r = client.get(url, headers=hdrs)
    assert r.status_code == 200
    assert r.json()["details"] == {"cats": [], "other_missing": []}
    f = {c["course_name"]: c for c in r.json()["courses"]["failed"]}
    assert f["必修高数A"]["code"] == "REQ1"
    assert f["必修高数A"]["reported_missing"] is False
    # 老批次 details.missing（v7 前结构）→ 取并集 → reported_missing=True
    db.conn.execute(
        "UPDATE selection_check SET details=? WHERE student_id='S1'",
        (json.dumps({"cats": [], "other_missing": [],
                     "missing": [{"code": "REQ1"}]}),))
    db.conn.commit()
    r2 = client.get(url, headers=hdrs)
    f2 = {c["course_name"]: c for c in r2.json()["courses"]["failed"]}
    assert f2["必修高数A"]["reported_missing"] is True
    # kind=grade 豁免挂科旧课：used_clean 命中 + 无方案码 → failed 剔除
    # （_failed_row_for None 路径），unmatched 同步剔除
    db.add_waiver(_waiver(kind="grade", course_name="旧版国际商务", note="因病豁免"))
    r3 = client.get(url, headers=hdrs)
    d3 = r3.json()
    assert {c["course_name"] for c in d3["courses"]["failed"]} == {"必修高数A"}
    assert not [c for c in d3["courses"]["unmatched"]
                if c["course_name"] == "旧版国际商务"]


def test_api_waiver_source_course_must_pass(api_env):
    """E/C2（POST 校验）：平替源（grade_source）与认可源（course_name）须代表
    行及格——挂科旧课（多行最高分 59 仍不及格）两分支均拒，文案
    "最高分未及格（59）…仅限及格课作平替/认可源"；及格旧课作源两分支成功。"""
    db = WarningDB(api_env)
    s1 = [*_lib("S1"),
        _gr("S1", "旧版国际商务", 1.0, "45",
            term_label="第二学年（2024-2025）第二学期"),
        _gr("S1", "旧版国际商务", 1.0, "59",
            term_label="第三学年（2025-2026）第一学期"),   # 最高分仍不及格
        _g("S1", "旧版管理学", 2.0),   # 及格旧课（平替源）
        _g("S1", "旧版金融", 3.0)]     # 及格旧课（认可源）
    _seed(db, s1)
    client = TestClient(warning_api.app)
    # 挂科旧课作平替源（kind=course grade_source）→ 拒
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ1", grade_source="旧版国际商务")
    assert r.json()["ok"] is False
    assert "最高分未及格（59）" in r.json()["message"]
    assert "仅限及格课作平替/认可源" in r.json()["message"]
    # 挂科旧课作认可源（kind=credit course_name）→ 拒（同文案）
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="credit",
                     course_name="旧版国际商务", category="专业选修课程")
    assert r.json()["ok"] is False
    assert "最高分未及格（59）" in r.json()["message"]
    assert "仅限及格课作平替/认可源" in r.json()["message"]
    assert WarningDB(api_env).list_waivers(_GRADE) == []
    # 及格旧课作源 → 两分支均成功
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ1", grade_source="旧版管理学", note="平替")
    assert r.json()["ok"] is True
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="credit",
                     course_name="旧版金融", category="专业选修课程", note="认可")
    assert r.json()["ok"] is True
    rows = WarningDB(api_env).list_waivers(_GRADE)
    assert [(w["kind"], w["grade_source"], w["course_name"]) for w in rows] == \
        [("course", "旧版管理学", "必修高数A"), ("credit", "", "旧版金融")]


def test_api_selection_check_rows_carry_details(api_env):
    """GET /selection-check 行响应带 details（解析后 JSON，missing 含 code）；
    既有 message 保留（旧端兼容）。"""
    db = WarningDB(api_env)
    _seed(db, _lib("S1"))
    _run(db)
    client = TestClient(warning_api.app)
    r = client.get(f"/api/warning/selection-check?grade={_GRADE}",
                   headers={"X-API-Key": WAIVER_KEY})
    assert r.status_code == 200
    d = r.json()
    assert [s["student_id"] for s in d["students"]] == ["S1"]
    s = d["students"][0]
    assert _miss_codes(s["details"]) == [{"code": "REQ1", "name": "必修高数A",
                                          "credit": 4.0,
                                          "reason": "unattempted", "score": ""}]
    assert s["details"]["cats"] == [{"cat": "专业核心课程", "expected": 4.0,
                                     "gained": 0.0, "selected": 0.0, "gap": 4.0,
                                     "gap_ex_missing": 0.0,
                                     "missing": [{"code": "REQ1",
                                                  "name": "必修高数A",
                                                  "credit": 4.0,
                                                  "reason": "unattempted",
                                                  "score": ""}]}]
    assert "缺修 《必修高数A》" in s["message"]


def test_api_waiver_grade_kind_old_course_exempt(api_env):
    """kind='grade' 旧课豁免（2026-09-07）：仅限最高分不及格的方案外旧课；
    记录后 failed/unmatched 清单剔除该课（撤销恢复）；及格旧课拒绝；与
    credit/course 源互斥。"""
    db = WarningDB(api_env)
    _seed(db, [*_lib("S1"), _g("S1", "旧版高数(45)", 4.0, pass_flag=0), _g("S1", "旧版英语(及格)", 2.0, pass_flag=1)])
    _run(db)
    client = TestClient(warning_api.app)
    # 及格旧课 → 拒绝（可正常作源）
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="grade",
                     course_name="旧版英语(及格)", note="x")
    assert r.json()["ok"] is False and "最高分已及格" in r.json()["message"]
    # 挂科旧课豁免成功 → failed/unmatched 剔除
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="grade",
                     course_name="旧版高数(45)", note="学校批准")
    assert r.json()["ok"] is True
    d = client.get(f"/api/warning/selection-check/student?grade={_GRADE}&sid=S1",
                   headers={"X-API-Key": WAIVER_KEY}).json()
    assert all(c["course_name"] != "旧版高数(45)" for c in d["courses"]["failed"])
    assert all(c["course_name"] != "旧版高数(45)" for c in d["courses"]["unmatched"])
    # 重复/互斥：再次 grade 豁免拒绝；credit 认可同课拒绝
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="grade",
                     course_name="旧版高数(45)")
    assert r.json()["ok"] is False and "已存在" in r.json()["message"]
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="credit",
                     course_name="旧版高数(45)", category="专业选修课程")
    assert r.json()["ok"] is False and "已有旧课豁免" in r.json()["message"]
    # 撤销 → 恢复出现
    wid = next(w for w in db.list_waivers(_GRADE) if w["kind"] == "grade")["id"]
    db.delete_waiver(wid)
    d2 = client.get(f"/api/warning/selection-check/student?grade={_GRADE}&sid=S1",
                    headers={"X-API-Key": WAIVER_KEY}).json()
    assert any(c["course_name"] == "旧版高数(45)" for c in d2["courses"]["failed"])


def test_api_waiver_missing_sid_and_no_plan_student(api_env):
    """端点前置守卫：缺 student_id 拒绝；名单内但专业无当前方案的学生拒绝
    （_student_prep None → 无法校验豁免归属）。"""
    db = WarningDB(api_env)
    _seed(db, _lib("S1"))
    rid = db.latest_source_file("roster", _GRADE).id
    db.insert_roster([{"student_id": "S3", "name": "丙", "grade": _GRADE,
                       "major": "哲学", "class_name": "哲2301", "status": "正常",
                       "source_file_id": rid}])
    client = TestClient(warning_api.app)
    r = _post_waiver(client, grade=_GRADE, kind="course", course_code="REQ1")
    assert r.json()["ok"] is False and "缺少学生学号" in r.json()["message"]
    r = _post_waiver(client, grade=_GRADE, student_id="S3", kind="course",
                     course_code="REQ1")
    assert r.json()["ok"] is False and "无有效培养方案" in r.json()["message"]


def test_api_waiver_source_name_invalid_and_grade_kind_guards(api_env):
    """course 平替源课名清洗后为空 → 无效；grade kind 缺课名 / 成绩单无此课 /
    挂科旧课已匹配方案课（应走课程免修而非旧课豁免）。"""
    db = WarningDB(api_env)
    _seed(db, [_g("S1", "旧版军理", 2.0, pass_flag=0)],
          plan_extra=[("MIL9", "旧版军理", 2.0, "专业核心课程", "必修", "2-1")])
    client = TestClient(warning_api.app)
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ1", grade_source="＊＊＊")
    assert r.json()["ok"] is False and "平替来源课程名无效" in r.json()["message"]
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="grade")
    assert r.json()["ok"] is False and "缺少课程名" in r.json()["message"]
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="grade",
                     course_name="不存在课程X")
    assert r.json()["ok"] is False and "成绩单中未找到课程" in r.json()["message"]
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="grade",
                     course_name="旧版军理")
    assert r.json()["ok"] is False and "已匹配当前培养方案课程" in r.json()["message"]


def test_api_waiver_conflict_scoped_to_student(api_env):
    """N2 互斥按学号隔离：他人（S2）的 credit 豁免不阻断 S1 同名旧课作平替源。"""
    db = WarningDB(api_env)
    _seed(db, [*_lib("S1"), _g("S1", "旧版管理学", 2.0)])
    db.add_waiver(_waiver(student_id="S2", kind="credit", course_name="旧版管理学",
                          category="专业选修课程", credit=2.0))
    client = TestClient(warning_api.app)
    r = _post_waiver(client, grade=_GRADE, student_id="S1", kind="course",
                     course_code="REQ1", grade_source="旧版管理学")
    assert r.json()["ok"] is True


def test_api_waiver_db_integrity_error_fallback(api_env, monkeypatch):
    """三个 kind 的 IntegrityError 兜底（应用层查重后 DB 唯一索引命中竞态）：
    monkeypatch ConflictDB 复现，统一回落「豁免记录冲突」提示。
    异常类取 warning_api.sqlite3（全量跑时 chromadb 会把 sys.modules 的
    sqlite3 替换成 pysqlite3——须与被测 except 绑定同源类）。"""
    _sq = warning_api.sqlite3

    db = WarningDB(api_env)
    _seed(db, [*_lib("S1"), _g("S1", "旧版管理学", 2.0),
               _g("S1", "旧版军理", 2.0, pass_flag=0)])

    class _ConflictDB(WarningDB):
        def add_waiver(self, w):
            raise _sq.IntegrityError("UNIQUE constraint failed")

    monkeypatch.setattr(warning_api, "WarningDB", lambda: _ConflictDB(api_env))
    client = TestClient(warning_api.app)
    for kind, extra in (
        ("course", {"course_code": "REQ1"}),
        ("grade", {"course_name": "旧版军理"}),
        ("credit", {"course_name": "旧版管理学", "category": "专业选修课程"}),
    ):
        r = _post_waiver(client, grade=_GRADE, student_id="S1", kind=kind, **extra)
        assert r.json()["ok"] is False and "豁免记录冲突" in r.json()["message"], kind
