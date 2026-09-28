"""doccenter/api.py HTTP API 测试（239 行 API 层，此前无直接 API 测试）。

全部离线：DB_PATH 与上传目录隔离到 tmp_path；解析编排
（process_pending / process_warning / sync_plan_jobs）替换为 fake，
鉴权负面用例集中在 tests/security/，此处只做功能面。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from doccenter import api, db as dc_db, service

TEST_KEY = "doc-center-test-key"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api, "_API_KEY", TEST_KEY)
    monkeypatch.setattr(dc_db, "DB_PATH", str(tmp_path / "dc.db"))
    monkeypatch.setattr(api, "_UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(service, "TRAINING_PLAN_DB", str(tmp_path / "tp.db"))
    # 解析编排 mock：本测试只验证 API 编排与存储层行为
    monkeypatch.setattr(service, "process_pending",
                        lambda limit=5, db_path=None: {"processed": 0})
    monkeypatch.setattr(service, "process_warning",
                        lambda limit=5, db_path=None: {"warning": 0})
    monkeypatch.setattr(service, "sync_plan_jobs", lambda db_path=None: 0)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _key_headers() -> dict[str, str]:
    return {"X-API-Key": TEST_KEY}


def _upload(client: TestClient, name: str = "2023版工商管理专业培养方案说明.txt",
            content: bytes = b"notice body " * 20, **form) -> dict:
    r = client.post("/api/doc-center/upload",
                    files={"file": (name, content, "text/plain")},
                    data=form, headers=_key_headers())
    assert r.status_code == 200
    return r.json()


# ── 鉴权 ────────────────────────────────────────────────────────────


def test_missing_key_returns_401(client: TestClient):
    assert client.get("/api/doc-center/files").status_code == 401


def test_wrong_key_returns_401(client: TestClient):
    r = client.get("/api/doc-center/files", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


# ── 上传 ────────────────────────────────────────────────────────────


def test_upload_registers_file_and_triggers_processing(client: TestClient, tmp_path: Path):
    body = _upload(client)
    assert body["status"] == "ok"
    assert body["created"] is True
    # 文件落在隔离的上传目录
    assert (tmp_path / "uploads" / "2023版工商管理专业培养方案说明.txt").exists()
    # 上传即触发两条解析编排
    assert "process_plan_text" in body and "process_warning" in body


def test_upload_rejects_unsupported_extension(client: TestClient):
    body = _upload(client, name="malware.exe", content=b"MZ...")
    assert body["status"] == "error"
    assert "不支持的文件类型" in body["message"]


def test_upload_enforces_size_limit(client: TestClient, monkeypatch):
    monkeypatch.setattr(api, "_MAX_SIZE", 10)
    body = _upload(client, content=b"x" * 40)
    assert body["status"] == "error"
    assert "10MB" in body["message"] or "上限" in body["message"]


def test_upload_same_content_deduped_by_hash(client: TestClient):
    first = _upload(client)
    second = _upload(client)
    assert second["status"] == "ok"
    assert second["created"] is False
    assert second["id"] == first["id"]


def test_upload_orig_name_overrides_temp_filename(client: TestClient):
    """微信临时名（U4Sek…xlsx）不得成为展示名；orig_name 才是真实文件名。"""
    body = _upload(client, name="U4Sek6BHo0jnb055e920db565a4c8b789ba7e1e719fa.xlsx",
                   content=b"same-content-1" * 20, doc_type="通识表",
                   orig_name="通识课程信息表.xlsx")
    assert body["created"] is True
    detail = client.get(f"/api/doc-center/file/{body['id']}",
                        headers=_key_headers()).json()
    assert detail["file_name"] == "通识课程信息表.xlsx"


def test_upload_dedup_repairs_historical_temp_name(client: TestClient):
    """同内容重复上传且带 orig_name 时，修正已入库的临时显示名。"""
    first = _upload(client, name="U4Sek6BHo0jnb055e920db565a4c8b789ba7e1e719fa.xlsx",
                    content=b"same-content-2" * 20, doc_type="通识表",
                    orig_name="通识课程信息表.xlsx")
    second = _upload(client, name="another-temp-hash.xlsx",
                     content=b"same-content-2" * 20, doc_type="通识表",
                     orig_name="通识课程信息表(2026).xlsx")
    assert second["created"] is False and second["id"] == first["id"]
    detail = client.get(f"/api/doc-center/file/{first['id']}",
                        headers=_key_headers()).json()
    assert detail["file_name"] == "通识课程信息表(2026).xlsx"


# ── 列表 / 详情 ─────────────────────────────────────────────────────


def test_list_files_and_get_detail(client: TestClient):
    body = _upload(client)
    fid = body["id"]

    r = client.get("/api/doc-center/files", headers=_key_headers())
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["files"][0]["id"] == fid

    r = client.get(f"/api/doc-center/file/{fid}", headers=_key_headers())
    assert r.status_code == 200
    detail = r.json()
    assert detail["id"] == fid
    assert "applicability" in detail and "jobs" in detail
    # 大纲类默认 text 管线 → 至少一个 job
    assert detail["jobs"]


def test_get_file_unknown_returns_404(client: TestClient):
    assert client.get("/api/doc-center/file/999",
                      headers=_key_headers()).status_code == 404


# ── 元数据更新 / 适用性 ─────────────────────────────────────────────


def test_update_file_metadata(client: TestClient):
    body = _upload(client)
    fid = body["id"]
    r = client.patch(f"/api/doc-center/file/{fid}", json={"note": "补录说明"},
                     headers=_key_headers())
    assert r.status_code == 200
    detail = client.get(f"/api/doc-center/file/{fid}",
                        headers=_key_headers()).json()
    assert detail["note"] == "补录说明"


def test_update_unknown_file_returns_404(client: TestClient):
    r = client.patch("/api/doc-center/file/999", json={"note": "x"},
                     headers=_key_headers())
    assert r.status_code == 404


def test_set_applicability_cartesian_rows(client: TestClient):
    body = _upload(client)
    fid = body["id"]
    r = client.put(f"/api/doc-center/file/{fid}/applicability",
                   json={"features": ["interpret", "plan"],
                         "grades": ["2023级", "2024级"], "majors": ["工商管理"]},
                   headers=_key_headers())
    assert r.status_code == 200
    # 2 功能 × 2 年级 × 1 专业 = 4 行
    assert r.json()["rows"] == 4
    applicability = client.get(f"/api/doc-center/file/{fid}",
                               headers=_key_headers()).json()["applicability"]
    assert len(applicability) == 4


def test_set_applicability_unknown_file_returns_404(client: TestClient):
    r = client.put("/api/doc-center/file/999/applicability",
                   json={"features": ["plan"]}, headers=_key_headers())
    assert r.status_code == 404


def test_set_applicability_collapses_wildcards(client: TestClient):
    """「全部」与具体年级、「不限」与具体专业不得共存（避免 resolve 重复命中）。"""
    body = _upload(client)
    fid = body["id"]
    r = client.put(f"/api/doc-center/file/{fid}/applicability",
                   json={"features": ["plan"], "grades": ["全部", "2023级"],
                         "majors": ["", "工商管理"]},
                   headers=_key_headers())
    assert r.status_code == 200
    assert r.json()["rows"] == 1     # 1 功能 ×「全部」×「不限」
    appl = client.get(f"/api/doc-center/file/{fid}",
                      headers=_key_headers()).json()["applicability"]
    assert {a["grade"] for a in appl} == {"全部"}
    assert {a["major"] for a in appl} == {""}


# ── 重解析 / 删除 ───────────────────────────────────────────────────


def test_retry_enqueues_pipelines(client: TestClient):
    body = _upload(client)
    fid = body["id"]
    r = client.post("/api/doc-center/retry", json={"file_id": fid},
                    headers=_key_headers())
    assert r.status_code == 200
    assert r.json()["pipelines"]


def test_retry_validates_file_id(client: TestClient):
    assert client.post("/api/doc-center/retry", json={},
                       headers=_key_headers()).status_code == 400
    assert client.post("/api/doc-center/retry", json={"file_id": 999},
                       headers=_key_headers()).status_code == 404


def test_delete_file_cascades(client: TestClient):
    body = _upload(client)
    fid = body["id"]
    r = client.delete(f"/api/doc-center/file/{fid}", headers=_key_headers())
    assert r.status_code == 200
    assert r.json()["cascaded"]["plan_document"] == 0
    # 删除后列表为空、详情 404
    assert client.get("/api/doc-center/files",
                      headers=_key_headers()).json()["count"] == 0
    assert client.get(f"/api/doc-center/file/{fid}",
                      headers=_key_headers()).status_code == 404


def test_delete_unknown_file_returns_404(client: TestClient):
    assert client.delete("/api/doc-center/file/999",
                         headers=_key_headers()).status_code == 404


def test_delete_file_cascades_legacy_source_doc(client: TestClient):
    """删除中心原件时，同内容 hash 的 legacy plan_source_doc 及其派生规则一并清理。"""
    from doccenter import service as dc_service
    from doccenter.db import DocCenterDB
    from trainingplan.db import TrainingPlanDB
    body = _upload(client, name="2023版工商管理专业培养方案说明.txt",
                   content=b"cascade-legacy-source" * 20)
    fid = body["id"]
    dc = DocCenterDB()
    try:
        fhash = dc.get_file(fid).file_hash
    finally:
        dc.close()
    tdb = TrainingPlanDB(dc_service.TRAINING_PLAN_DB)
    try:
        sid = tdb.upsert_source_doc({"category": "政策", "file_name": "x.txt",
                                     "file_path": "/tmp/x.txt", "file_hash": fhash,
                                     "ext": ".txt", "applies_to": ["全部"]})
        tdb.add_transfer_rules("2026", [{"rule_type": "志愿", "item": "数量", "value": "3"}],
                               source_doc_id=sid)
    finally:
        tdb.close()
    r = client.delete(f"/api/doc-center/file/{fid}", headers=_key_headers())
    assert r.status_code == 200
    assert r.json()["cascaded"]["plan_source_doc"] == 1
    tdb = TrainingPlanDB(dc_service.TRAINING_PLAN_DB)
    try:
        assert tdb.list_source_docs() == []
        n = tdb.conn.execute("SELECT COUNT(*) AS c FROM plan_transfer_rule WHERE source_doc_id=?",
                             (sid,)).fetchone()["c"]
        assert n == 0
    finally:
        tdb.close()


# ── 状态 / 手动消费 ─────────────────────────────────────────────────


def test_status_returns_summary_and_jobs(client: TestClient):
    _upload(client)
    r = client.get("/api/doc-center/status", headers=_key_headers())
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body and isinstance(body["jobs"], list)


def test_process_endpoint_dispatches(client: TestClient, monkeypatch):
    seen: list[dict] = []

    def fake_pending(limit=5, db_path=None):
        seen.append({"kind": "pending", "limit": limit})
        return {"processed": limit}

    def fake_warning(limit=5, db_path=None):
        seen.append({"kind": "warning", "limit": limit})
        return {"warning": limit}

    monkeypatch.setattr(service, "process_pending", fake_pending)
    monkeypatch.setattr(service, "process_warning", fake_warning)

    r = client.post("/api/doc-center/process?limit=3",
                    headers=_key_headers())
    assert r.status_code == 200
    assert r.json()["plan_or_text"] == {"processed": 3}
    assert r.json()["warning"] == {}       # 默认不跑预警
    assert seen == [{"kind": "pending", "limit": 3}]

    r2 = client.post("/api/doc-center/process?limit=2&warning=true",
                     headers=_key_headers())
    assert r2.json()["warning"] == {"warning": 2}
    assert seen[-1] == {"kind": "warning", "limit": 2}
