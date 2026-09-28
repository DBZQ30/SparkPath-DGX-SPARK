"""SQLite 存储层测试（临时库）。"""
import pytest

from academicwarning.db import WarningDB
from academicwarning.models import (SourceFile, Selection, Grade, SelectionCheckRow)


@pytest.fixture
def db(tmp_path):
    d = WarningDB(str(tmp_path / "warning.db"))
    d.init_schema()
    yield d
    d.close()


def test_schema_user_version(db):
    row = db.conn.execute("PRAGMA user_version").fetchone()
    assert row[0] == 7  # v7: selection_check 补 details（豁免；v6 为四表 grade 列）


def test_insert_and_latest_source_file(db):
    fid = db.insert_source_file(SourceFile(
        file_type="selection", file_name="a.xlsx", file_hash="h1",
        file_path="/x/a.xlsx", upload_time="2026-08-19 10:00:00", uploader="admin",
    ))
    assert fid > 0
    latest = db.latest_source_file("selection")
    assert latest is not None and latest.file_name == "a.xlsx"


def test_plan_versioning(db):
    pid1 = db.insert_plan_meta("0824工商管理", "v1", "p1.docx", "2026-08-01 10:00:00")
    pid2 = db.insert_plan_meta("0824工商管理", "v2", "p2.docx", "2026-08-20 10:00:00")
    active = db.latest_active_plans()
    assert active["0824工商管理"] == pid2
    # 旧版标记历史
    row = db.conn.execute(
        "SELECT is_active FROM training_plan WHERE id=?", (pid1,)
    ).fetchone()
    assert row[0] == 0


def test_file_hash_dedup(db):
    sf = SourceFile(file_type="selection", file_name="a.xlsx", file_hash="same",
                    file_path="/x/a.xlsx", upload_time="2026-08-19", uploader="admin")
    db.insert_source_file(sf)
    hit = db.file_hash_exists("same", "selection")
    assert hit is not None
    assert db.file_hash_exists("other", "selection") is None


def _sel(sid, code, credit, fid):
    return Selection(student_id=sid, semester_label="2026-2027学年 第一学期",
                     semester_code="4-1", course_code=code, course_name=f"课{code}",
                     credit=credit, nature="必修", category="专业核心课程",
                     status="选中", retake="初修", source_file_id=fid)


def test_refresh_selection_credits(db):
    """I8：按最新方案刷新指定选课文件的学分（方案后传补链）。"""
    db.insert_selections([_sel("S1", "C001", 0.0, 5), _sel("S1", "C002", 3.0, 5)])
    db.refresh_selection_credits(5, {"C001": 4.0, "C002": 2.0})
    creds = dict(db.conn.execute(
        "SELECT course_code, credit FROM selection").fetchall())
    assert creds == {"C001": 4.0, "C002": 2.0}


def _orphans(db):
    """各子表中 source_file_id 已不存在于 source_file 的行数（孤儿残留）。"""
    out = {}
    for t in ("roster", "grade", "selection", "selection_check"):
        out[t] = db.conn.execute(
            f"SELECT COUNT(*) FROM {t} WHERE source_file_id NOT IN"
            " (SELECT id FROM source_file)").fetchone()[0]
    return out


