"""学籍名单 roster 测试（v6.5，2026-09-04：提醒名单以学籍为准 + 未选课单独提醒）。"""
import os
import time
import pytest
from fastapi.testclient import TestClient

from academicwarning.db import WarningDB
from academicwarning.models import SourceFile

ROSTER_SAMPLE = "academicwarning/docs/2023级学籍信息.xls"

# 上传校验测试鉴权（与 test_grade_partition.py 同模式）
TEST_KEY = "test-key-123"


def _db(tmp_path):
    db = WarningDB(str(tmp_path / "t.db"))
    db.init_schema()
    return db


def _roster_xls(path, rows):
    """xlwt 生成最小学籍 xls（表头列名与样本一致；parse 按名列定位）。

    rows: [(学号, 姓名, 年级, 专业, 班级, 是否在籍, 是否在校, 学籍状态), ...]"""
    import xlwt
    wb = xlwt.Workbook()
    ws = wb.add_sheet("sheet1")
    for c, h in enumerate(("学号", "姓名", "年级", "专业", "班级",
                           "是否在籍", "是否在校", "学籍状态")):
        ws.write(0, c, h)
    for r, row in enumerate(rows, start=1):
        for c, v in enumerate(row):
            ws.write(r, c, v)
    wb.save(str(path))


def _roster_file(tmp_path, name, rows):
    p = tmp_path / name
    _roster_xls(p, rows)
    return p


# ---- parse_roster ----

@pytest.mark.samples
def test_parse_roster_real_sample(tmp_path):
    """真实样本集成测试（样本与真实人数分布不入库，见 docs/02-features/ 测试数据
    分层设计）：136 人全在籍在校 → 全收；专业代码剥除归一到 4 标准名；
    meta 年级众数 = 2023级。姓名仅做结构校验（全非空），真值核对在开发机人工完成。"""
    if not os.path.exists(ROSTER_SAMPLE):
        pytest.skip("样本缺失")
    from academicwarning.parsers import parse_roster
    meta, rows = parse_roster(ROSTER_SAMPLE)
    assert meta["grade"] == "2023级"
    assert len(rows) == 136
    assert meta["majors"] == {"工商管理": 34, "会计学（ACCA）": 44,
                              "大数据管理与应用": 29, "工业工程": 29}
    names = {r["name"] for r in rows}
    assert all(names) and len(names) > 100   # 姓名全非空（真值核对在开发机人工完成）
    for r in rows:
        assert r["major"] in ("工商管理", "大数据管理与应用", "工业工程", "会计学（ACCA）")
        assert r["student_id"].isdigit() and "." not in r["student_id"]
        assert r["class_name"] and r["status"] == "正常"


def test_parse_roster_filter_and_grade_mode(tmp_path):
    """在籍在校过滤（休学/退学排除）+ 专业代码剥除 + 数字学号归一 +
    meta 主年级 = 在籍在校学生年级众数。"""
    from academicwarning.parsers import parse_roster
    p = _roster_file(tmp_path, "学籍信息.xls", [
        ("2023001", "张三", "2023级", "0824工商管理", "工商2301", "是", "是", "正常"),
        ("2023002", "李四", "2023级", "工商管理", "工商2301", "是", "是", "正常"),  # 无代码前缀
        ("2023003", "王五", "2023级", "0842会计学（ACCA）", "ACCA2301", "是", "否", "休学"),
        ("2023004", "赵六", "2022级", "0846大数据管理与应用", "大数据2201", "否", "是", "退学"),
        (2023005, "钱七", "2023级", "0826工业工程", "工业工程2301", "是", "是", "正常"),
        ("2022001", "孙八", "2022级", "0824工商管理", "工商2201", "是", "是", "正常"),
    ])
    meta, rows = parse_roster(str(p))
    assert meta["grade"] == "2023级"   # 众数（被排除行不计；孙八 2022级 在籍在校 → 计入）
    assert meta["majors"] == {"工商管理": 3, "工业工程": 1}
    assert [(r["student_id"], r["name"]) for r in rows] == [
        ("2023001", "张三"), ("2023002", "李四"), ("2023005", "钱七"),
        ("2022001", "孙八")]
    assert [r["major"] for r in rows] == ["工商管理", "工商管理", "工业工程", "工商管理"]
    assert rows[2]["student_id"] == "2023005"   # 数字单元格 → 整数字符串


# ---- db：roster 表 ----

