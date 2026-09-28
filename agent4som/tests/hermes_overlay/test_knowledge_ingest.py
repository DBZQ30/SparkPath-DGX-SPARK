"""hermes_overlay/tools/knowledge_ingest.py 纯逻辑与权限测试（此前零覆盖）。

离线：不开 Chroma、不触 WeCom 缓存目录；handle 级用例只测空文件名守卫
（不进入缓存查找）。入库权限由 check_write_permission 把守（fail-closed）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "hermes_overlay" / "tools" / "knowledge_ingest.py"
_spec = importlib.util.spec_from_file_location("knowledge_ingest_under_test", _SRC)
ki = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ki)


@pytest.fixture(autouse=True)
def _clear_guard_counter():
    ki._EMPTY_FILENAME_COUNTS.clear()
    # QueryContext contextvar 全局可见——用完复位，别污染其他用例
    from knowledge_base.core.query_context import QueryContext, inject_context
    inject_context(QueryContext())
    yield
    inject_context(QueryContext())
    ki._EMPTY_FILENAME_COUNTS.clear()


# ── _normalize_filename / _normalize_for_match ─────────────────────


def test_normalize_filename_strips_wecom_cache_prefix():
    assert ki._normalize_filename("doc_a1b2c3d4e5f6_课程表.docx") == "课程表.docx"
    # 普通文件名原样返回
    assert ki._normalize_filename("通知.pdf") == "通知.pdf"
    # 前缀不匹配（大写 hex / 长度不对）不剥离
    assert ki._normalize_filename("doc_A1B2C3D4E5F6_x.doc") == "doc_A1B2C3D4E5F6_x.doc"
    assert ki._normalize_filename("doc_a1b2_x.doc") == "doc_a1b2_x.doc"


def test_normalize_for_match_keeps_semantic_core():
    # WeCom 会把中文标点打碎：只留汉字/字母/数字/扩展名，统一小写
    assert ki._normalize_for_match("《高数(下)复习提纲》.pdf") == "高数下复习提纲.pdf"
    assert ki._normalize_for_match("Doc_ABC123_Report.PDF") == "docabc123report.pdf"
    # 缓存前缀先剥离再归一
    assert ki._normalize_for_match("doc_a1b2c3d4e5f6_通知.txt") == "通知.txt"


# ── _normalize_scopes ──────────────────────────────────────────────


def test_normalize_scopes_string():
    assert ki._normalize_scopes("global") == ["global"]
    assert ki._normalize_scopes("  teachers ") == ["teachers"]


def test_normalize_scopes_list():
    assert ki._normalize_scopes(["global", " users/u1 "]) == ["global", "users/u1"]
    # 非字符串与空白项被过滤；全空列表回退为自动推导
    assert ki._normalize_scopes(["", 42, None]) == [""]
    assert ki._normalize_scopes([]) == [""]


def test_normalize_scopes_empty_means_auto_derive():
    assert ki._normalize_scopes("") == [""]
    assert ki._normalize_scopes(None) == [""]
    assert ki._normalize_scopes("   ") == [""]


# ── _derive_scope ──────────────────────────────────────────────────


def test_derive_scope_explicit_request_wins():
    assert ki._derive_scope("student", "u1", "users/u1") == "users/u1"
    # student 请求 global 也不在此处拦——交给 check_write_permission（分层校验）
    assert ki._derive_scope("student", "u1", "global") == "global"


def test_derive_scope_empty_falls_back_to_role_default(monkeypatch):
    # 真实共享逻辑：admin/owner → global，teacher → teachers，其余 → 个人库
    assert ki._derive_scope("admin", "u1", "") == "global"
    assert ki._derive_scope("teacher", "u1", "") == "teachers"
    assert ki._derive_scope("student", "u1", "") == "users/u1"
    assert ki._derive_scope("", "u9", "") == "users/u9"


# ── _ingest_one（权限把门 + 结果文案） ─────────────────────────────


class _FakeResult:
    def __init__(self, status, node_count=3, file_size_bytes=100,
                 notification="", error_detail=""):
        self.status = status
        self.node_count = node_count
        self.file_size_bytes = file_size_bytes
        self.notification = notification
        self.error_detail = error_detail


class _FakeOrch:
    def __init__(self, result=None):
        self.result = result
        self.calls: list[dict] = []

    def ingest_file(self, user_id, file_path, *, scope, source, count_quota=True):
        self.calls.append({"user_id": user_id, "file_path": file_path,
                           "scope": scope, "count_quota": count_quota})
        return self.result


def test_ingest_one_denies_student_writing_global():
    """student 写 global → 权限不足文案，ingest_file 根本不被调用。"""
    orch = _FakeOrch()
    out = ki._ingest_one(orch, "u1", "/tmp/x.pdf", "x.pdf", "global", "student")
    assert "权限不足" in out
    assert orch.calls == []


def test_ingest_one_denies_student_writing_others_personal():
    orch = _FakeOrch()
    out = ki._ingest_one(orch, "u1", "/tmp/x.pdf", "x.pdf", "users/someone_else", "student")
    assert "权限不足" in out
    assert out.startswith("[users/someone_else]")
    assert orch.calls == []


def test_ingest_one_denies_teacher_writing_global():
    orch = _FakeOrch()
    out = ki._ingest_one(orch, "t1", "/tmp/x.pdf", "x.pdf", "global", "teacher")
    assert "权限不足" in out
    assert orch.calls == []


def test_ingest_one_admin_global_success(monkeypatch):
    from knowledge_base.ingestion.orchestrator import IngestionStatus
    from knowledge_base.retrieval import response_verifier
    monkeypatch.setattr(response_verifier, "_audit", None)  # 审计为 None 时静默跳过

    orch = _FakeOrch(_FakeResult(IngestionStatus.INGESTED, node_count=5))
    out = ki._ingest_one(orch, "adm1", "/cache/doc_a1b2c3d4e5f6_课程表.docx",
                         "课程表.docx", "global", "admin", count_quota=False)
    assert "[INGEST_RESULT: SUCCESS]" in out
    assert "课程表" in out and "global" in out
    # 多 scope 一次性调用 → 外层统一配额，逐 scope 均为 count_quota=False
    assert orch.calls[0]["scope"] == "global"
    assert orch.calls[0]["count_quota"] is False


def test_ingest_one_replaced_and_failed_paths(monkeypatch):
    from knowledge_base.ingestion.orchestrator import IngestionStatus
    from knowledge_base.retrieval import response_verifier
    monkeypatch.setattr(response_verifier, "_audit", None)

    orch = _FakeOrch(_FakeResult(IngestionStatus.REPLACED))
    assert "更新" in ki._ingest_one(orch, "adm1", "/x/f.txt", "f.txt", "global", "admin")

    orch_skip = _FakeOrch(_FakeResult(IngestionStatus.SKIPPED))
    assert "SKIPPED" in ki._ingest_one(orch_skip, "adm1", "/x/f.txt", "f.txt", "global", "admin")

    orch_fail = _FakeOrch(_FakeResult(IngestionStatus.ERROR,
                                      notification="解析失败"))
    out = ki._ingest_one(orch_fail, "adm1", "/x/f.txt", "f.txt", "global", "admin")
    assert "FAILED" in out and "解析失败" in out


# ── _find_file_in_cache fail-closed ────────────────────────────────


def test_find_file_in_cache_refuses_empty_user():
    """空 user_id / unknown 直接 None，绝不去扫别人的目录（fail-closed）。"""
    assert ki._find_file_in_cache("x.pdf", "") is None
    assert ki._find_file_in_cache("x.pdf", "unknown") is None


# ── handle_knowledge_ingest 空文件名守卫 ───────────────────────────


def _inject_ctx(user_id: str, platform: str = "wecom"):
    from knowledge_base.core.query_context import QueryContext, inject_context
    inject_context(QueryContext(platform=platform, user_id=user_id))


def test_handle_guard_prompts_then_escalates(monkeypatch):
    """空 filename：第 1 次温和提示，第 2 次 TOOL_GUARD 警告，第 3 次转自动补全。"""
    _inject_ctx("guard-user")

    # 前两次不进入缓存查找（直接返回提示）
    assert ki.handle_knowledge_ingest({}) == "请提供要入库的文件名。"
    assert "[TOOL_GUARD]" in ki.handle_knowledge_ingest({})

    # 第 3 次守卫放行 → 走自动补全；无缓存文件则仍提示提供文件名
    monkeypatch.setattr(ki, "_auto_detect_file", lambda uid: None)
    assert ki.handle_knowledge_ingest({}) == "请提供要入库的文件名。"


def test_handle_guard_resets_on_success(monkeypatch):
    _inject_ctx("guard-user-2")
    assert ki.handle_knowledge_ingest({}) == "请提供要入库的文件名。"

    # 有 filename（自动补全成功）→ 计数器清零
    monkeypatch.setattr(ki, "_auto_detect_file", lambda uid: "文件.pdf")
    monkeypatch.setattr(ki, "_find_file_in_cache", lambda fname, uid: None)
    import time as _time_mod
    monkeypatch.setattr(_time_mod, "sleep", lambda *_: None)  # 重试等待不真睡
    out = ki.handle_knowledge_ingest({"filename": "文件.pdf"})
    assert "未在缓存中找到文件" in out

    # 计数器已清零 → 再来一次空 filename 仍是第 1 次的温和提示
    assert ki.handle_knowledge_ingest({}) == "请提供要入库的文件名。"


# ── _wait_for_cached_file：缓存等待 + 归属校验 ─────────────────────


def test_wait_for_cached_file_owner_match(monkeypatch):
    """命中路径含 /documents/{safe_user}/ → 归属通过，原样返回。"""
    path = "/home/u/.hermes/cache/documents/u1/doc_abc_通知.docx"
    monkeypatch.setattr(ki, "_find_file_in_cache", lambda fname, uid: path)
    out, err = ki._wait_for_cached_file("通知.docx", "u1")
    assert out == path and err is None


def test_wait_for_cached_file_owner_mismatch(monkeypatch):
    """命中他人目录 → 安全错误文案（不重试、不返回路径）。"""
    path = "/home/u/.hermes/cache/documents/other/doc_abc_通知.docx"
    monkeypatch.setattr(ki, "_find_file_in_cache", lambda fname, uid: path)
    out, err = ki._wait_for_cached_file("通知.docx", "u1")
    assert out is None
    assert err == "安全校验失败：文件不属于当前用户。请重新发送文件。"


def test_wait_for_cached_file_not_found(monkeypatch):
    """4 轮重试全 miss → (None, None)，由调用方渲染未找到文案。"""
    monkeypatch.setattr(ki, "_find_file_in_cache", lambda fname, uid: None)
    import time as _time_mod
    monkeypatch.setattr(_time_mod, "sleep", lambda *_: None)
    assert ki._wait_for_cached_file("通知.docx", "u1") == (None, None)


def test_wait_for_cached_file_no_documents_segment_accepted(monkeypatch):
    """image_cache 命中（路径无 /documents/ 段）→ 不做归属比对，直接返回。"""
    path = "/home/u/.hermes/image_cache/u1/img_001.png"
    monkeypatch.setattr(ki, "_find_file_in_cache", lambda fname, uid: path)
    out, err = ki._wait_for_cached_file("img_001.png", "u1")
    assert out == path and err is None


# ── _check_quota_once：整调用一次配额 ──────────────────────────────


class _FakeQuota:
    def __init__(self, raise_exc=None):
        self.calls = []
        self._raise = raise_exc

    def check_quota(self, user_id, size, is_admin_or_owner):
        self.calls.append((user_id, size, is_admin_or_owner))
        if self._raise:
            raise self._raise


def test_check_quota_once_privileged_bypass(tmp_path):
    f = tmp_path / "x.docx"
    f.write_bytes(b"z")
    quota = _FakeQuota()
    orch = type("O", (), {"_quota": quota})()
    # owner/admin 直接放行，不查配额
    for role in ("owner", "admin"):
        assert ki._check_quota_once(orch, "u1", str(f), role) is None
    assert quota.calls == []


def test_check_quota_once_pass_and_fail(tmp_path):
    f = tmp_path / "x.docx"
    f.write_bytes(b"zz")
    quota = _FakeQuota()
    orch = type("O", (), {"_quota": quota})()
    assert ki._check_quota_once(orch, "u1", str(f), "student") is None
    assert quota.calls == [("u1", 2, False)]   # 文件大小透传，非特权口径

    quota_fail = _FakeQuota(raise_exc=RuntimeError("今日配额已用尽"))
    orch_fail = type("O", (), {"_quota": quota_fail})()
    msg = ki._check_quota_once(orch_fail, "u1", str(f), "student")
    assert msg == "[INGEST_RESULT: FAILED] 配额检查未通过：今日配额已用尽"
