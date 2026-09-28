"""trainingplan 单测：解析口径 / 队列 / 解读 / API。

用真实培养方案样本（data/SmartGuide）跑解析；队列与 API 用临时库。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_DIR = "data/SmartGuide"
SAMPLE = {
    "工商管理": f"{SAMPLE_DIR}/2023版工商管理专业培养方案.docx",
    "工业工程": f"{SAMPLE_DIR}/2023版工业工程专业培养方案.docx",
    "会计学（ACCA）": f"{SAMPLE_DIR}/2023版会计学（ACCA）专业培养方案.docx",
    "大数据管理与应用": f"{SAMPLE_DIR}/2023版大数据管理与应用专业培养方案.docx",
}

pytestmark = [pytest.mark.samples, pytest.mark.skipif(
    not os.path.isfile(SAMPLE["工商管理"]), reason="缺少培养方案样本"
)]


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    from trainingplan import service
    monkeypatch.setattr(service, "DB_PATH", str(tmp_path / "tp.db"))
    monkeypatch.setattr(service, "_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    # 隔离后台 worker：单测只测队列/解析本身，不启动消费者
    monkeypatch.setattr(service, "_wake_worker", lambda: None)
    return service


# ─────────────────────────── 解析 ───────────────────────────

def test_parse_credit_structure_top_numbers():
    from trainingplan.parsers import parse_credit_structure
    expected = {"工商管理": ("148", 126, 22, 8), "工业工程": ("153.5", 130, 23.5, 8),
                "会计学（ACCA）": ("154", 136, 18, 8), "大数据管理与应用": ("142", 124, 18, 8)}
    for major, (total, ct, pr, ep) in expected.items():
        _nodes, top = parse_credit_structure(SAMPLE[major])
        assert top["total"].startswith(total), (major, top)
        assert top["course_teaching"] == ct, (major, top)
        assert top["practice"] == pr
        assert top["extra_practice"] == ep


def test_parse_graduation_reqs():
    from trainingplan.parsers import parse_credit_structure, parse_graduation_reqs
    for _major, path in SAMPLE.items():
        _, top = parse_credit_structure(path)
        reqs = parse_graduation_reqs(path, top)
        items = {r.item: r for r in reqs}
        assert "授予学位" in items
        assert items["授予学位"].value.endswith("学士学位")
        assert items["毕业总学分"].unit == "学分"
        assert items["劳动教育"].unit == "学时"
        assert "创新创业类课程" in items and "美育课程" in items


def test_parse_courses_reuses_academicwarning():
    from trainingplan.parsers import parse_plan_data
    _meta, courses, sem, _bad = parse_plan_data(SAMPLE["工商管理"])
    assert len(courses) > 50
    assert len(sem) > 40
    # 复用 academicwarning.models.PlanCourse
    assert isinstance(courses, list)
    assert hasattr(courses[0], "course_code")


def test_credit_display_cleanup():
    from trainingplan.parsers import display_credit, display_course_name
    assert display_credit("148+（8）") == "148+8"
    assert display_course_name("体育-1体育-3") == "体育-1/体育-3"
    assert display_course_name("军训") == "军训"


def test_extract_prereq_candidates_from_section():
    from trainingplan.parsers import extract_prereq_candidates
    cands = extract_prereq_candidates(SAMPLE["工商管理"])
    assert cands, "应能从第八章抽到先修图"
    assert cands[0]["media"].startswith("word/media/")


# ─────────────────────────── 队列 ───────────────────────────

def test_queue_fifo_and_positions(tmp_db):
    svc = tmp_db
    for _major, path in list(SAMPLE.items())[:3]:
        r = svc.submit_upload(path, uploader="t")
        assert r["status"] == "queued", r
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        items = db.queue_items()
        assert [i["position"] for i in items] == [1, 2, 3]
        assert [i["ahead"] for i in items] == [0, 1, 2]
        # 单消费者：claim 后该条变 parsing，其余仍 queued
        d1 = db.claim_next()
        assert d1 is not None and d1.parsed_status == "parsing"
        summary = db.queue_summary()
        assert summary["parsing"] == 1 and summary["queued"] == 2
        # 剩余项的队首位置重排
        rest = [i for i in db.queue_items() if i["state"] == "queued"]
        assert [i["position"] for i in rest] == [1, 2]
        d2 = db.claim_next()
        assert d2 is not None and d2.id != d1.id
    finally:
        db.close()


def test_duplicate_upload_not_reenqueued(tmp_db):
    """同（专业,年级）同内容重复上传 → 不重复入队（返回 done）。"""
    svc = tmp_db
    r1 = svc.submit_upload(SAMPLE["工商管理"], uploader="t")
    assert r1["status"] == "queued"
    # 直接标记 done 模拟已解析完成
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        db.finish_document(r1["id"], "done", note="x")
    finally:
        db.close()
    r2 = svc.submit_upload(SAMPLE["工商管理"], uploader="t")
    assert r2["status"] == "done"
    assert "已上传过" in r2["message"]


def test_recover_stale_marks_parsing_failed(tmp_db):
    svc = tmp_db
    svc.submit_upload(SAMPLE["工商管理"], uploader="t")
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        db.claim_next()  # → parsing
        n = db.recover_stale()
        assert n == 1
        doc = db.list_documents()[0]
        assert doc.parsed_status == "failed"
        assert "中断" in doc.error
        # 可重试
        assert db.retry(doc.id) is True
        assert db.list_documents()[0].parsed_status == "queued"
    finally:
        db.close()


def test_reject_wrong_major(tmp_db):
    svc = tmp_db
    r = svc.submit_upload(SAMPLE["工商管理"], major="工业工程", uploader="t")
    assert r["status"] == "rejected"
    assert "不匹配" in r["message"]


def test_normalize_helpers(tmp_db):
    svc = tmp_db
    assert svc.normalize_major("大数据") == "大数据管理与应用"
    assert svc.normalize_major("ACCA") == "会计学（ACCA）"
    assert svc.normalize_major("不存在专业") is None
    assert svc.normalize_entry_year("23级") == "2023级"
    assert svc.normalize_entry_year("2023版") == "2023级"


def test_academic_hint_and_grade_words():
    """相对说法（大一/新生/今年）按当前学年折算（2026-09 → 大一=2026级）。"""
    from datetime import datetime
    from trainingplan.service import academic_hint, normalize_entry_year as n
    h = academic_hint(datetime(2026, 9, 24))
    assert h["academic_start"] == 2026
    assert h["grades"] == {"大一": "2026级", "大二": "2025级", "大三": "2024级", "大四": "2023级"}
    # 春季学期（3 月）仍属上一学年
    h2 = academic_hint(datetime(2026, 3, 1))
    assert h2["academic_start"] == 2025 and h2["grades"]["大一"] == "2025级"
    # 折算与当前时间自洽
    cur = academic_hint()
    assert n("大一") == f"{cur['academic_start']}级"
    assert n("我是今年的大一新生") == f"{cur['academic_start']}级"
    assert n("大二") == f"{cur['academic_start'] - 1}级"
    assert n("新生") == f"{cur['academic_start']}级"
    assert n("") is None


# ─────────────────────────── 解读（不跑 VL） ───────────────────────────

def _seed_plan(svc, major="工商管理", year="2023级"):
    """跳过 VL，直接写一份解析结果，供解读/API 测试。"""
    from trainingplan.parsers import (
        parse_credit_structure, parse_graduation_reqs, parse_plan_data,
        parse_mode_rules, parse_course_notes)
    from trainingplan.service import _build_mode_data
    path = SAMPLE[major]
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        from trainingplan.models import PlanDocument
        doc = PlanDocument(major=major, entry_year=year, file_name=os.path.basename(path),
                           file_path=path, uploader="t")
        pid = db.enqueue_document(doc)
        _meta, courses, sem, _bad = parse_plan_data(path)
        nodes, top = parse_credit_structure(path)
        reqs = parse_graduation_reqs(path, top)
        rules, mcourses, scopes = _build_mode_data(parse_mode_rules(path))
        db.replace_plan_data(pid, nodes, courses, sem, [], reqs,
                             course_notes=parse_course_notes(path))
        db.replace_mode_data(pid, rules, mcourses, scopes)
        db.finish_document(pid, "done", note="test", meta_extra={"top": top})
        return pid
    finally:
        db.close()


def test_interpret_summary_four_sections(tmp_db):
    svc = tmp_db
    _seed_plan(svc)
    r = svc.interpret("工商管理", "2023")
    assert r["state"] == "done"
    text = r["summary_text"]
    for sec in ("一、学分结构", "二、四年课程地图", "三、先修关系", "四、毕业/授学位硬条件"):
        assert sec in text, text
    assert "148+8" in text
    assert "管理学学士学位" in text
    # 无先修边时应给出明确说明，而不是留空
    assert "先修关系" in text


def test_interpret_unavailable_grade(tmp_db):
    svc = tmp_db
    r = svc.interpret("工商管理", "2099")
    assert r["state"] == "unavailable"
    assert "尚未收录" in r["message"]


def test_interpret_parsing_state(tmp_db):
    svc = tmp_db
    svc.submit_upload(SAMPLE["工商管理"], uploader="t")
    r = svc.interpret("工商管理", "2023")
    assert r["state"] == "parsing"
    assert "解析中" in r["message"]


def test_prereq_verify_flow(tmp_db):
    svc = tmp_db
    pid = _seed_plan(svc)
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        from trainingplan.models import PrereqEdge
        db.replace_plan_data(pid, [], [], [], [
            PrereqEdge(plan_id=pid, from_course_name="管理学", to_course_name="企业战略管理"),
        ], [])
        assert len(db.get_prereq_edges(pid, verified_only=True)) == 0
        n = db.set_prereq_verified(pid, [{"action": "confirm", "from": "管理学",
                                          "to": "企业战略管理"}], "admin")
        assert n == 1
        edges = db.get_prereq_edges(pid, verified_only=True)
        assert len(edges) == 1 and edges[0]["verified"] == 1
        # 删除
        db.set_prereq_verified(pid, [{"action": "delete", "from": "管理学",
                                      "to": "企业战略管理"}], "admin")
        assert len(db.get_prereq_edges(pid, verified_only=False)) == 0
    finally:
        db.close()


# ─────────────────────────── API ───────────────────────────

def test_prereq_image_signed_url():
    """小程序 <image> 无法带 header → 用短期签名 URL 鉴权。"""
    from urllib.parse import urlparse, parse_qs
    import trainingplan.api as api_mod
    path = api_mod._image_path("工商管理", "2023级")
    q = parse_qs(urlparse(path).query)
    assert "exp" in q and "token" in q
    assert api_mod._verify_image_token("工商管理", "2023级", q["exp"][0], q["token"][0])
    # 篡改 token / 换专业 / 过期 → 不通过
    assert not api_mod._verify_image_token("工商管理", "2023级", q["exp"][0], q["token"][0] + "x")
    assert not api_mod._verify_image_token("工业工程", "2023级", q["exp"][0], q["token"][0])
    assert not api_mod._verify_image_token("工商管理", "2023级", "1", q["token"][0])


def test_interpret_needs_both_major_and_year(tmp_db):
    """专业与年级缺一都反问；指定未收录年级不冒充。"""
    svc = tmp_db
    _seed_plan(svc, "工商管理", "2023级")
    # 缺年级 → 反问（列可用年级），不自动挑年级
    r = svc.interpret("工商管理")
    assert r["state"] == "need_year"
    assert "2023级" in r["message"]
    assert r.get("years") == ["2023级"]
    # 缺专业 → 反问
    r_bad = svc.interpret("不存在专业", "2023级")
    assert r_bad["state"] == "need_major"
    # 明确指定未收录年级 → 不替换
    r2 = svc.interpret("工商管理", "2025")
    assert r2["state"] == "unavailable"
    assert "尚未收录" in r2["message"]
    # 专业+年级齐 → 正常
    r3 = svc.interpret("工商管理", "2023")
    assert r3["state"] == "done"
    assert "【工商管理 · 2023级 培养方案解读】" in r3["summary_text"]


def test_applies_to_multi_grade(tmp_db):
    """applies_to：精确年级优先，其次"全部"；一版可适用多个年级。"""
    from trainingplan.db import ALL_GRADES
    svc = tmp_db
    pid = _seed_plan(svc, "工商管理", "2023级")
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        # 默认 applies_to = [主年级]
        doc = db.get_document(pid)
        assert doc.applies_to == ["2023级"]
        # 未收录年级（2024）→ 尚未收录
        assert svc.interpret("工商管理", "2024")["state"] == "unavailable"
        # 把该版设为适用 2023+2024 → 2024 也能解读
        assert db.set_document_applies_to(pid, ["2023级", "2024级"]) is True
        r = svc.interpret("工商管理", "2024")
        assert r["state"] == "done"
        assert r["applies_to"] == ["2023级", "2024级"]
        assert "该方案适用于" in r["summary_text"]
        # 设为"全部" → 任意年级可解读
        db.set_document_applies_to(pid, [ALL_GRADES])
        assert db.find_document_for_grade("工商管理", "2099级").applies_to == [ALL_GRADES]
        assert svc.interpret("工商管理", "2026")["state"] == "done"
    finally:
        db.close()


def test_source_doc_applies_to_defaults(tmp_db, tmp_path):
    """资料类默认"全部"；培养方案资料副本默认绑定其年级。"""
    from trainingplan.db import ALL_GRADES
    svc = tmp_db
    d = tmp_path / "资料目录"
    d.mkdir()
    import shutil
    shutil.copy(SAMPLE["工商管理"], d / "2023版工商管理专业培养方案.docx")
    shutil.copy(f"{SAMPLE_DIR}/通识课程信息表(1).xlsx", d / "通识课程信息表.xlsx")
    r = svc.__dict__
    from trainingplan.ingest import ingest_directory
    res = ingest_directory(str(d), svc.DB_PATH, with_text=False, enqueue_plans=False)
    assert res["registered"] == 2
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        plans = db.list_source_docs("培养方案")
        others = db.list_source_docs("清单")
        assert plans and plans[0]["applies_to"] == ["2023级"]
        assert others and others[0]["applies_to"] == [ALL_GRADES]
        # 可改
        assert db.set_source_doc_applies_to(others[0]["id"], ["2024级"]) is True
        assert db.list_source_docs("清单")[0]["applies_to"] == ["2024级"]
    finally:
        db.close()


def test_prereq_vml_extraction(tmp_db):
    """工业工程先修图为 VML/OLE 对象（<v:imagedata>），须能抽出。"""
    from trainingplan.parsers import extract_prereq_candidates
    for major, path in SAMPLE.items():
        cands = extract_prereq_candidates(path)
        assert cands, f"{major} 应能抽到先修关系图"
        assert cands[0]["media"].startswith("word/media/")
    ie = extract_prereq_candidates(SAMPLE["工业工程"])
    assert ie[0]["media"].endswith(".emf"), ie


def test_set_applies_to_empty_resets_to_primary(tmp_db):
    """空了 applies_to 不应变成"全部"；培养方案空值 = 重置为主年级。"""
    svc = tmp_db
    pid = _seed_plan(svc, "工商管理", "2023级")
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        db.set_document_applies_to(pid, ["全部"])
        assert db.get_document(pid).applies_to == ["全部"]
        db.set_document_applies_to(pid, [])
        assert db.get_document(pid).applies_to == ["2023级"]
        # 旧库空值回填：直接写 '[]' 后重新打开 → 回填为主年级
        db.conn.execute("UPDATE plan_document SET applies_to='[]' WHERE id=?", (pid,))
        db.conn.commit()
    finally:
        db.close()
    db2 = svc.TrainingPlanDB(svc.DB_PATH)   # 重新打开触发 _backfill_applies_to
    try:
        assert db2.get_document(pid).applies_to == ["2023级"]
    finally:
        db2.close()


def test_list_and_interpret_agree_on_grades(tmp_db, capsys):
    """list 与 interpret 必须口径一致：都按 applies_to（适用年级），不能用主年级。

    回归：曾出现 list 说「2023级」而 interpret 说「全部」的不一致。
    """
    svc = tmp_db
    pid = _seed_plan(svc, "工商管理", "2023级")
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        db.set_document_applies_to(pid, ["全部"])
    finally:
        db.close()
    # interpret 缺年级 → 报"全部"
    r = svc.interpret("工商管理")
    assert r["state"] == "need_year"
    assert r["years"] == ["全部"]
    # list → 显示"适用：全部"（主年级仅作附注）
    from trainingplan.cli import main as cli_main
    assert cli_main(["list"]) == 0
    out = capsys.readouterr().out
    assert "适用：全部" in out, out
    assert "主年级 2023级" in out, out


def test_api_endpoints(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient
    svc = tmp_db
    _seed_plan(svc)
    import trainingplan.api as api_mod
    monkeypatch.setattr(api_mod, "_API_KEY", "testkey")

    with TestClient(api_mod.app) as client:
        assert client.get("/api/plan/majors").status_code == 401
        h = {"X-API-Key": "testkey"}
        r = client.get("/api/plan/majors", headers=h)
        assert r.status_code == 200
        assert "工商管理" in r.json()["majors"]

        r = client.get("/api/plan/overview?major=工商管理&entry_year=2023", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "done"
        assert body["top"]["course_teaching"] == 126

        r = client.get("/api/plan/status", headers=h)
        assert r.status_code == 200
        assert set(r.json()["summary"]) >= {"total", "done", "queued", "parsing", "failed"}

        r = client.get("/api/plan/courses?major=工商管理&entry_year=2023", headers=h)
        assert r.json()["count"] > 50

        r = client.get("/api/plan/prereq?major=工商管理&entry_year=2023&include_unverified=1", headers=h)
        assert r.status_code == 200

        r = client.get("/api/plan/semester-map?major=工商管理&entry_year=2023", headers=h)
        assert len(r.json()["semester_map"]) >= 6


# ─────────────────────── 培养模式（四路径，设计 005 §4） ───────────────────────

def test_parse_mode_rules_all_majors():
    from trainingplan.parsers import parse_mode_rules
    # 专业: (科学研究型学分, 研究生进阶门数, 交叉融合型学分, 学期学分上限)
    expected = {
        "工商管理": (6, 6, 6, 25),
        "工业工程": (6, 4, 6, 27),
        "会计学（ACCA）": (6, 4, 6, 30),
        "大数据管理与应用": (8, 4, 8, 25),
    }
    for major, (sci_credit, n_courses, cross_credit, limit) in expected.items():
        mi = parse_mode_rules(SAMPLE[major])
        sci = mi["modes"]["科学研究型"]
        assert sci["credit"] == sci_credit, (major, sci)
        assert len(sci["courses"]) == n_courses, (major, sci["courses"])
        assert mi["modes"]["交叉融合型"]["credit"] == cross_credit, major
        assert mi["modes"]["创新创业型"]["credit"] == 6, major
        assert mi["credit_limit"]["value"] == limit, (major, mi["credit_limit"])
        assert mi["apply_node"] == "大三第二学期"


def test_parse_mode_rules_cross_scope():
    from trainingplan.parsers import parse_mode_rules
    mi = parse_mode_rules(SAMPLE["工商管理"])
    assert "工业工程" in mi["modes"]["交叉融合型"]["majors"]
    # 大数据样式的"自动化专业核心课和专业选修课"（无"的"）也要抽到
    mi2 = parse_mode_rules(SAMPLE["大数据管理与应用"])
    assert "自动化" in mi2["modes"]["交叉融合型"]["majors"]


def test_parse_course_notes():
    from trainingplan.parsers import parse_course_notes
    notes = parse_course_notes(SAMPLE["工商管理"])
    assert notes.get("082038") == "研究生进阶"
    assert "前沿交叉" in notes.get("MAGT420208", "")


def test_modes_service(tmp_db):
    svc = tmp_db
    _seed_plan(svc)
    r = svc.modes("工商管理", "2023")
    assert r["state"] == "done"
    rules = r["rules"]
    sci = [x for x in rules if x["mode"] == "科学研究型" and x["rule_type"] == "学分替代"]
    assert sci and sci[0]["value"] == "6"
    cross_scope = [x for x in rules if x["rule_type"] == "跨选范围"]
    assert cross_scope and "工业工程" in cross_scope[0]["value"]
    limit = [x for x in rules if x["rule_type"] == "学期学分上限"]
    assert limit and limit[0]["value"] == "25"
    assert [s["allowed_major"] for s in r["scopes"]][:1] == ["工业工程"]
    assert "管理研究方法论I" in [c["course_name"] for c in r["courses"]]


def test_modes_need_year(tmp_db):
    svc = tmp_db
    _seed_plan(svc)
    r = svc.modes("工商管理", "")
    assert r["state"] == "need_year"
    assert "2023级" in r["message"]


def test_modes_cli(tmp_db, capsys):
    svc = tmp_db
    _seed_plan(svc)
    from trainingplan import cli
    cli.main(["modes", "--major", "工商管理", "--entry-year", "2023"])
    out = capsys.readouterr().out
    assert "科学研究型" in out and "交叉融合型" in out and "创新创业型" in out
    assert "大三第二学期" in out


# ─────────────────────── 先修/删除：applies_to 口径与级联（回归） ───────────────────────

def test_prereq_and_image_resolve_applies_to_grade(tmp_db, tmp_path, monkeypatch):
    """方案设为「全部」后，其它年级请求先修关系原图也应命中（不再只按主年级精确匹配）。"""
    from fastapi.testclient import TestClient
    import trainingplan.api as api_mod
    monkeypatch.setattr(api_mod, "_API_KEY", "k")
    monkeypatch.setattr(api_mod, "_IMAGE_SECRET", b"sec")
    svc = tmp_db
    pid = _seed_plan(svc, "工商管理", "2023级")
    img = tmp_path / "prereq.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 8)
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        db.set_document_applies_to(pid, ["全部"])
        db.finish_document(pid, "done", note="t", meta_extra={"prereq_image": str(img)})
    finally:
        db.close()
    client = TestClient(api_mod.app)
    h = {"X-API-Key": "k"}
    r = client.get("/api/plan/prereq",
                   params={"major": "工商管理", "entry_year": "2025级"}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0 and body["image_path"]
    # 签名 URL 无需 header（小程序 <image> 场景）
    assert client.get(body["image_path"]).status_code == 200
    # courses 同口径：不再因主年级不符而 unavailable
    rc = client.get("/api/plan/courses",
                    params={"major": "工商管理", "entry_year": "2025级"}, headers=h)
    assert rc.json().get("state") != "unavailable"


def test_delete_document_cascades_children(tmp_db):
    svc = tmp_db
    pid = _seed_plan(svc)
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        assert db.get_document(pid) is not None
        assert db.delete_document(pid) is True
        assert db.get_document(pid) is None
        for table in svc.TrainingPlanDB.PLAN_CHILD_TABLES:
            n = db.conn.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE plan_id=?", (pid,)).fetchone()["c"]
            assert n == 0, table
        assert db.delete_document(pid) is False   # 幂等：不再存在
    finally:
        db.close()


def test_delete_source_doc_cascades_policy(tmp_db):
    svc = tmp_db
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        sid = db.upsert_source_doc({
            "category": "政策", "file_name": "转专业政策.txt", "file_path": "/tmp/p.txt",
            "file_hash": "h1", "ext": ".txt", "applies_to": ["全部"],
        })
        assert sid
        db.add_transfer_rules("2026", [{"rule_type": "志愿", "item": "数量", "value": "3"}],
                              source_doc_id=sid)
        n = db.conn.execute("SELECT COUNT(*) AS c FROM plan_transfer_rule WHERE source_doc_id=?",
                            (sid,)).fetchone()["c"]
        assert n == 1
        assert db.delete_source_doc(sid) is True
        assert db.list_source_docs() == []
        n2 = db.conn.execute("SELECT COUNT(*) AS c FROM plan_transfer_rule WHERE source_doc_id=?",
                             (sid,)).fetchone()["c"]
        assert n2 == 0
        assert db.delete_source_doc(sid) is False
    finally:
        db.close()