def test_db_roster_insert_latest_partitioned(tmp_path):
    """insert_roster/latest_roster round-trip：按年级分区取最新文件（done）行；
    roster 文件解析失败/无文件 → []。"""
    db = _db(tmp_path)

    def _up(grade, rows, fname, status="done"):
        rid = db.insert_source_file(SourceFile(
            file_type="roster", file_name=fname, file_hash=fname, file_path="",
            upload_time="2026-09-01 00:00:00", uploader="t",
            parsed_status=status, grade=grade))
        db.insert_roster([{**r, "source_file_id": rid} for r in rows])
        return rid

    _up("2023级", [{"student_id": "S1", "name": "甲", "grade": "2023级",
                    "major": "工商管理", "class_name": "工商2301", "status": "正常"}], "r1.xls")
    _up("2024级", [{"student_id": "T1", "name": "丙", "grade": "2024级",
                    "major": "工业工程", "class_name": "工业2401", "status": "正常"}], "r2.xls")
    g23 = db.latest_roster("2023级")
    assert [s.student_id for s in g23] == ["S1"]
    assert g23[0].name == "甲" and g23[0].major == "工商管理"
    assert g23[0].class_name == "工商2301" and g23[0].grade == "2023级"
    assert g23[0].enrolled_status == "在籍"
    assert [s.student_id for s in db.latest_roster("2024级")] == ["T1"]
    assert [s.student_id for s in db.latest_roster("")] == ["T1"]   # 空 = 最新文件全取
    assert db.latest_roster("2026级") == []
    # 新文件 → latest_roster 绑定最新文件行（旧文件行保留在库，replace 由 service 层
    # 先 delete_roster 再插新实现——见 test_upload_roster_parsed_and_replace）
    _up("2023级", [{"student_id": "S9", "name": "丁", "grade": "2023级",
                    "major": "工商管理", "class_name": "工商2301", "status": "正常"}], "r3.xls")
    assert [s.student_id for s in db.latest_roster("2023级")] == ["S9"]
    # 文件解析失败（latest_roster 绑定 done 文件）→ []
    _up("2023级", [{"student_id": "SX", "name": "戊", "grade": "2023级"}], "r4.xls",
        status="failed")
    assert [s.student_id for s in db.latest_roster("2023级")] == ["S9"]


def test_db_delete_roster_and_cascade(tmp_path):
    """delete_roster 整年级删除；delete_major_files("roster") 级联删名单行。"""
    db = _db(tmp_path)

    def _up(grade, sid, fname):
        rid = db.insert_source_file(SourceFile(
            file_type="roster", file_name=fname, file_hash=fname, file_path="",
            upload_time="2026-09-01 00:00:00", uploader="t", parsed_status="done",
            grade=grade))
        db.insert_roster([{"student_id": sid, "name": f"学{sid}", "grade": grade,
                           "major": "工商管理", "class_name": "工商2301",
                           "status": "正常", "source_file_id": rid}])

    _up("2023级", "S1", "r1.xls")
    _up("2024级", "T1", "r2.xls")
    db.delete_roster("2023级")
    assert db.latest_roster("2023级") == []
    assert [s.student_id for s in db.latest_roster("2024级")] == ["T1"]  # 分区隔离
    # 文件删除级联：删 2024级 roster 文件 → 2024级 名单行清空
    db.delete_major_files("roster", grade="2024级")
    assert db.latest_roster("2024级") == []
    assert db.latest_source_file("roster", "2024级") is None


# ---- service：_upload_impl roster 分支（年级校验 + 入库 + 替换）----

