"""doccenter service 合成测试（不依赖 data/SmartGuide 样本）。

覆盖 ingest_file 登记回执/默认适用性/去重不重置/orig_name 修正，与
migrate_from_legacy 两段迁移分支（hash 命中复用、路径补登、路径缺失跳过、
ingest 异常记账、旧库表缺失幂等）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def dc(tmp_path, monkeypatch):
    from doccenter import db as dc_db, service
    monkeypatch.setattr(dc_db, "DB_PATH", str(tmp_path / "dc.db"))
    monkeypatch.setattr(service, "TRAINING_PLAN_DB", str(tmp_path / "tp.db"))
    return service


@pytest.fixture()
def _db():
    from doccenter.db import DocCenterDB
    db = DocCenterDB()
    try:
        yield db
    finally:
        db.close()


def _file(tmp_path, name: str, content: bytes = b"doc-bytes") -> str:
    p = tmp_path / name
    p.write_bytes(content)
    return str(p)


def _legacy_tp(tmp_path, rows) -> str:
    """造 legacy training_plan 库（plan_source_doc）。"""
    db = tmp_path / "tp_legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE plan_source_doc (id INTEGER PRIMARY KEY AUTOINCREMENT, file_name TEXT,"
        " file_path TEXT, file_hash TEXT, size_bytes INTEGER, ext TEXT, category TEXT,"
        " major TEXT, entry_year TEXT, applies_to TEXT, note TEXT)")
    for row in rows:
        conn.execute(
            "INSERT INTO plan_source_doc (file_name,file_path,file_hash,size_bytes,ext,"
            "category,major,entry_year,applies_to,note) VALUES (?,?,?,?,?,?,?,?,?,?)", row)
    conn.commit()
    conn.close()
    return str(db)


def _legacy_warning(tmp_path, rows) -> str:
    """造 legacy 预警库（source_file，只含 plan/gen_ed 需要的列）。"""
    db = tmp_path / "warning_legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE source_file (id INTEGER PRIMARY KEY, file_name TEXT, file_path TEXT,"
        " file_hash TEXT, file_type TEXT, grade TEXT)")
    for row in rows:
        conn.execute(
            "INSERT INTO source_file (id,file_name,file_path,file_hash,file_type,grade)"
            " VALUES (?,?,?,?,?,?)", row)
    conn.commit()
    conn.close()
    return str(db)


def _ref(job: dict):
    ref = job.get("output_ref") or {}
    return json.loads(ref) if isinstance(ref, str) else ref


# ── ingest_file：登记 / 适用性 / orig_name ─────────────────────────


def test_ingest_missing_file(dc):
    r = dc.ingest_file("/no/such/file.docx")
    assert r == {"status": "error", "message": "文件不存在"}


def test_ingest_defaults_and_jobs(dc, tmp_path, _db):
    from doccenter.models import default_for
    p = _file(tmp_path, "2023版工商管理专业培养方案.docx")
    r = dc.ingest_file(p, doc_type="培养方案", subject_grade="2023级", uploaded_by="u1")
    assert r["status"] == "ok" and r["created"] is True
    dflt = default_for("培养方案")
    assert r["doc_type"] == "培养方案" and r["subject_grade"] == "2023级"
    assert r["features"] == dflt["features"] and r["pipelines"] == dflt["pipelines"]
    assert r["grades"] == ["2023级"] and r["subject_major"] == "工商管理"
    appl = _db.get_applicability(file_id=r["id"])
    assert {a["feature"] for a in appl} == set(dflt["features"])
    assert {a["grade"] for a in appl} == {"2023级"}
    jobs = _db.get_jobs(file_id=r["id"])
    assert {j["pipeline"] for j in jobs} == set(dflt["pipelines"])


def test_ingest_dedup_and_applicability_reset_semantics(dc, tmp_path, _db):
    p = _file(tmp_path, "a.txt")
    r1 = dc.ingest_file(p, doc_type="政策", grades=["2023级"])
    assert r1["created"] is True
    # 重复上传 + 显式 features → 适用性被替换
    r2 = dc.ingest_file(p, doc_type="政策", features=["interpret"])
    assert r2["created"] is False and r2["id"] == r1["id"]
    appl = _db.get_applicability(file_id=r1["id"])
    assert {a["feature"] for a in appl} == {"interpret"}
    # 重复上传不显式传入 → 适用性不重置（仍是上一次的 interpret）
    dc.ingest_file(p, doc_type="政策")
    appl = _db.get_applicability(file_id=r1["id"])
    assert {a["feature"] for a in appl} == {"interpret"}


def test_ingest_orig_name_fixes_history(dc, tmp_path, _db):
    p = _file(tmp_path, "U4Sek9tmp0.bin")
    r1 = dc.ingest_file(p, doc_type="政策")
    f = _db.get_file(r1["id"])
    assert f.file_name == "U4Sek9tmp0.bin"
    # 同内容带显式 orig_name 重复上传 → 修正历史临时名
    r2 = dc.ingest_file(p, doc_type="政策", orig_name="2026转专业细则.pdf")
    assert r2["created"] is False
    f2 = _db.get_file(r1["id"])
    assert f2.file_name == "2026转专业细则.pdf" and f2.title == "2026转专业细则.pdf"


# ── migrate_from_legacy：两段迁移分支 ──────────────────────────────


def test_migrate_all_missing_dbs(dc, tmp_path):
    out = dc.migrate_from_legacy(training_plan_db=str(tmp_path / "none1.db"),
                                 warning_db=str(tmp_path / "none2.db"))
    assert out == {"status": "ok", "registered": 0, "skipped": 0, "errors": []}


def test_migrate_plan_docs_branches(dc, tmp_path, monkeypatch):
    ok = _file(tmp_path, "plan_ok.docx")
    other = _file(tmp_path, "plan_bad.json.docx", content=b"other-bytes")
    rows = [
        ("ok.docx", ok, "h_ok", 1, ".docx", "培养方案", "工商管理",
         "2023级", '["2023级","2024级"]', "备注"),                # 正常登记
        ("gone.docx", str(tmp_path / "gone.docx"), "h_gone", 1,
         ".docx", "政策", "", "", "[]", ""),                     # 路径缺失 → skipped
        ("bad_json.docx", other, "h_bad", 1, ".docx", "大纲", "", "",
         "not-json", ""),                                        # applies_to 坏 JSON → grades 退默认
        ("boom.docx", ok, "h_boom", 1, ".docx", "政策", "", "", "[]", "boom"),  # 同 hash 命中后再抛异常
    ]
    legacy = _legacy_tp(tmp_path, rows)

    real_ingest = dc.ingest_file

    def fake_ingest(path, **kw):
        if kw.get("note") == "boom":
            raise RuntimeError("模拟失败")
        return real_ingest(path, **kw)

    monkeypatch.setattr(dc, "ingest_file", fake_ingest)
    out = dc.migrate_from_legacy(training_plan_db=legacy,
                                 warning_db=str(tmp_path / "none.db"))
    assert out["status"] == "ok"
    assert out["registered"] == 2 and out["skipped"] == 1
    assert len(out["errors"]) == 1 and "boom.docx: RuntimeError: 模拟失败" in out["errors"][0]


def test_migrate_warning_hash_reuse_marks_done(dc, tmp_path, _db):
    # 预先把文件入中心库，再让 legacy 记录以 hash 命中（旧机路径不存在也无所谓）
    p = _file(tmp_path, "w_plan.docx")
    r = dc.ingest_file(p, doc_type="培养方案", subject_grade="2023级")
    f = _db.get_file(r["id"])
    legacy = _legacy_warning(
        tmp_path, [(7, "w_plan.docx", "/旧机路径不存在.docx", f.file_hash, "plan", "2023级")])

    out = dc.migrate_from_legacy(training_plan_db=str(tmp_path / "none.db"),
                                 warning_db=legacy)
    assert out["registered"] == 0 and out["skipped"] == 0 and out["errors"] == []
    jobs = _db.get_jobs(file_id=r["id"], pipeline=dc.PIPE_WARNING_PLAN)
    assert jobs and jobs[0]["status"] == "done"
    assert _ref(jobs[0]) == {"source_file_id": 7}


def test_migrate_warning_path_ingest_gen_ed(dc, tmp_path, _db):
    # 无 hash 命中 + 路径有效 → 补登为通识表并标 gen_ed job done
    p = _file(tmp_path, "gen_ed.docx")
    legacy = _legacy_warning(tmp_path, [(3, "gen_ed.docx", p, "", "gen_ed", "2024级")])
    from doccenter.service import _file_md5

    out = dc.migrate_from_legacy(training_plan_db=str(tmp_path / "none.db"),
                                 warning_db=legacy)
    assert out["registered"] == 1 and out["skipped"] == 0 and out["errors"] == []
    f = _db.get_file_by_hash(_file_md5(p))
    assert f and f.doc_type == "通识表" and f.subject_grade == "2024级"
    jobs = _db.get_jobs(file_id=f.id, pipeline=dc.PIPE_WARNING_GEN_ED)
    assert jobs and jobs[0]["status"] == "done"
    assert _ref(jobs[0]) == {"source_file_id": 3}


def test_migrate_warning_missing_path_skipped(dc, tmp_path, _db):
    legacy = _legacy_warning(
        tmp_path, [(9, "gone.docx", str(tmp_path / "gone.docx"), "", "plan", "")])
    out = dc.migrate_from_legacy(training_plan_db=str(tmp_path / "none.db"),
                                 warning_db=legacy)
    assert out["registered"] == 0 and out["skipped"] == 1 and out["errors"] == []


def test_legacy_rows_missing_table_returns_empty(tmp_path):
    from doccenter.service import _legacy_rows
    empty_db = tmp_path / "empty.db"
    conn = sqlite3.connect(empty_db)
    conn.close()
    assert _legacy_rows(str(empty_db), "SELECT * FROM no_such_table") == []
