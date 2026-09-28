"""trainingplan/api.py HTTP API 测试（441 行，此前无 API 层测试）。

全部离线：DB_PATH 指向 tmp_path，TestClient 不进 lifespan（不启动解析 worker）。
鉴权负面/HMAC 伪造等安全用例在 tests/security/，此处只做功能面。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trainingplan import api, service

TEST_KEY = "plan-test-key-123"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api, "_API_KEY", TEST_KEY)
    monkeypatch.setattr(api, "_IMAGE_SECRET", b"test-image-secret")
    monkeypatch.setattr(service, "DB_PATH", str(tmp_path / "plan.db"))
    # 上传目录也隔离到 tmp，防止测试残留
    monkeypatch.setattr(api, "_UPLOAD_DIR", tmp_path / "uploads")


@pytest.fixture
def client() -> TestClient:
    # 不用 with（不触发 lifespan → 不 start_worker，保持纯离线）
    return TestClient(api.app)


def _key_headers() -> dict[str, str]:
    return {"X-API-Key": TEST_KEY}


# ── 鉴权 ────────────────────────────────────────────────────────────


def test_missing_key_returns_401(client: TestClient):
    assert client.get("/api/plan/majors").status_code == 401


def test_wrong_key_returns_401(client: TestClient):
    assert client.get("/api/plan/majors",
                      headers={"X-API-Key": "wrong"}).status_code == 401


def test_valid_key_passes(client: TestClient):
    assert client.get("/api/plan/majors", headers=_key_headers()).status_code == 200


# ── 只读端点 ────────────────────────────────────────────────────────


def test_majors_lists_standard_majors(client: TestClient):
    r = client.get("/api/plan/majors", headers=_key_headers())
    assert r.status_code == 200
    body = r.json()
    majors = body.get("majors") if isinstance(body, dict) else body
    assert isinstance(majors, list) and majors, body


def test_overview_unknown_major_reports_bad_state(client: TestClient):
    r = client.get("/api/plan/overview",
                   params={"major": "不存在的专业", "entry_year": "2023"},
                   headers=_key_headers())
    assert r.status_code == 200
    # 无法识别的专业 → need_major（并列出可选专业）
    assert r.json().get("state") in {"bad", "unavailable", "need_major"}


def test_overview_no_data_unavailable(client: TestClient):
    r = client.get("/api/plan/overview",
                   params={"major": "工商管理", "entry_year": "2023"},
                   headers=_key_headers())
    assert r.status_code == 200
    # 空库 → 尚未收录
    assert r.json().get("state") == "unavailable"


def test_semester_map_no_data(client: TestClient):
    r = client.get("/api/plan/semester-map",
                   params={"major": "工商管理", "entry_year": "2023"},
                   headers=_key_headers())
    assert r.status_code == 200


def test_prereq_no_document_unavailable(client: TestClient):
    r = client.get("/api/plan/prereq",
                   params={"major": "工商管理", "entry_year": "2023"},
                   headers=_key_headers())
    assert r.status_code == 200
    assert r.json().get("state") == "unavailable"


def test_status_endpoint_no_documents(client: TestClient):
    r = client.get("/api/plan/status",
                   params={"major": "工商管理", "entry_year": "2023"},
                   headers=_key_headers())
    assert r.status_code == 200


# ── 先修关系原图：HMAC 签名鉴权二选一 ──────────────────────────────


def test_prereq_image_valid_token_reaches_lookup(client: TestClient):
    """有效签名 + API key 双通道：签名有效时即使无 key 也可进入（图不存在 → 404）。"""
    import time as _time

    exp = int(_time.time()) + 300
    token = api._sign_image("工商管理", "2023级", exp)
    r = client.get("/api/plan/prereq-image",
                   params={"major": "工商管理", "entry_year": "2023级",
                           "exp": str(exp), "token": token})
    # 签名通过 → 进入查图逻辑；空库无图 → 404（而非 401）
    assert r.status_code == 404


def test_prereq_image_fallback_to_header_key(client: TestClient):
    """无/坏签名时回退 X-API-Key：带 key → 进入查找（404）；不带 → 401。"""
    r = client.get("/api/plan/prereq-image",
                   params={"major": "工商管理", "entry_year": "2023级",
                           "exp": "9999999999", "token": "forged"},
                   headers=_key_headers())
    assert r.status_code == 404

    r2 = client.get("/api/plan/prereq-image",
                    params={"major": "工商管理", "entry_year": "2023级",
                            "exp": "9999999999", "token": "forged"})
    assert r2.status_code == 401