def test_upload_roster_parsed_and_replace(tmp_path):
    """roster 入库：年级校验一致 → 入库（先删同年级旧行再插新）；不一致 → 拒绝文本、
    不产生记录（文案同 selection）。"""
    from academicwarning.service import _upload_impl
    db = _db(tmp_path)
    p1 = _roster_file(tmp_path, "学籍信息-v1.xls", [
        ("2023001", "张三", "2023级", "0824工商管理", "工商2301", "是", "是", "正常")])
    p2 = _roster_file(tmp_path, "学籍信息-v2.xls", [
        ("2023002", "李四", "2023级", "0826工业工程", "工业工程2301", "是", "是", "正常"),
        ("2023003", "王五", "2023级", "0846大数据管理与应用", "大数据2301", "是", "是", "正常")])
    p3 = _roster_file(tmp_path, "学籍信息-2024级.xls", [
        ("2024001", "丙", "2024级", "0824工商管理", "工商2401", "是", "是", "正常")])

    r1 = _upload_impl(str(p1), uploader="t", platform="api", db=db,
                      force_type="roster", grade_hint="2023级")
    assert "学籍名单入库" in r1 and "1 名学生" in r1
    sf = db.latest_source_file("roster", "2023级")
    assert sf.in_file_meta["grade"] == "2023级"
    assert sf.in_file_meta["count"] == 1
    assert sf.in_file_meta["majors"] == {"工商管理": 1}

    # 同年级重传（不同内容）→ 替换而非追加
    _upload_impl(str(p2), uploader="t", platform="api", db=db,
                 force_type="roster", grade_hint="2023级")
    assert sorted(s.student_id for s in db.latest_roster("2023级")) == ["2023002", "2023003"]
    assert db.conn.execute(
        "SELECT COUNT(*) FROM roster WHERE grade='2023级'").fetchone()[0] == 2

    # 年级不一致 → 拒绝文本、不产生记录（含 failed）
    r3 = _upload_impl(str(p3), uploader="t", platform="api", db=db,
                      force_type="roster", grade_hint="2023级")
    assert "主年级为「2024级」" in r3 and "不匹配" in r3
    assert db.latest_source_file("roster", "2024级") is None
    assert db.conn.execute("SELECT COUNT(*) FROM source_file WHERE file_type='roster'"
                           " AND parsed_status='failed'").fetchone()[0] == 0


def test_db_replace_roster_atomic(tmp_path):
    """M-1：同年级替换单事务——插入中途失败 → 回滚，旧名单行保留（不删旧不半插）。"""
    db = _db(tmp_path)
    rid = db.insert_source_file(SourceFile(
        file_type="roster", file_name="r1.xls", file_hash="h1", file_path="",
        upload_time="2026-09-01 00:00:00", uploader="t", parsed_status="done",
        grade="2023级"))
    db.insert_roster([{"student_id": "S1", "name": "甲", "grade": "2023级",
                       "major": "工商管理", "class_name": "工商2301",
                       "status": "正常", "source_file_id": rid}])
    good = {"student_id": "S2", "name": "乙", "grade": "2023级",
            "major": "工商管理", "class_name": "工商2301",
            "status": "正常", "source_file_id": rid}
    bad = {"name": "丙", "grade": "2023级", "source_file_id": rid}   # 缺 student_id
    with pytest.raises(KeyError):
        db.replace_roster("2023级", [good, bad])
    rows = db.conn.execute(
        "SELECT student_id FROM roster WHERE grade='2023级'").fetchall()
    assert [r[0] for r in rows] == ["S1"]   # 旧名单未被删、坏行未插入


def test_upload_roster_empty_does_not_clear_old(tmp_path):
    """I-1：空/坏 xls 解析 0 行 → 记录 failed（不 done、不删旧名单）并返回提示。"""
    from academicwarning.service import _upload_impl
    db = _db(tmp_path)
    good = _roster_file(tmp_path, "学籍信息-v1.xls", [
        ("2023001", "张三", "2023级", "0824工商管理", "工商2301", "是", "是", "正常")])
    msg = _upload_impl(str(good), uploader="t", platform="api", db=db,
                       force_type="roster", grade_hint="2023级")
    assert "1 名学生" in msg

    # 空文件（仅表头无数据行）→ 0 行守卫
    empty = _roster_file(tmp_path, "学籍信息-empty.xls", [])
    r = _upload_impl(str(empty), uploader="t", platform="api", db=db,
                     force_type="roster", grade_hint="2023级")
    assert "学籍名单解析为空" in r
    # 表头不符（有数据行但无学号等列名）→ 同样 0 行守卫
    import xlwt
    bad = tmp_path / "学籍信息-bad.xls"
    wb = xlwt.Workbook()
    ws = wb.add_sheet("sheet1")
    ws.write(0, 0, "foo")
    ws.write(1, 0, "2023001")
    wb.save(str(bad))
    r2 = _upload_impl(str(bad), uploader="t", platform="api", db=db,
                      force_type="roster", grade_hint="2023级")
    assert "学籍名单解析为空" in r2

    # 最新 roster 记录 = failed；旧名单未被清空（latest_roster 仍绑定此前 done 文件）
    sf = db.latest_source_file("roster", "2023级")
    assert sf.parsed_status == "failed" and "解析为空" in (sf.in_file_meta or {}).get("error", "")
    assert db.conn.execute("SELECT COUNT(*) FROM source_file WHERE file_type='roster'"
                           " AND grade='2023级' AND parsed_status='done'"
                           ).fetchone()[0] == 1
    assert [s.student_id for s in db.latest_roster("2023级")] == ["2023001"]


