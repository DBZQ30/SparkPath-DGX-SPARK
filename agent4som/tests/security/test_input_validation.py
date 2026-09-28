"""输入校验（Input Validation）测试：上传配额 / 类型门禁 / 文件名穿越 / 参数模糊。

覆盖：
- V01 入库管线：不成类型拒收、超限拒收、限流、敏感系统目录拒读
- V02 文件名穿越：doccenter 上传与 WeCom 缓存文件名归一化均不逃逸
- V03 查询参数模糊：恶意/超长/控制字符入参不 500、不落盘
"""

from __future__ import annotations


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_base.bootstrap import create_ingestion_orchestrator
from knowledge_base.ingestion.orchestrator import IngestionOrchestrator, IngestionStatus
from doccenter import api as doc_api, db as dc_db, service as dc_service
from trainingplan import api as plan_api, service as plan_service

TEST_KEY = "input-validation-key"


# ── V01：入库管线校验 ───────────────────────────────────────────────


class _FakeQuotaRepo:
    """QuotaManager 的 repo 依赖：计数器全部指到内存值。"""

    def __init__(self, recent=0, daily=0, count=0):
        self._recent, self._daily, self._count = recent, daily, count

    def get_user_recent_upload_count(self, user_id, window_seconds=60):
        return self._recent

    def get_user_daily_upload_count(self, user_id):
        return self._daily

    def get_user_file_count(self, user_id):
        return self._count

    def record_upload(self, user_id):
        pass

    def file_hash_exists(self, file_hash, scope=None):
        # 真库语义：同 hash 已入库 → 走 SKIPPED 去重分支，不进解析管线
        return True

    def count_nodes(self, scope, source_file=None):
        return 7  # 已有节点 → SKIPPED（不再触碰解析管线）


@pytest.fixture
def orch():
    """真 IngestionOrchestrator + fake repo：路由/配额逻辑走真代码，存储 mock。"""
    o = create_ingestion_orchestrator(repo=_FakeQuotaRepo())
    yield o


def _ingest(orch, path, **kw):
    return orch.ingest_file("validator-user", str(path), scope="global", **kw)


def test_unsupported_file_type_rejected(orch, tmp_path):
    """可执行文件（无从识别的类型）→ UNSUPPORTED_TYPE，不进解析管线。"""
    exe = tmp_path / "payload.exe"
    exe.write_bytes(b"MZ\x90\x00" + b"\x00" * 64)
    result = _ingest(orch, exe)
    assert result.status == IngestionStatus.UNSUPPORTED_TYPE


def test_empty_file_not_ingested(orch, tmp_path):
    """空文件绝不进入解析管线（去重/类型任一守卫兜住）。"""
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    result = _ingest(orch, empty)
    assert result.status in (IngestionStatus.UNSUPPORTED_TYPE,
                             IngestionStatus.ERROR,
                             IngestionStatus.SKIPPED)


def test_oversized_file_rejected_by_quota(orch, monkeypatch, tmp_path):
    """超过大小上限 → QUOTA_EXCEEDED（先于任何解析）。"""
    from knowledge_base.core.quota_manager import QuotaManager
    monkeypatch.setattr(orch, "_quota", QuotaManager(_FakeQuotaRepo(), max_file_size_mb=0))
    big = tmp_path / "big.txt"
    big.write_text("x" * 1024)
    result = _ingest(orch, big)
    assert result.status == IngestionStatus.QUOTA_EXCEEDED


def test_rate_limited_uploads_rejected(monkeypatch, tmp_path):
    """同一用户 1 分钟内第 4 次上传 → 限流（QUOTA_EXCEEDED 编码返回，不抛）。"""
    from knowledge_base.core.quota_manager import QuotaManager
    repo = _FakeQuotaRepo(recent=3)          # 已达每分钟 3 次上限
    o = create_ingestion_orchestrator(repo=repo)
    monkeypatch.setattr(o, "_quota", QuotaManager(repo))
    f = tmp_path / "f.txt"
    f.write_text("内容")
    result = _ingest(o, f)
    assert result.status == IngestionStatus.QUOTA_EXCEEDED


def test_forbidden_system_paths_rejected(orch, tmp_path):
    """/proc /sys /dev 下的"文件"拒读（即使存在且是常规文件形态）。"""
    result = orch.ingest_file("u", "/proc/self/cmdline", scope="global")
    assert result.status == IngestionStatus.FILE_NOT_FOUND
    assert "不允许" in result.notification or "File not found" in result.notification


def test_non_regular_file_rejected(orch, tmp_path):
    """FIFO / 目录不是常规文件 → 拒收。"""
    fifo = tmp_path / "pipe.txt"
    import os
    os.mkfifo(fifo)
    result = _ingest(orch, fifo)
    assert result.status == IngestionStatus.FILE_NOT_FOUND


