"""文件中心（设计 007）单测：类型识别 / 去重 / 适用性 / 解析编排 / API / 迁移。"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_DIR = "data/SmartGuide"
PLAN = f"{SAMPLE_DIR}/2023版工商管理专业培养方案.docx"
SYLLABUS = f"{SAMPLE_DIR}/27、28、29-管理学大纲-王磊.docx"
POLICY = f"{SAMPLE_DIR}/【发布】管理学院2026年本科生转专业实施细则.pdf"

pytestmark = [pytest.mark.samples, pytest.mark.skipif(
    not os.path.isfile(PLAN), reason="缺少样本文件")]


@pytest.fixture()
def dc(tmp_path, monkeypatch):
    from doccenter import db as dc_db, service
    monkeypatch.setattr(dc_db, "DB_PATH", str(tmp_path / "dc.db"))
    monkeypatch.setattr(service, "TRAINING_PLAN_DB", str(tmp_path / "tp.db"))
    return service


def test_classify_doc_type():
    from doccenter import service
    assert service.classify_doc_type("2023版工商管理专业培养方案.docx") == "培养方案"
    assert service.classify_doc_type("【发布】管理学院2026年本科生转专业实施细则.pdf") == "转专业政策"
    assert service.classify_doc_type("27、28、29-管理学大纲-王磊.docx") == "大纲"
    assert service.classify_doc_type("管理学院2025级本科生专业选择实施方案.pdf") == "专业选择方案"


def test_detect_meta():
    from doccenter import service
    year, major = service.detect_meta("2023版工商管理专业培养方案.docx")
    assert year == "2023级" and major == "工商管理"


def test_ingest_dedup_and_applicability(dc):
    r1 = dc.ingest_file(SYLLABUS, doc_type="大纲")
    assert r1["status"] == "ok" and r1["created"] is True
    r2 = dc.ingest_file(SYLLABUS, doc_type="大纲")
    assert r2["created"] is False and r2["id"] == r1["id"]     # hash 去重
    from doccenter.db import DocCenterDB
    db = DocCenterDB()
    try:
        appl = db.get_applicability(file_id=r1["id"])
        assert {a["feature"] for a in appl} == {"interpret"}    # 大纲默认只服务解读
        jobs = db.get_jobs(file_id=r1["id"])
        assert {j["pipeline"] for j in jobs} == {"text"}
    finally:
        db.close()


def test_process_text_job(dc):
    r = dc.ingest_file(SYLLABUS, doc_type="大纲")
    out = dc.process_pending(limit=10)
    assert out["text"] >= 1
    from doccenter.db import DocCenterDB
    db = DocCenterDB()
    try:
        jobs = db.get_jobs(file_id=r["id"])
        assert all(j["status"] == "done" for j in jobs)
    finally:
        db.close()


def test_plan_type_default_features_and_pipelines(dc):
    r = dc.ingest_file(PLAN, doc_type="培养方案")
    from doccenter.db import DocCenterDB
    db = DocCenterDB()
    try:
        feats = {a["feature"] for a in db.get_applicability(file_id=r["id"])}
        assert feats == {"interpret", "plan", "warning"}
        pipes = {j["pipeline"] for j in db.get_jobs(file_id=r["id"])}
        assert pipes == {"plan_structured", "warning_plan"}
    finally:
        db.close()


def test_resolve_returns_ready(dc):
    r = dc.ingest_file(SYLLABUS, doc_type="大纲")
    dc.process_pending(limit=5)
    items = dc.resolve("interpret", "2023级", "")
    hit = [x for x in items if x["file_id"] == r["id"]]
    assert hit and hit[0]["ready"] is True
    assert hit[0]["output_ref"] == {} or isinstance(hit[0]["output_ref"], dict)


def test_resolve_dedupes_wildcard_and_specific(dc):
    """同一文件同时存「全部」与具体年级（历史数据）→ resolve 只返回一次且取精确行。"""
    from doccenter.db import DocCenterDB
    from doccenter.models import Applicability
    r = dc.ingest_file(SYLLABUS, doc_type="大纲")
    dc.process_pending(limit=5)
    db = DocCenterDB()
    try:
        db.set_applicability(r["id"], [
            Applicability(file_id=r["id"], feature="interpret", grade="全部", major=""),
            Applicability(file_id=r["id"], feature="interpret", grade="2023级", major=""),
        ])
    finally:
        db.close()
    items = dc.resolve("interpret", "2023级", "")
    hits = [x for x in items if x["file_id"] == r["id"]]
    assert len(hits) == 1
    assert hits[0]["grade"] == "2023级"


def test_api_files_and_applicability(dc, monkeypatch):
    from fastapi.testclient import TestClient
    import trainingplan.api as api_mod
    from doccenter import api as dc_api
    monkeypatch.setattr(api_mod, "_API_KEY", "k")
    monkeypatch.setattr(dc_api, "_API_KEY", "k")
    r = dc.ingest_file(SYLLABUS, doc_type="大纲")
    with TestClient(api_mod.app) as client:
        h = {"X-API-Key": "k"}
        assert client.get("/api/doc-center/files").status_code == 401
        resp = client.get("/api/doc-center/files", headers=h)
        assert resp.status_code == 200 and resp.json()["count"] >= 1
        resp = client.put(f"/api/doc-center/file/{r['id']}/applicability", headers=h,
                          json={"features": ["interpret", "plan"], "grades": ["2023级"], "majors": []})
        assert resp.status_code == 200 and resp.json()["rows"] == 2
        resp = client.get("/api/doc-center/status", headers=h)
        assert resp.status_code == 200 and "summary" in resp.json()


@pytest.mark.skipif(not os.path.isfile(POLICY), reason="缺少政策样本（转专业实施细则 PDF）")
def test_policy_pipeline_parses_rules(dc, tmp_path, monkeypatch):
    """文件中心上传政策 → policy_rules 管线解析规则（政策入库统一入口）。"""
    import json
    from trainingplan import service as tp_service
    monkeypatch.setattr(tp_service, "DB_PATH", str(tmp_path / "tp.db"))
    r = dc.ingest_file(POLICY)          # 文件名识别 → 转专业政策
    dc.process_pending(limit=10)
    from doccenter.db import DocCenterDB
    db = DocCenterDB()
    try:
        jobs = {j["pipeline"]: j for j in db.get_jobs(file_id=r["id"])}
        assert jobs["policy_rules"]["status"] == "done"
        sid = json.loads(jobs["policy_rules"]["output_ref"]).get("source_doc_id")
    finally:
        db.close()
    assert sid
    tdb = tp_service.TrainingPlanDB(tp_service.DB_PATH)
    try:
        plans = tdb.get_transfer_plans(policy_year="2026", major="大数据管理与应用",
                                       entry_year="2025级")
        assert {p["scope"]: p["quota"] for p in plans} == {"学院内": 3, "跨学院": 5}
    finally:
        tdb.close()


def test_migrate_from_legacy(dc, tmp_path):
    # 造一个最小 legacy training_plan.db（plan_source_doc 指向真实样本）
    legacy = str(tmp_path / "tp.db")
    conn = sqlite3.connect(legacy)
    conn.execute("CREATE TABLE plan_source_doc (id INTEGER PRIMARY KEY, file_name TEXT, file_path TEXT,"
                 " file_hash TEXT, size_bytes INTEGER, ext TEXT, category TEXT, major TEXT,"
                 " entry_year TEXT, applies_to TEXT, note TEXT)")
    conn.execute("INSERT INTO plan_source_doc VALUES (1,?,?,?,?,?,?,?,?,?,?)",
                 (os.path.basename(SYLLABUS), os.path.abspath(SYLLABUS), "h1", 1, ".docx",
                  "大纲", "", "", '["全部"]', ""))
    conn.commit()
    conn.close()
    r = dc.migrate_from_legacy(training_plan_db=legacy, warning_db=str(tmp_path / "no.db"))
    assert r["status"] == "ok" and r["registered"] == 1
    # 再迁一次：hash 去重
    r2 = dc.migrate_from_legacy(training_plan_db=legacy, warning_db=str(tmp_path / "no.db"))
    assert r2["registered"] == 0 and r2["skipped"] == 1
