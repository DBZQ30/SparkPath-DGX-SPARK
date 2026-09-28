"""学业预警 HTTP API 测试（007 设计，TestClient）。"""
import os
import time

import pytest
from fastapi.testclient import TestClient

from academicwarning import api
from academicwarning.db import WarningDB

# 鉴权：测试用固定 key（monkeypatch 模块级 _API_KEY）
TEST_KEY = "test-key-123"


def _wait_parsed(client, file_type, major=None, timeout=15.0):
    """轮询 /status 直到指定类型（可按 major 过滤）出现 done/failed 记录。

    v1.7 嵌套结构：files[].items[] 内查找；done 记录还需 note 非空（线程写回，微秒级窗口）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get("/api/warning/status", headers={"X-API-Key": TEST_KEY})
        assert r.status_code == 200
        for f in r.json()["files"]:
            if f["file_type"] != file_type:
                continue
            for it in f["items"]:
                if major is not None and it["major"] != major:
                    continue
                if it["parsed_status"] == "failed":
                    return it
                if it["parsed_status"] == "done" and it.get("note"):
                    return it
        time.sleep(0.2)
    raise AssertionError(f"{file_type} 解析超时（后台线程未完成）")


@pytest.fixture(autouse=True)
def _key(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_API_KEY", TEST_KEY)
    # 数据目录隔离到临时目录
    monkeypatch.setattr(api, "_UPLOAD_DIR", tmp_path / "uploads")
    # db 隔离：api 与 service 的 WarningDB 均指向临时库（防读到真实 data/warning.db）
    db_path = str(tmp_path / "warning.db")
    import academicwarning.service as svc
    monkeypatch.setattr(api, "WarningDB", lambda: WarningDB(db_path))
    monkeypatch.setattr(svc, "WarningDB", lambda: WarningDB(db_path))
    # 临时库初始化 schema（lifespan 只初始化真实库）
    _db = WarningDB(db_path)
    try:
        _db.init_schema()
    finally:
        _db.close()
    yield


@pytest.fixture
def client():
    return TestClient(api.app)


def test_detect_major_canonical():
    """v1.7 专业识别：plan 正文/文件名、grade 文件名；无法识别 → None（信任所选专业）。"""
    from academicwarning.parsers import canonical_major, detect_major
    assert canonical_major("2023级工商管理成绩单.docx") == "工商管理"
    assert canonical_major("成绩单.docx") is None        # 无专业词 → 未识别（不误拒改名文件）
    assert canonical_major("会计学") == "会计学（ACCA）"  # 归一化
    src = "academicwarning/docs/2023版工商管理专业培养方案.docx"
    if os.path.exists(src):
        assert detect_major(src, "plan") == "工商管理"
    assert detect_major("无专业词.docx", "grade") is None
    assert detect_major("无专业词.docx", "plan") is None
    assert detect_major("无专业词.docx", "selection") is None


def test_unauthorized(client):
    r = client.get("/api/warning/status")
    assert r.status_code == 401
    r = client.get("/api/warning/status", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401
    r = client.get("/api/warning/selection-check")
    assert r.status_code == 401
    r = client.post("/api/warning/selection-check/run")
    assert r.status_code == 401


def test_selection_check_empty_state(client):
    """选课检查空态：无选课文件 → source_file_id=null、students=[]。"""
    r = client.get("/api/warning/selection-check", headers={"X-API-Key": TEST_KEY})
    assert r.status_code == 200
    data = r.json()
    assert data["source_file_id"] is None
    assert data["students"] == []
    assert data["summary"] == ""


def test_selection_check_after_upload(client, tmp_path):
    """上传选课 → 自动检查落库 → GET 名单 + POST 重跑。"""
    # 预置：方案（工商，含 1 门专业选修）+ 名单 + 成绩单（S1 修过选修）
    db = WarningDB(str(tmp_path / "warning.db"))
    from academicwarning.models import PlanCourse, SourceFile, Grade
    db.insert_source_file(SourceFile(
        file_type="plan", file_name="p.docx", file_hash="h1", file_path="",
        upload_time="2026-08-27 12:00:00", uploader="admin",
        in_file_meta={"major": "工商管理"}))
    pid = db.insert_plan_meta("工商管理", "v1", "p.docx", "2026-08-01")
    db.insert_plan_courses(pid, [
        PlanCourse(plan_id=pid, course_code="EL1", course_name="选修课A", credit=2.0,
                   course_type="专业选修课程", required_flag="选修", semester="3-1"),
        PlanCourse(plan_id=pid, course_code="EL2", course_name="选修课B", credit=2.0,
                   course_type="专业选修课程", required_flag="选修", semester="3-2"),
    ])
    gid = db.insert_source_file(SourceFile(
        file_type="grade", file_name="g.docx", file_hash="h2", file_path="",
        upload_time="2026-08-27 12:00:00", uploader="admin",
        in_file_meta={"major": "工商管理"}))
    # S2 修过两门选修（应累计不被豁免）；S1 只修 EL1 → 差 2.0 → 触发
    # 2026-08-31 JSON 口径：两人各补 6 门 ◆通识课（模块课程应 12 达标，不触发模块）
    from academicwarning.parsers import clean_course_name
    grades = [
        Grade(student_id="S2", student_name="乙", course_name="选修课A", credit=2.0,
              grade_raw="85", pass_flag=1, course_name_clean="选修课A",
              source_file_id=gid),
        Grade(student_id="S2", student_name="乙", course_name="选修课B", credit=2.0,
              grade_raw="85", pass_flag=1, course_name_clean="选修课B",
              source_file_id=gid),
        Grade(student_id="S1", student_name="甲", course_name="选修课A", credit=2.0,
              grade_raw="85", pass_flag=1, course_name_clean="选修课A",
              source_file_id=gid),
    ]
    for _i, sid_ in enumerate(("S1", "S2")):
        for j in range(6):
            grades.append(Grade(
                student_id=sid_, student_name=sid_, course_name=f"通识课{j}◆",
                credit=2.0, grade_raw="80", pass_flag=1, marker="◆选修课",
                course_name_clean=clean_course_name(f"通识课{j}◆"),
                source_file_id=gid))
    db.insert_grades(grades)
    db.close()

    # 上传选课文件（S1 无选课、S2 无选课——用简单 xlsx 内容？直接构造 selection 记录）
    # 简化：直接通过 _upload_impl 的 db 注入路径不可行，这里手动插 selection + 触发检查
    db = WarningDB(str(tmp_path / "warning.db"))
    from academicwarning.models import Selection
    sid = db.insert_source_file(SourceFile(
        file_type="selection", file_name="s.xlsx", file_hash="h3", file_path="",
        upload_time="2026-08-31 12:00:00", uploader="admin",
        in_file_meta={"semester_label": "2026-2027学年 第一学期",
                      "semester_code": "4-1", "grades": ["2023级"]}))
    # 名单 = 选课结果中的 distinct 学生（2026-08-31 精简）——selection 带 name/major
    db.insert_selections([
        Selection(student_id="S1", name="甲", major="0824工商管理",
                  semester_label="2026-2027学年 第一学期",
                  semester_code="4-1", course_code="C1", course_name="公共课",
                  credit=2.0, nature="必修", category="公共课程", status="选中",
                  source_file_id=sid),
        Selection(student_id="S2", name="乙", major="0824工商管理",
                  semester_label="2026-2027学年 第一学期",
                  semester_code="4-1", course_code="C1", course_name="公共课",
                  credit=2.0, nature="必修", category="公共课程", status="选中",
                  source_file_id=sid),
    ])
    db.close()

    # v6.5：名单 = 学籍 roster（该 run 不带 grade，roster 文件亦按空年级分区落库）
    db = WarningDB(str(tmp_path / "warning.db"))
    from academicwarning.models import SourceFile as _SF
    rid = db.insert_source_file(_SF(
        file_type="roster", file_name="r.xls", file_hash="hr", file_path="",
        upload_time="2026-08-31 12:00:00", uploader="admin"))
    db.insert_roster([
        {"student_id": "S1", "name": "甲", "grade": "2023级", "major": "工商管理",
         "class_name": "工商2301", "status": "正常", "source_file_id": rid},
        {"student_id": "S2", "name": "乙", "grade": "2023级", "major": "工商管理",
         "class_name": "工商2301", "status": "正常", "source_file_id": rid},
    ])
    db.close()

    # 手动跑检查（模拟上传后自动触发；锁内路径由 service 层测试覆盖）
    from academicwarning.service import run_selection_check
    n, note = run_selection_check()
    assert n == 2 and "选课不合理 2 人" in note

    r = client.get("/api/warning/selection-check", headers={"X-API-Key": TEST_KEY})
    assert r.status_code == 200
    data = r.json()
    assert data["source_file_id"] == sid
    # 2026-08-31 JSON 口径：专业选修应修 = 8（毕业要求）——S1 差 6、S2 差 4 均触发
    assert len(data["students"]) == 2
    by_id = {s["student_id"]: s for s in data["students"]}
    s1 = by_id["S1"]
    assert s1["expected_credit"] == 8.0 and s1["gained_credit"] == 2.0 and s1["gap"] == 6.0

    r2 = client.post("/api/warning/selection-check/run", headers={"X-API-Key": TEST_KEY})
    assert r2.status_code == 200
    assert "选课检查" in r2.json()["message"]
    # 多次 run 后名单仍只返回最新一批（不重复）
    r3 = client.post("/api/warning/selection-check/run", headers={"X-API-Key": TEST_KEY})
    assert r3.status_code == 200
    r4 = client.get("/api/warning/selection-check", headers={"X-API-Key": TEST_KEY})
    sids = [s["student_id"] for s in r4.json()["students"]]
    assert len(sids) == len(set(sids)) and len(sids) == 2


@pytest.mark.samples
def test_upload_wrong_type_rejected(client, tmp_path):
    """类型强校验：在"选课结果"模块上传成绩单文件 → 拒绝（传错防呆）。"""
    src = "academicwarning/docs/2023级工商管理成绩单.docx"
    if not os.path.exists(src):
        pytest.skip("样本缺失")
    with open(src, "rb") as f:
        r = client.post(
            "/api/warning/upload",
            files={"file": ("成绩单.docx", f, "application/octet-stream")},
            data={"type_hint": "选课结果"},
            headers={"X-API-Key": TEST_KEY},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["parsed_status"] == "rejected"
    assert "不匹配" in body["message"]
    assert "选课结果" in body["message"] and "成绩单" in body["message"]


@pytest.mark.samples
def test_upload_correct_type_async_parsed(client, tmp_path):
    """培养方案模块上传培养方案文件 → 立即返回 parsing，轮询 status 至 done。

    验证异步契约 + v1.7 嵌套结构：parsing 返回；status items 中工商行 done + note；
    majors 为标准 4 专业名单。"""
    src = "academicwarning/docs/2023版工商管理专业培养方案.docx"
    if not os.path.exists(src):
        pytest.skip("样本缺失")
    with open(src, "rb") as f:
        r = client.post(
            "/api/warning/upload",
            files={"file": ("方案.docx", f, "application/octet-stream")},
            data={"type_hint": "培养方案", "major_hint": "工商管理"},
            headers={"X-API-Key": TEST_KEY},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["parsed_status"] == "parsing"
    assert body["file_type"] == "plan"
    assert body["major"] == "工商管理"

    sf = _wait_parsed(client, "plan", major="工商管理")
    assert sf["parsed_status"] == "done"
    assert sf["id"] and sf["file_name"] and sf["upload_time"]
    assert "培养方案入库" in (sf["note"] or "")

    # v1.7 结构断言：majors 名单 + 标准 4 行占位（其余 3 专业 file_name 为空）
    files = client.get("/api/warning/status",
                       headers={"X-API-Key": TEST_KEY}).json()["files"]
    plan = next(f for f in files if f["file_type"] == "plan")
    assert plan["majors"] == ["工商管理", "大数据管理与应用", "工业工程", "会计学（ACCA）"]
    assert len(plan["items"]) == 4
    by_major = {it["major"]: it for it in plan["items"]}
    assert by_major["工商管理"]["parsed_status"] == "done"
    for m in ("大数据管理与应用", "工业工程", "会计学（ACCA）"):
        assert by_major[m]["file_name"] is None


@pytest.mark.samples
def test_upload_duplicate_dedup_no_reparse(client, tmp_path):
    """同内容重复上传 → 判重直接返回 done（已上传过），不重复启动解析。"""
    src = "academicwarning/docs/2023版工商管理专业培养方案.docx"
    if not os.path.exists(src):
        pytest.skip("样本缺失")

    def _upload():
        with open(src, "rb") as f:
            r = client.post(
                "/api/warning/upload",
                files={"file": ("方案.docx", f, "application/octet-stream")},
                data={"type_hint": "培养方案"},
                headers={"X-API-Key": TEST_KEY},
            )
        assert r.status_code == 200
        return r.json()

    assert _upload()["parsed_status"] == "parsing"
    _wait_parsed(client, "plan", major="工商管理")
    body = _upload()
    assert body["parsed_status"] == "done"
    assert "已上传过" in body["message"]


@pytest.mark.samples
def test_upload_orig_name_stored(client, tmp_path):
    """v1.8：orig_name 入库（微信上传临时名场景），响应回传 file_name 供轮询匹配。"""
    src = "academicwarning/docs/2023版工商管理专业培养方案.docx"
    if not os.path.exists(src):
        pytest.skip("样本缺失")
    with open(src, "rb") as f:
        r = client.post(
            "/api/warning/upload",
            files={"file": ("微信临时名5F3k.docx", f, "application/octet-stream")},
            data={"type_hint": "培养方案", "major_hint": "工商管理",
                  "orig_name": "2023版工商管理专业培养方案.docx"},
            headers={"X-API-Key": TEST_KEY},
        )
    body = r.json()
    assert body["parsed_status"] == "parsing"
    assert body["file_name"] == "2023版工商管理专业培养方案.docx"
    sf = _wait_parsed(client, "plan", major="工商管理")
    assert sf["file_name"] == "2023版工商管理专业培养方案.docx"   # 原名入库，非临时名


@pytest.mark.samples
def test_major_mismatch_rejected(client, tmp_path):
    """v1.7 专业强校验：工商方案传会计学ACCA 入口 → 同步拒绝（防传错升级）。"""
    src = "academicwarning/docs/2023版工商管理专业培养方案.docx"
    if not os.path.exists(src):
        pytest.skip("样本缺失")
    with open(src, "rb") as f:
        r = client.post(
            "/api/warning/upload",
            files={"file": ("方案.docx", f, "application/octet-stream")},
            data={"type_hint": "培养方案", "major_hint": "会计学（ACCA）"},
            headers={"X-API-Key": TEST_KEY},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["parsed_status"] == "rejected"
    assert "工商管理" in body["message"] and "不匹配" in body["message"]
    # 落盘文件已清理，库中无记录
    db = WarningDB(str(tmp_path / "warning.db"))
    assert db.latest_source_file("plan") is None
    db.close()


@pytest.mark.samples
def test_delete_file_and_fallback(client, tmp_path):
    """v1.7 删除端点：按专业删除 → 该专业回未上传占位、plan 级联清空、
    触发被拒、重复删除提示、plan/grade 缺 major 拒绝、非法类型。"""
    src = "academicwarning/docs/2023版工商管理专业培养方案.docx"
    if not os.path.exists(src):
        pytest.skip("样本缺失")
    with open(src, "rb") as f:
        r = client.post(
            "/api/warning/upload",
            files={"file": ("方案.docx", f, "application/octet-stream")},
            data={"type_hint": "培养方案", "major_hint": "工商管理"},
            headers={"X-API-Key": TEST_KEY},
        )
    assert r.json()["parsed_status"] == "parsing"
    _wait_parsed(client, "plan", major="工商管理")
    db = WarningDB(str(tmp_path / "warning.db"))
    assert db.conn.execute("SELECT COUNT(*) FROM plan_course").fetchone()[0] > 0
    db.close()

    r = client.post("/api/warning/delete", json={"file_type": "plan",
                                                 "major": "工商管理"},
                    headers={"X-API-Key": TEST_KEY})
    assert r.status_code == 200
    assert "已删除培养方案文件" in r.json()["message"]
    assert "工商管理" in r.json()["message"]

    # plan 级联清空 + 状态回落未上传占位
    db = WarningDB(str(tmp_path / "warning.db"))
    assert db.conn.execute("SELECT COUNT(*) FROM plan_course").fetchone()[0] == 0
    db.close()
    plan = next(f for f in client.get("/api/warning/status",
                headers={"X-API-Key": TEST_KEY}).json()["files"]
                if f["file_type"] == "plan")
    gs = next(it for it in plan["items"] if it["major"] == "工商管理")
    assert gs["file_name"] is None

    # 重复删除提示暂无
    r3 = client.post("/api/warning/delete", json={"file_type": "plan",
                                                  "major": "工商管理"},
                     headers={"X-API-Key": TEST_KEY})
    assert "暂无培养方案「工商管理」文件可删除" in r3.json()["message"]

    # plan/grade 缺 major → 拒绝（防误删整类）
    r5 = client.post("/api/warning/delete", json={"file_type": "plan"},
                     headers={"X-API-Key": TEST_KEY})
    assert "缺少 major 参数" in r5.json()["message"]

    # 非法 file_type
    r4 = client.post("/api/warning/delete", json={"file_type": "nope"},
                     headers={"X-API-Key": TEST_KEY})
    assert "未知文件类型" in r4.json()["message"]


@pytest.mark.samples
def test_delete_keeps_other_majors(client, tmp_path):
    """v1.7 删除按专业隔离：删工商方案保留工业工程方案。"""
    srcs = ["academicwarning/docs/2023版工商管理专业培养方案.docx",
            "academicwarning/docs/2023版工业工程专业培养方案.docx"]
    for i, src in enumerate(srcs):
        if not os.path.exists(src):
            pytest.skip("样本缺失")
        with open(src, "rb") as f:
            r = client.post(
                "/api/warning/upload",
                files={"file": (f"方案{i}.docx", f, "application/octet-stream")},
                data={"type_hint": "培养方案", "major_hint": ("工商管理" if i == 0
                                                              else "工业工程")},
                headers={"X-API-Key": TEST_KEY},
            )
        assert r.json()["parsed_status"] == "parsing"
        _wait_parsed(client, "plan", major=("工商管理" if i == 0 else "工业工程"))

    r = client.post("/api/warning/delete", json={"file_type": "plan",
                                                 "major": "工商管理"},
                    headers={"X-API-Key": TEST_KEY})
    assert "共 1 份" in r.json()["message"]

    plan = next(f for f in client.get("/api/warning/status",
                headers={"X-API-Key": TEST_KEY}).json()["files"]
                if f["file_type"] == "plan")
    by_major = {it["major"]: it for it in plan["items"]}
    assert by_major["工商管理"]["file_name"] is None      # 已删
    assert by_major["工业工程"]["parsed_status"] == "done"  # 保留


@pytest.mark.samples
def test_upload_shows_queued_before_parse(client, tmp_path, monkeypatch):
    """v1.10 上传即占位：解析未完成期间 /status 即见该文件（parsed_status=queued），
    而非"未上传"——根治排队期状态区不显示。

    用 monkeypatch 阻塞解析线程（事件同步），确保观察到中间态。"""
    import threading
    import academicwarning.service as svc
    src = "academicwarning/docs/2023版工商管理专业培养方案.docx"
    if not os.path.exists(src):
        pytest.skip("样本缺失")

    release = threading.Event()
    real_parse = svc.parse_plan

    def _blocked(path):
        release.wait(10)   # 卡住解析，让占位态可观察
        return real_parse(path)

    monkeypatch.setattr(svc, "parse_plan", _blocked)

    with open(src, "rb") as f:
        r = client.post(
            "/api/warning/upload",
            files={"file": ("方案.docx", f, "application/octet-stream")},
            data={"type_hint": "培养方案", "major_hint": "工商管理"},
            headers={"X-API-Key": TEST_KEY},
        )
    assert r.json()["parsed_status"] == "parsing"

    # 解析被阻塞 → status 应显示 queued（记录已存在、带原名），不是"未上传"
    plan = next(f for f in client.get("/api/warning/status",
                headers={"X-API-Key": TEST_KEY}).json()["files"]
                if f["file_type"] == "plan")
    row = {it["major"]: it for it in plan["items"]}["工商管理"]
    assert row["file_name"] == "方案.docx"
    assert row["parsed_status"] == "queued"

    release.set()
    done = _wait_parsed(client, "plan", major="工商管理")
    assert done["parsed_status"] == "done"
    assert "培养方案入库" in (done["note"] or "")
    # 占位行原地收尾：库中该专业只有一条记录（不新增第二条）
    db = WarningDB(str(tmp_path / "warning.db"))
    n = db.conn.execute(
        "SELECT COUNT(*) FROM source_file WHERE file_type='plan'"
        " AND json_extract(in_file_meta,'$.major')='工商管理'").fetchone()[0]
    assert n == 1
    db.close()


@pytest.mark.samples
def test_upload_failed_finishes_queued_as_failed(client, tmp_path, monkeypatch):
    """v1.10：解析失败 → 占位行原地收尾为 failed（不新增第二条记录）。"""
    import academicwarning.service as svc
    src = "academicwarning/docs/2023版工商管理专业培养方案.docx"
    if not os.path.exists(src):
        pytest.skip("样本缺失")

    def _boom(path):
        raise RuntimeError("解析崩了")
    monkeypatch.setattr(svc, "parse_plan", _boom)

    with open(src, "rb") as f:
        r = client.post(
            "/api/warning/upload",
            files={"file": ("方案.docx", f, "application/octet-stream")},
            data={"type_hint": "培养方案", "major_hint": "工商管理"},
            headers={"X-API-Key": TEST_KEY},
        )
    assert r.json()["parsed_status"] == "parsing"

    failed = _wait_parsed(client, "plan", major="工商管理")
    assert failed["parsed_status"] == "failed"
    assert "解析崩了" in (failed["error"] or "")
    db = WarningDB(str(tmp_path / "warning.db"))
    n = db.conn.execute(
        "SELECT COUNT(*) FROM source_file WHERE file_type='plan'").fetchone()[0]
    assert n == 1   # 仅占位一条，未新增
    db.close()


# ── 年级注册表（唯一事实来源）：列表 / 删除 ─────────────────────────


def test_grades_registry_endpoints(client):
    h = {"X-API-Key": TEST_KEY}
    # 列表（初始可空）
    assert client.get("/api/warning/grades", headers=h).status_code == 200
    # 添加 → 出现在列表
    assert client.post("/api/warning/grade", json={"name": "2027级"},
                       headers=h).json()["ok"] is True
    assert "2027级" in client.get("/api/warning/grades", headers=h).json()["grades"]
    # 删除 → 从列表消失
    d = client.delete("/api/warning/grade", params={"name": "2027级"}, headers=h)
    assert d.status_code == 200 and d.json()["ok"] is True
    assert "2027级" not in client.get("/api/warning/grades", headers=h).json()["grades"]
    # 幂等：再删返回 ok=false
    assert client.delete("/api/warning/grade", params={"name": "2027级"},
                         headers=h).json()["ok"] is False
    # 格式校验 + 鉴权
    assert client.delete("/api/warning/grade", params={"name": "27"},
                         headers=h).json()["ok"] is False
    assert client.get("/api/warning/grades").status_code == 401