# ---- 检查名单来源 = roster + 未选课单独提醒 ----

def _seed_check(db, roster_extra=("S2", "乙")):
    """2023级 工商：S1 有选课（无专业选修 → 触发）；名单还含 S2（可无选课）。

    同 test_grade_partition._seed_check_by_grade 的触发形态（专业选修应修 8，
    S1 只修了 ◆通识课 → 模块 12 达标；专业选修差 8 → 触发）。"""
    from academicwarning.models import PlanCourse, Selection, Grade
    from academicwarning.parsers import clean_course_name
    db.insert_plan_meta("工商管理", "v1", "p.docx", "2026-09-01 00:00:00",
                        grade="2023级", elective_req=8.0)
    pid = db.conn.execute("SELECT id FROM training_plan WHERE major_name='工商管理'"
                          " AND grade='2023级' ORDER BY id DESC LIMIT 1").fetchone()[0]
    db.insert_plan_courses(pid, [PlanCourse(plan_id=pid, course_code=f"EL{i}",
                                            course_name=f"选修课{i}", credit=2.0,
                                            course_type="专业选修课程",
                                            required_flag="选修", semester="3-1")
                                 for i in range(1, 5)])
    fid = db.insert_source_file(SourceFile(
        file_type="selection", file_name="s.xlsx", file_hash="h-s", file_path="",
        upload_time="2026-09-01 00:00:00", uploader="t", parsed_status="done",
        grade="2023级",
        in_file_meta={"semester_label": "2026-2027学年 第一学期",
                      "semester_code": "4-1", "grades": ["2023级"],
                      "entry_year": 2023}))
    db.insert_selections([Selection(
        student_id="S1", name="甲", major="工商管理", grade="2023级",
        semester_label="2026-2027学年 第一学期", semester_code="4-1",
        course_code="C1", course_name="公共课", credit=2.0, nature="必修",
        category="公共课程", status="选中", source_file_id=fid)])
    gid = db.insert_source_file(SourceFile(
        file_type="grade", file_name="g.docx", file_hash="h-g", file_path="",
        upload_time="2026-09-01 00:00:00", uploader="t", parsed_status="done",
        grade="2023级", in_file_meta={"major": "工商管理"}))
    db.insert_grades([Grade(student_id="S1", student_name="甲",
                            course_name=f"通识课{i}◆", credit=2.0, grade_raw="80",
                            pass_flag=1, marker="◆选修课",
                            course_name_clean=clean_course_name(f"通识课{i}◆"),
                            source_file_id=gid) for i in range(6)])
    rid = db.insert_source_file(SourceFile(
        file_type="roster", file_name="r.xls", file_hash="h-r", file_path="",
        upload_time="2026-09-01 00:00:00", uploader="t", parsed_status="done",
        grade="2023级"))
    db.insert_roster([{"student_id": sid, "name": name, "grade": "2023级",
                       "major": "工商管理", "class_name": "工商2301",
                       "status": "正常", "source_file_id": rid}
                      for sid, name in (("S1", "甲"), roster_extra)])


def test_run_not_selected_stored_in_meta(tmp_path):
    """名单 2 人（1 人选课）→ 触发仅 S1；S2 未选课不入合理性计算，
    随批次落选课文件 meta（not_selected）；run 摘要含未选课单独提醒。"""
    from academicwarning.service import run_selection_check
    db = _db(tmp_path)
    _seed_check(db)
    n, note = run_selection_check(db, "2023级")
    assert n == 1
    assert "名单内本学期未选课 1 人：乙" in note
    assert "未参与合理性计算" in note
    meta = db.latest_source_file("selection", "2023级").in_file_meta
    assert meta["not_selected"] == [{
        "student_id": "S2", "name": "乙", "major": "工商管理",
        "class_name": "工商2301"}]
    sids = [r[0] for r in db.conn.execute(
        "SELECT student_id FROM selection_check WHERE grade='2023级'")]
    assert sids == ["S1"]