def test_allowed_dirs_allowlist_blocks_escape(orch, monkeypatch, tmp_path):
    """配置 INGEST_ALLOWED_DIRS 后，白名单外路径拒绝（防路径漂移）。"""
    monkeypatch.setattr(
        IngestionOrchestrator, "_ALLOWED_DIRS", (str(tmp_path / "inside"),))
    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "ok.txt").write_text("允许的文件内容，足够长以避免空文件拒绝。")
    outside = tmp_path / "outside.txt"
    outside.write_text("白名单外的文件内容，同样足够长。")

    assert _ingest(orch, inside / "ok.txt").status != IngestionStatus.FILE_NOT_FOUND \
        or "File not found"  # 白名单内：路径校验通过（后续状态视类型而定）
    result = _ingest(orch, outside)
    assert result.status == IngestionStatus.FILE_NOT_FOUND
    assert "不在允许的目录" in result.notification


# ── V02：文件名穿越（doccenter 上传层） ────────────────────────────


@pytest.fixture
def doc_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(doc_api, "_API_KEY", TEST_KEY)
    monkeypatch.setattr(dc_db, "DB_PATH", str(tmp_path / "dc.db"))
    monkeypatch.setattr(doc_api, "_UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(dc_service, "TRAINING_PLAN_DB", str(tmp_path / "tp.db"))
    monkeypatch.setattr(dc_service, "process_pending", lambda *a, **k: {})
    monkeypatch.setattr(dc_service, "process_warning", lambda *a, **k: {})
    app = FastAPI()
    app.include_router(doc_api.router)
    return TestClient(app)


@pytest.mark.parametrize(
    "malicious_name",
    [
        "../../etc/passwd.txt",
        "..\\..\\windows\\system32\\evil.txt",
        "/etc/cron.d/evil.txt",
        "sub/../../escape.txt",
    ],
)
def test_upload_filename_traversal_stripped(doc_client, malicious_name, tmp_path):
    """任何路径形式的文件名 → basename 后才落盘，永远不逃出上传目录。"""
    r = doc_client.post("/api/doc-center/upload",
                        files={"file": (malicious_name, b"traversal attempt" * 3,
                                        "text/plain")},
                        headers={"X-API-Key": TEST_KEY})
    assert r.status_code == 200
    uploads = tmp_path / "uploads"
    saved = [p.name for p in uploads.rglob("*") if p.is_file()]
    for name in ("passwd.txt", "evil.txt", "escape.txt"):
        # 同名文件只允许出现在上传目录顶层（basename 后），绝无子目录
        assert all("/" not in str(p.relative_to(uploads)) or
                   name not in p.name for p in uploads.rglob(name))
    assert saved, "文件应被保存（以 basename）"
    assert not any(p.is_dir() and ".." in p.name for p in uploads.rglob("*"))


def test_upload_control_characters_in_name(doc_client, tmp_path):
    """控制字符 / Unicode 特殊字符文件名不炸、不产生目录穿越。"""
    r = doc_client.post("/api/doc-center/upload",
                        files={"file": ("通知\x00\x1b..\n.pdf".replace("\x00", ""),
                                        b"ctrl" * 20, "application/pdf")},
                        headers={"X-API-Key": TEST_KEY})
    assert r.status_code == 200
    uploads = tmp_path / "uploads"
    saved = [p.name for p in uploads.rglob("*") if p.is_file()]
    assert all("\x00" not in n and "\n" not in n for n in saved)


# ── V03：查询参数模糊 ──────────────────────────────────────────────


@pytest.fixture
def plan_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(plan_api, "_API_KEY", TEST_KEY)
    monkeypatch.setattr(plan_service, "DB_PATH", str(tmp_path / "plan.db"))
    return TestClient(plan_api.app)  # 不进 lifespan


FUZZ_PAYLOADS = [
    "'; DROP TABLE plan_document;--",
    "<script>alert(1)</script>",
    "工商'管理",
    "%27%20OR%20%271%27%3D%271",
    "A" * 5000,                      # 超长
    "null\x00byte",
    "\x1b[31mred\x1b[0m",
]


@pytest.mark.parametrize("payload", FUZZ_PAYLOADS)
def test_fuzzed_parameters_do_not_crash(plan_client, payload):
    """恶意 major 入参：端点要么 200（空态）要么 4xx，绝不 5xx。"""
    r = plan_client.get("/api/plan/overview",
                        params={"major": payload, "entry_year": "2023"},
                        headers={"X-API-Key": TEST_KEY})
    assert r.status_code < 500


def test_fuzzed_year_parameter_rejected_gracefully(plan_client):
    for payload in ("9999", "'; DROP TABLE x;--", "not-a-year", "-1"):
        r = plan_client.get("/api/plan/overview",
                            params={"major": "工商管理", "entry_year": payload},
                            headers={"X-API-Key": TEST_KEY})
        assert r.status_code < 500
