"""precheck_selection 数据齐全性预检测试（2026-09-22，对话触发路径）。"""
from academicwarning.db import WarningDB
from academicwarning.models import SourceFile
from academicwarning.service import STANDARD_MAJORS, precheck_selection


def _db(tmp_path):
    db = WarningDB(str(tmp_path / "t.db"))
    db.init_schema()
    return db


def _sf(db, ftype, grade, major=None, status="done", name=None):
    meta = {"major": major} if major else {}
    return db.insert_source_file(SourceFile(
        file_type=ftype, file_name=name or f"{ftype}.bin",
        file_hash=f"{ftype}-{grade}-{major}", file_path="/x",
        upload_time="2026-09-22 10:00:00", uploader="t",
        parsed_status=status, in_file_meta=meta, grade=grade))


def test_precheck_empty_db_lists_all_missing(tmp_path):
    """空库：选课结果/学籍名单/培养方案/成绩单 均报缺；通识表为可选不算缺。"""
    db = _db(tmp_path)
    ready, pending, text = precheck_selection(db=db, grade="2023级")
    assert ready is False and pending is False
    assert "选课结果：✗" in text
    assert "学籍名单：✗" in text
    assert "培养方案：✗" in text and "缺" in text
    assert "成绩单：✗" in text
    assert "通识课程信息表：✓" in text   # 可选，缺失不阻断
    db.close()


def test_precheck_ready_when_all_present(tmp_path):
    """四类必需数据齐备 → ready=True。"""
    db = _db(tmp_path)
    _sf(db, "selection", "2023级")
    _sf(db, "roster", "2023级")
    for m in STANDARD_MAJORS:
        _sf(db, "plan", "2023级", major=m)
        _sf(db, "grade", "2023级", major=m)
    ready, pending, text = precheck_selection(db=db, grade="2023级")
    assert ready is True and pending is False
    assert "数据齐备" in text
    assert "培养方案：✓ 4 专业齐" in text
    assert "成绩单：✓ 4 专业齐" in text
    db.close()


def test_precheck_partial_major_gap_named(tmp_path):
    """成绩单只传 3 个专业 → 点名缺哪个专业；ready=False。"""
    db = _db(tmp_path)
    _sf(db, "selection", "2023级")
    _sf(db, "roster", "2023级")
    for m in STANDARD_MAJORS:
        _sf(db, "plan", "2023级", major=m)
    for m in STANDARD_MAJORS[:3]:
        _sf(db, "grade", "2023级", major=m)
    ready, pending, text = precheck_selection(db=db, grade="2023级")
    assert ready is False and pending is False
    assert f"缺 {STANDARD_MAJORS[3]}" in text
    db.close()


def test_precheck_parsing_file_not_counted(tmp_path):
    """解析中（queued）的文件不算齐备，且 pending=True（区别于真缺失）。"""
    db = _db(tmp_path)
    _sf(db, "selection", "2023级", status="queued")
    _sf(db, "roster", "2023级")
    ready, pending, text = precheck_selection(db=db, grade="2023级")
    assert ready is False and pending is True
    assert "选课结果：✗" in text and "解析中" in text
    db.close()


def test_precheck_failed_is_not_pending(tmp_path):
    """解析失败（failed）归入"真缺失"类 → pending=False（应让管理员补传）。"""
    db = _db(tmp_path)
    _sf(db, "selection", "2023级", status="failed")
    _sf(db, "roster", "2023级")
    ready, pending, _text = precheck_selection(db=db, grade="2023级")
    assert ready is False and pending is False
    db.close()


def test_precheck_is_readonly(tmp_path):
    """预检只读：不写库（source_file 行数不变）。"""
    db = _db(tmp_path)
    _sf(db, "selection", "2023级")
    before = db.conn.execute("SELECT COUNT(*) FROM source_file").fetchone()[0]
    precheck_selection(db=db, grade="2023级")
    after = db.conn.execute("SELECT COUNT(*) FROM source_file").fetchone()[0]
    assert before == after
    db.close()
