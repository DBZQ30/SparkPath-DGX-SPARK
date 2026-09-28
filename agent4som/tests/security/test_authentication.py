"""认证（Authentication）测试：X-API-Key 三服务阴性用例 + HMAC 图片短期签名。

覆盖：
- A01 凭证缺失/错误/为空 → 401（trainingplan / doccenter / academicwarning）
- A02 HMAC 图片 token：伪造、过期、篡改参数、常数时间比较放行
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from academicwarning import api as warning_api
from doccenter import api as doc_api, db as dc_db, service as dc_service
from trainingplan import api as plan_api, service as plan_service

TEST_KEY = "security-suite-key"


# ── 三服务 TestClient（全部隔离 DB / 上传目录） ─────────────────────


@pytest.fixture
def plan_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(plan_api, "_API_KEY", TEST_KEY)
    monkeypatch.setattr(plan_service, "DB_PATH", str(tmp_path / "plan.db"))
    monkeypatch.delenv("TRAINING_PLAN__API_KEY", raising=False)
    return TestClient(plan_api.app)  # 不进 lifespan（不启解析 worker）


@pytest.fixture
def doc_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(doc_api, "_API_KEY", TEST_KEY)
    monkeypatch.setattr(dc_db, "DB_PATH", str(tmp_path / "dc.db"))
    monkeypatch.setattr(doc_api, "_UPLOAD_DIR", tmp_path / "up")
    monkeypatch.setattr(dc_service, "TRAINING_PLAN_DB", str(tmp_path / "tp.db"))
    app = FastAPI()
    app.include_router(doc_api.router)
    return TestClient(app)


@pytest.fixture
def warning_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(warning_api, "_API_KEY", TEST_KEY)
    monkeypatch.setattr(warning_api, "_UPLOAD_DIR", tmp_path / "uploads")
    # db 隔离：api 与 service 的 WarningDB 都指向临时库并初始化 schema
    from academicwarning import service as warning_service
    from academicwarning.db import WarningDB
    db_path = str(tmp_path / "warning.db")
    monkeypatch.setattr(warning_api, "WarningDB", lambda: WarningDB(db_path))
    monkeypatch.setattr(warning_service, "WarningDB", lambda: WarningDB(db_path))
    _db = WarningDB(db_path)
    try:
        _db.init_schema()
    finally:
        _db.conn.close()
    return TestClient(warning_api.app)


# ── A01：X-API-Key 阴性用例（三个暴露面逐一验证） ──────────────────


def test_trainingplan_rejects_missing_and_wrong_key(plan_client):
    assert plan_client.get("/api/plan/majors").status_code == 401
    assert plan_client.get("/api/plan/majors",
                           headers={"X-API-Key": "wrong"}).status_code == 401
    assert plan_client.get("/api/plan/majors",
                           headers={"X-API-Key": ""}).status_code == 401


def test_doccenter_rejects_missing_and_wrong_key(doc_client):
    assert doc_client.get("/api/doc-center/files").status_code == 401
    assert doc_client.get("/api/doc-center/files",
                          headers={"X-API-Key": "wrong"}).status_code == 401


def test_academicwarning_rejects_missing_and_wrong_key(warning_client):
    assert warning_client.get("/api/warning/status").status_code == 401
    assert warning_client.get("/api/warning/status",
                              headers={"X-API-Key": "wrong"}).status_code == 401


def test_valid_key_passes_on_all_surfaces(plan_client, doc_client, warning_client):
    headers = {"X-API-Key": TEST_KEY}
    assert plan_client.get("/api/plan/majors", headers=headers).status_code == 200
    assert doc_client.get("/api/doc-center/files", headers=headers).status_code == 200
    assert warning_client.get("/api/warning/status", headers=headers).status_code == 200


# ── A02：HMAC 图片短期签名（trainingplan/api.py） ──────────────────


@pytest.fixture
def image_secret(monkeypatch):
    monkeypatch.setattr(plan_api, "_IMAGE_SECRET", b"security-image-secret")


def test_image_token_valid_signature_passes(image_secret):
    """正确签名 → compare_digest 相等 → 放行（小程序 <image> 无法带 header 的替代通道）。"""
    exp = int(time.time()) + 300
    token = plan_api._sign_image("工商管理", "2023级", exp)
    assert plan_api._verify_image_token("工商管理", "2023级", str(exp), token) is True


def test_image_token_rejects_forged_token(image_secret):
    exp = int(time.time()) + 300
    assert plan_api._verify_image_token("工商管理", "2023级", str(exp), "forged") is False


def test_image_token_rejects_expired(image_secret):
    exp = int(time.time()) - 10  # 已过期
    token = plan_api._sign_image("工商管理", "2023级", exp)
    assert plan_api._verify_image_token("工商管理", "2023级", str(exp), token) is False


def test_image_token_rejects_parameter_tampering(image_secret):
    """签名覆盖 major+year+exp：换专业/学年/时间戳任一项即失效。"""
    exp = int(time.time()) + 300
    token = plan_api._sign_image("工商管理", "2023级", exp)
    assert plan_api._verify_image_token("会计学", "2023级", str(exp), token) is False
    assert plan_api._verify_image_token("工商管理", "2024级", str(exp), token) is False
    assert plan_api._verify_image_token("工商管理", "2023级", str(exp + 1), token) is False


def test_image_token_rejects_malformed_exp(image_secret):
    assert plan_api._verify_image_token("工商管理", "2023级", "not-a-number", "x") is False
    assert plan_api._verify_image_token("工商管理", "2023级", None, "x") is False


def test_image_token_empty_token_is_falsy(image_secret):
    exp = int(time.time()) + 300
    assert plan_api._verify_image_token("工商管理", "2023级", str(exp), "") is False


def test_image_token_signature_differs_by_secret(image_secret, monkeypatch):
    """换 secret 后旧 token 全部失效（密钥轮换语义）。"""
    exp = int(time.time()) + 300
    token = plan_api._sign_image("工商管理", "2023级", exp)
    monkeypatch.setattr(plan_api, "_IMAGE_SECRET", b"rotated-secret")
    assert plan_api._verify_image_token("工商管理", "2023级", str(exp), token) is False