def test_delete_major_files_cascades_all_child_tables(db):
    """v1.9.1：删除 source_file 须级联清空全部按 source_file_id 关联的子表
    （grade/selection/selection_check/roster），杜绝孤儿残留。

    回归：此前只级联 roster/plan → 删成绩单后 grade 表留下 32689 行孤儿。"""
    def _sf(ftype, grade):
        return db.insert_source_file(SourceFile(
            file_type=ftype, file_name=f"{ftype}.bin", file_hash=f"{ftype}-{grade}",
            file_path="/x", upload_time="2026-09-22 00:00:00", uploader="t",
            parsed_status="done", grade=grade))

    sf_grade = _sf("grade", "2023级")
    sf_grade_keep = _sf("grade", "2024级")
    sf_sel = _sf("selection", "2023级")
    sf_roster = _sf("roster", "2023级")

    db.insert_grades([Grade(student_id="S1", course_name="高数", grade_raw="85",
                            source_file_id=sf_grade),
                      Grade(student_id="S2", course_name="高数", grade_raw="85",
                            source_file_id=sf_grade_keep)])
    db.insert_selections([_sel("S1", "C001", 3.0, sf_sel)])
    db.insert_roster([{"student_id": "S1", "name": "甲", "grade": "2023级",
                       "major": "工商管理", "status": "正常",
                       "source_file_id": sf_roster}])
    db.insert_selection_check_rows([SelectionCheckRow(
        source_file_id=sf_sel, student_id="S1", checked_at="2026-09-22 00:00:00")])
    assert _orphans(db) == {"roster": 0, "grade": 0, "selection": 0,
                            "selection_check": 0}

    # 删 2023级 成绩单 → 该文件成绩行级联清空，2024级 保留
    db.delete_major_files("grade", grade="2023级")
    assert db.conn.execute(
        "SELECT COUNT(*) FROM grade WHERE source_file_id=?", (sf_grade,)
    ).fetchone()[0] == 0
    assert db.conn.execute(
        "SELECT COUNT(*) FROM grade WHERE source_file_id=?", (sf_grade_keep,)
    ).fetchone()[0] == 1

    # 删选课文件 → selection + selection_check 级联
    db.delete_major_files("selection", grade="2023级")
    # 删 roster 文件 → roster 级联
    db.delete_major_files("roster", grade="2023级")
    assert _orphans(db) == {"roster": 0, "grade": 0, "selection": 0,
                            "selection_check": 0}


def test_delete_major_files_plan_cascades_training_plan(db):
    """plan 级联（此前零覆盖）：删 plan 类型 source_file 时按 major_name 删
    training_plan + plan_course/plan_semester_course（含 is_active=0 历史版，
    该专业全版本）；未指定 major 时专业名从 in_file_meta 提取；
    grade 分区只删该年级；其他专业不受影响。"""
    from academicwarning.models import PlanCourse, PlanSemesterCourse

    def _plan_fid(major, grade, h):
        return db.insert_source_file(SourceFile(
            file_type="plan", file_name=f"{major}.docx", file_hash=h,
            file_path="/x", upload_time="2026-09-26 00:00:00", uploader="t",
            parsed_status="done", in_file_meta={"major": major}, grade=grade))

    # 工商管理 两个年级分区各一版方案 + 每版挂课程与推荐课表
    f_g23 = _plan_fid("工商管理", "2023级", "p23")
    f_g24 = _plan_fid("工商管理", "2024级", "p24")
    pids = {}
    for grade in ("2023级", "2024级"):
        pid = db.insert_plan_meta("工商管理", "v1", "p.docx",
                                  "2026-09-26 00:00:00", grade=grade)
        db.insert_plan_courses(pid, [PlanCourse(
            plan_id=pid, course_code=f"EL-{grade}", course_name="选修课",
            credit=2.0, course_type="专业选修课程", required_flag="选修",
            semester="3-1")])
        db.insert_plan_semester_courses(pid, [PlanSemesterCourse(
            plan_id=pid, semester="3-1", course_code=f"EL-{grade}",
            course_name="选修课", credit=2.0)])
        pids[grade] = pid
    # 会计学 其他专业方案（不受影响的对照组）
    pid_acc = db.insert_plan_meta("会计学", "v1", "a.docx",
                                  "2026-09-26 00:00:00", grade="2023级")
    db.insert_plan_courses(pid_acc, [PlanCourse(
        plan_id=pid_acc, course_code="ACC1", course_name="会计课",
        credit=2.0, course_type="专业核心课程", required_flag="必修",
        semester="1-1")])

    # 未指定 major（专业名从 in_file_meta 提取）+ grade=2023级 分区：
    # 只删工商 2023级 方案（含 plan_course / plan_semester_course），2024级 与会计保留
    deleted = db.delete_major_files("plan", grade="2023级")
    assert [d.id for d in deleted] == [f_g23]
    assert db.conn.execute(
        "SELECT COUNT(*) FROM plan_course WHERE plan_id=?", (pids["2023级"],)
    ).fetchone()[0] == 0
    assert db.conn.execute(
        "SELECT COUNT(*) FROM plan_semester_course WHERE plan_id=?",
        (pids["2023级"],)).fetchone()[0] == 0
    assert db.conn.execute(
        "SELECT COUNT(*) FROM training_plan WHERE id=?", (pids["2023级"],)
    ).fetchone()[0] == 0
    for kept in (pids["2024级"], pid_acc):
        assert db.conn.execute(
            "SELECT COUNT(*) FROM plan_course WHERE plan_id=?", (kept,)
        ).fetchone()[0] == 1

    # 指定 major 删除该专业全部年级分区（grade=None = 全删，兼容旧调用）
    db.delete_major_files("plan", major="工商管理")
    assert db.conn.execute(
        "SELECT COUNT(*) FROM training_plan WHERE major_name=?",
        ("工商管理",)).fetchone()[0] == 0
    assert db.conn.execute(
        "SELECT COUNT(*) FROM training_plan WHERE id=?", (pid_acc,)
    ).fetchone()[0] == 1   # 会计学不受影响