def test_run_grade_sid_backfill_by_name(tmp_path):
    """姓名↔名单交叉校正（_fix_grade_ids_by_name）：OCR 学号错位的成绩行
    （学号不在名单）按姓名回填到名单学号——错位学分计入该生已修 → 不再触发；
    姓名/学号都对不上的行保持原样不崩。"""
    from academicwarning.models import Grade
    from academicwarning.parsers import clean_course_name
    from academicwarning.service import run_selection_check
    db = _db(tmp_path)
    _seed_check(db)
    gid = db.conn.execute(
        "SELECT id FROM source_file WHERE file_type='grade'"
        " ORDER BY id DESC LIMIT 1").fetchone()[0]
    # OCR 学号错位（"S9" 不在名单）但姓名与 S1 一致 → 回填到 S1：
    # 专业选修 4×2=8 补齐 → S1 不再触发
    db.insert_grades([Grade(
        student_id="S9", student_name="甲", course_name=f"选修课{i}",
        credit=2.0, grade_raw="80", pass_flag=1,
        course_name_clean=clean_course_name(f"选修课{i}"), source_file_id=gid)
        for i in range(1, 5)])
    # 学号姓名都对不上（不在名单、无同名）→ 原样保留，不影响任何人
    db.insert_grades([Grade(
        student_id="S8", student_name="丙", course_name="选修课9", credit=2.0,
        grade_raw="80", pass_flag=1,
        course_name_clean=clean_course_name("选修课9"), source_file_id=gid)])
    n, note = run_selection_check(db, "2023级")
    assert n == 0   # S1 选修学分经姓名回填补齐（模块此前已达标）→ 无触发
    assert "名单内本学期未选课 1 人：乙" in note


def test_run_all_selected_not_selected_empty(tmp_path):
    """名单学生都有选课 → not_selected 空（列表空落 meta）。"""
    from academicwarning.models import Selection
    from academicwarning.service import run_selection_check
    db = _db(tmp_path)
    _seed_check(db)
    # S2 补选课 → 名单两人全部有选课
    fid = db.latest_source_file("selection", "2023级").id
    db.insert_selections([Selection(
        student_id="S2", name="乙", major="工商管理", grade="2023级",
        semester_label="2026-2027学年 第一学期", semester_code="4-1",
        course_code="C1", course_name="公共课", credit=2.0, nature="必修",
        category="公共课程", status="选中", source_file_id=fid)])
    n, note = run_selection_check(db, "2023级")
    assert n == 2   # S2 也参与检查（有选课；无成绩但同专业有覆盖 → 差额触发）
    assert "未选课" not in note
    meta = db.latest_source_file("selection", "2023级").in_file_meta
    assert meta.get("not_selected") == []


def test_run_no_roster_degrades(tmp_path):
    """缺 roster → 降级"学籍名单未上传，未执行检查"（不再用选课文件兜底名单）。"""
    from academicwarning.service import run_selection_check
    db = _db(tmp_path)
    _seed_check(db)
    db.delete_roster("2023级")   # 名单行删光（文件仍在）
    n, note = run_selection_check(db, "2023级")
    assert n == 0 and note == "选课检查：学籍名单未上传，未执行检查"


# ---- api：上传校验 + GET /selection-check 未选课字段 ----

@pytest.fixture
def api_env(tmp_path, monkeypatch):
    """api 测试环境：临时库 + 上传目录 + 固定 key（同 test_grade_partition）。"""
    from academicwarning import api
    monkeypatch.setattr(api, "_API_KEY", TEST_KEY)
    monkeypatch.setattr(api, "_UPLOAD_DIR", tmp_path / "uploads")
    db_path = str(tmp_path / "warning.db")
    import academicwarning.service as svc
    monkeypatch.setattr(api, "WarningDB", lambda: WarningDB(db_path))
    monkeypatch.setattr(svc, "WarningDB", lambda: WarningDB(db_path))
    _db = WarningDB(db_path)
    try:
        _db.init_schema()
    finally:
        _db.close()
    return db_path


@pytest.fixture
def client():
    from academicwarning import api
    return TestClient(api.app)


