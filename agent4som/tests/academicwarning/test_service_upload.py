"""上传编排测试。"""
import os
import pytest

from academicwarning.db import WarningDB
from academicwarning.service import detect_file_type, is_admin

SEL = "academicwarning/docs/2023级26-27学年第一学期的选课结果.xlsx"
STATUS = "academicwarning/docs/2023级学籍异动详情.xls"


@pytest.fixture
def db(tmp_path):
    d = WarningDB(str(tmp_path / "warning.db"))
    d.init_schema()
    yield d
    d.close()


@pytest.mark.samples
def test_detect_file_type_by_content():
    assert detect_file_type(SEL) == "selection"
    # 2026-08-31 精简：仅三类文件（方案/选课/成绩单）


def test_detect_by_filename():
    assert detect_file_type("/tmp/培养方案-2023版工商管理.docx") == "plan"
    assert detect_file_type("/tmp/学生成绩单.docx") == "grade"



def test_is_admin_defaults_false():
    # 无 roles.json 映射的未知用户 → resolve_role 返回默认 student → False（确定性断言）
    assert not is_admin("wecom", "definitely-not-a-mapped-user")


@pytest.mark.samples
def test_upload_two_grade_files_both_kept(tmp_path, db, monkeypatch):
    """H1：两份不同专业成绩单连续上传后，两份数据均保留。"""
    import academicwarning.service as svc
    monkeypatch.setattr(svc, "is_admin", lambda p, u: True)
    g1 = "academicwarning/docs/2023级工商管理成绩单.docx"
    g2 = "academicwarning/docs/2023级工业工程成绩单.docx"
    if not (os.path.exists(g1) and os.path.exists(g2)):
        pytest.skip("样本缺失")
    # 用 monkeypatch 让 parse_grades 返回固定学生，避免真实 OCR
    from academicwarning.models import Grade
    fake = lambda path: ([("S1", "甲", [Grade(student_id="S1", course_name="高等数学I-2",
                                             credit=6.5, grade_raw="80", pass_flag=1)])], [])
    monkeypatch.setattr(svc, "parse_grades", fake)
    svc.upload_file(g1, uploader="admin", platform="wecom", db=db)
    svc.upload_file(g2, uploader="admin", platform="wecom", db=db)
    assert len(db.get_grades(1)) == 1
    assert len(db.get_grades(2)) == 1   # 两份都在


@pytest.mark.samples
def test_failed_parse_can_retry_upload(tmp_path, db, monkeypatch):
    """I1：解析失败的记录不参与判重，修复后可重传入库。"""
    import academicwarning.service as svc
    monkeypatch.setattr(svc, "is_admin", lambda p, u: True)
    g = "academicwarning/docs/2023级工商管理成绩单.docx"
    if not os.path.exists(g):
        pytest.skip("样本缺失")

    def boom(path):
        raise RuntimeError("OCR 服务不可用")
    monkeypatch.setattr(svc, "parse_grades", boom)
    r1 = svc.upload_file(g, uploader="admin", platform="wecom", db=db)
    assert "解析失败" in r1
    failed = db.latest_source_file("grade")
    assert failed is not None and failed.parsed_status == "failed"

    from academicwarning.models import Grade
    monkeypatch.setattr(svc, "parse_grades", lambda path: (
        [("S1", "甲", [Grade(student_id="S1", course_name="高等数学I-2",
                             credit=6.5, grade_raw="80", pass_flag=1)])], []))
    r2 = svc.upload_file(g, uploader="admin", platform="wecom", db=db)
    assert "成绩单入库" in r2           # 修复前：被判重拦截"已上传过"
    assert len(db.get_grades(2)) == 1   # 重传成功，成绩落库


def test_missing_file_friendly_reply(tmp_path, db, monkeypatch):
    """M5 兜底：文件不存在时友好回复而非 traceback，failed 记录入库。"""
    import academicwarning.service as svc
    monkeypatch.setattr(svc, "is_admin", lambda p, u: True)
    r = svc.upload_file("/tmp/不存在的选课结果-2026-2027学年.xlsx",
                        uploader="admin", platform="wecom", db=db)
    assert "解析失败" in r and "FileNotFoundError" in r
    sf = db.latest_source_file("selection")   # 文件名识别出类型，md5 读取失败
    assert sf is not None and sf.parsed_status == "failed"