def test_queued_placeholder_lifecycle(db):
    """v1.10：insert_queued_source_file → /status 可见 queued；
    finish_source_file 原地收尾为 done（同一 id，不新增）；file_hash 回填。"""
    sf_id = db.insert_queued_source_file(SourceFile(
        file_type="grade", file_name="工商2301成绩单.docx", file_hash="",
        file_path="/x", upload_time="2026-09-22 10:00:00", uploader="api-admin",
        in_file_meta={"major": "工商管理"}, grade="2023级"))
    row = db.latest_source_file("grade", "2023级")
    assert row.id == sf_id and row.parsed_status == "queued"
    assert row.file_name == "工商2301成绩单.docx"

    db.finish_source_file(sf_id, "done",
                          in_file_meta={"major": "工商管理", "note": "入库"},
                          file_hash="deadbeef")
    row = db.latest_source_file("grade", "2023级")
    assert row.id == sf_id                      # 原地收尾，不新增
    assert row.parsed_status == "done"
    assert row.file_hash == "deadbeef"
    assert row.in_file_meta.get("note") == "入库"
    n = db.conn.execute("SELECT COUNT(*) FROM source_file").fetchone()[0]
    assert n == 1


def test_fail_stale_queued(db):
    """v1.10：启动清理把遗留 queued 行标记 failed（解析被中断）；done 行不受影响。"""
    q = db.insert_queued_source_file(SourceFile(
        file_type="grade", file_name="q.docx", file_hash="", file_path="/x",
        upload_time="2026-09-22 10:00:00", uploader="api-admin", grade="2023级"))
    d = db.insert_source_file(SourceFile(
        file_type="plan", file_name="p.docx", file_hash="h", file_path="/x",
        upload_time="2026-09-22 10:00:00", uploader="api-admin",
        parsed_status="done", grade="2023级"))
    assert db.fail_stale_queued() == 1
    assert db.latest_source_file("grade", "2023级").parsed_status == "failed"
    assert db.latest_source_file("plan", "2023级").parsed_status == "done"
    assert db.conn.execute("SELECT COUNT(*) FROM source_file WHERE id=?",
                           (d,)).fetchone()[0] == 1


def test_grade_covered_semester_ignores_queued(db):
    """v1.10：grade_covered_semester 须忽略 queued/failed 占位行——
    占位行子表为空，误当有效成绩会算出"无覆盖"（假阴性）。"""
    from academicwarning.service import grade_covered_semester
    # 只有一条 queued 占位（无成绩行）→ 覆盖学期应为空，而非报错/误判
    db.insert_queued_source_file(SourceFile(
        file_type="grade", file_name="工商成绩单.docx", file_hash="",
        file_path="/x", upload_time="2026-09-22 10:00:00", uploader="api-admin",
        in_file_meta={"major": "工商管理"}, grade="2023级"))
    auto, covered = grade_covered_semester(db, "2023级")
    assert auto == "" and covered == ""


def test_grade_master_add_delete(db):
    """年级注册表：可增可删；删除幂等（不存在返回 False）。"""
    db.add_grade("2027级")
    assert "2027级" in db.list_grades()
    assert db.delete_grade("2027级") is True
    assert "2027级" not in db.list_grades()
    assert db.delete_grade("2027级") is False