def _wait_done(db_path, file_type, grade, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        db = WarningDB(db_path)
        try:
            sf = db.latest_source_file(file_type, grade)
        finally:
            db.close()
        if sf and sf.parsed_status == "done":
            return sf
        time.sleep(0.2)
    raise AssertionError(f"{file_type}({grade}) 解析超时")


def test_upload_roster_module_parsed(client, api_env, tmp_path):
    """学籍名单模块上传（type_hint=学籍名单）→ 后台解析入库 + note 写回；
    /status roster 分组一行（majors=[]，同 selection 模式）。"""
    p = _roster_file(tmp_path, "2023级学籍信息.xls", [
        ("2023001", "张三", "2023级", "0824工商管理", "工商2301", "是", "是", "正常"),
        ("2023002", "李四", "2023级", "0826工业工程", "工业工程2301", "是", "是", "正常")])
    with open(p, "rb") as fh:
        r = client.post("/api/warning/upload",
                        files={"file": ("2023级学籍信息.xls", fh,
                                        "application/octet-stream")},
                        data={"type_hint": "学籍名单", "grade_hint": "2023级"},
                        headers={"X-API-Key": TEST_KEY})
    body = r.json()
    assert body["parsed_status"] == "parsing"
    assert body["file_type"] == "roster"
    sf = _wait_done(api_env, "roster", "2023级")
    # 后台线程入库后写回 note（与 parsed_status=done 间有微秒窗口，轮询等待）
    deadline = time.time() + 5.0
    while time.time() < deadline and not (sf.in_file_meta.get("note") or "").startswith("学籍名单入库"):
        db = WarningDB(api_env)
        try:
            sf = db.latest_source_file("roster", "2023级")
        finally:
            db.close()
        time.sleep(0.2)
    assert (sf.in_file_meta.get("note") or "").startswith("学籍名单入库")
    db = WarningDB(api_env)
    try:
        assert sorted(s.student_id for s in db.latest_roster("2023级")) == [
            "2023001", "2023002"]
    finally:
        db.close()
    # /status：roster 组一行 majors=[]（同 selection）
    files = client.get("/api/warning/status",
                       headers={"X-API-Key": TEST_KEY}).json()["files"]
    row = next(f for f in files if f["file_type"] == "roster")
    assert row["type_name"] == "学籍名单" and row["majors"] == []
    assert row["items"][0]["file_name"] == "2023级学籍信息.xls"


def test_upload_roster_wrong_grade_rejected(client, api_env, tmp_path):
    """v6.5 防传错：学籍文件主年级 2023级 传 2024级 入口 → 同步拒绝（同 selection 文案）。"""
    p = _roster_file(tmp_path, "学籍信息.xls", [
        ("2023001", "张三", "2023级", "0824工商管理", "工商2301", "是", "是", "正常")])
    with open(p, "rb") as fh:
        r = client.post("/api/warning/upload",
                        files={"file": ("学籍信息.xls", fh, "application/octet-stream")},
                        data={"type_hint": "学籍名单", "grade_hint": "2024级"},
                        headers={"X-API-Key": TEST_KEY})
    body = r.json()
    assert body["parsed_status"] == "rejected"
    assert "2023级" in body["message"] and "不匹配" in body["message"]
    db = WarningDB(api_env)
    try:
        assert db.latest_source_file("roster", "2024级") is None
    finally:
        db.close()


def test_selection_check_response_not_selected(client, api_env):
    """GET /selection-check：run 后 not_selected/not_selected_count 读选课文件 meta；
    students 只含参与检查（有选课）的学生。"""
    db = WarningDB(api_env)
    try:
        _seed_check(db)
    finally:
        db.close()
    r = client.post("/api/warning/selection-check/run", json={"grade": "2023级"},
                    headers={"X-API-Key": TEST_KEY})
    assert r.json()["count"] == 1
    d = client.get("/api/warning/selection-check?grade=2023级",
                   headers={"X-API-Key": TEST_KEY}).json()
    assert d["not_selected_count"] == 1
    assert d["not_selected"] == [{"student_id": "S2", "name": "乙",
                                  "major": "工商管理", "class_name": "工商2301"}]
    assert [s["student_id"] for s in d["students"]] == ["S1"]
    assert "乙" not in [s["name"] for s in d["students"]]


def test_selection_check_empty_not_selected_fields(client, api_env):
    """未跑过检查（无选课文件）→ not_selected=[] / count=0（空态不报错）。"""
    d = client.get("/api/warning/selection-check?grade=2023级",
                   headers={"X-API-Key": TEST_KEY}).json()
    assert d["not_selected"] == [] and d["not_selected_count"] == 0
