"""scripts/sync_jxtz.py 纯逻辑与管线阶段测试（此前零覆盖，离线）。

不触外部网络与生产 data/：DATA_FILE/RUNS_FILE/TMP_DIR/SYNC_LOG 全部
monkeypatch 到 tmp_path；WAF / 抓取 / 入库均打桩。
"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_SRC = Path(__file__).resolve().parents[2] / "scripts" / "sync_jxtz.py"
_spec = importlib.util.spec_from_file_location("sync_jxtz_under_test", _SRC)
sj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sj)


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path, monkeypatch):
    """模块级路径常量全部指向 tmp_path，杜绝写生产 data/。"""
    monkeypatch.setattr(sj, "DATA_FILE", tmp_path / "notices.jsonl")
    monkeypatch.setattr(sj, "RUNS_FILE", tmp_path / "runs.jsonl")
    monkeypatch.setattr(sj, "TMP_DIR", tmp_path / "tmp")
    tmp_path.joinpath("tmp").mkdir()   # 生产路径由 _sync_once 循环前 mkdir
    monkeypatch.setattr(sj, "SYNC_LOG", tmp_path / "sync.log")


# ── 纯函数 ──────────────────────────────────────────────────────────


def test_classify_status_four_states():
    assert sj.classify_status(0, 0, 0, 0) == "no_new"
    assert sj.classify_status(2, 2, 0, 0) == "ok"
    assert sj.classify_status(2, 0, 1, 1) == "partial"   # skip 计入成功
    assert sj.classify_status(2, 0, 0, 2) == "failed"


def test_summarize_error_truncates():
    assert sj.summarize_error([]) == ""
    assert sj.summarize_error(["a", "b", "c", "d"]) == "a; b; c"   # 只取前 3
    assert len(sj.summarize_error(["x" * 300])) <= 200


# ── JSONL 账本读写 ─────────────────────────────────────────────────


def test_load_existing_urls_skips_bad_lines():
    sj.DATA_FILE.write_text(
        json.dumps({"url": "https://x/1.htm"}) + "\n"
        + "not json\n"
        + json.dumps({"nourl": 1}) + "\n"
        + json.dumps({"url": "https://x/2.htm"}) + "\n",
        encoding="utf-8")
    assert sj._load_existing_urls() == {"https://x/1.htm", "https://x/2.htm"}
    # 文件不存在 → 空集
    sj.DATA_FILE.unlink()
    assert sj._load_existing_urls() == set()


def test_prepend_committed_keeps_order_and_old_lines():
    sj.DATA_FILE.write_text(json.dumps({"url": "old"}) + "\n", encoding="utf-8")
    sj._prepend_committed([{"url": "new1"}, {"url": "new2"}])
    lines = [json.loads(l) for l in sj.DATA_FILE.read_text(encoding="utf-8").splitlines()]
    assert [l["url"] for l in lines] == ["new1", "new2", "old"]
    # 空 committed → 不动文件
    before = sj.DATA_FILE.read_text(encoding="utf-8")
    sj._prepend_committed([])
    assert sj.DATA_FILE.read_text(encoding="utf-8") == before


# ── _ingest_one_notice：抓取/入库各分支 ───────────────────────────


def _notice(url="https://x/n1.htm"):
    return {"url": url, "title": "关于课程调整的通知", "category": "教务",
            "date": "2026-09-26"}


class _Result:
    def __init__(self, status, nodes=3, notification=""):
        self.status = SimpleNamespace(value=status)
        self.node_count = nodes
        self.notification = notification


def _no_waf(monkeypatch, *, body="正文", permanent=False):
    monkeypatch.setattr(sj, "fetch_notice_body", lambda s, u: (body, permanent))
    monkeypatch.setattr(sj, "solve_challenge", lambda s: True)


def test_ingest_one_permanent_404(monkeypatch):
    monkeypatch.setattr(sj, "fetch_notice_body", lambda s, u: (None, True))
    fake_orch = SimpleNamespace(ingest_file=lambda *a, **kw: pytest.fail("不应入库"))
    r = sj._ingest_one_notice(None, fake_orch, _notice())
    assert r["outcome"] == "fail" and r["commit"] is not None   # 404 记录在案防重试
    assert r["item"]["status"] == "file_not_found"
    assert "页面不存在" in r["reason"]


def test_ingest_one_temp_fetch_failure_not_committed(monkeypatch):
    monkeypatch.setattr(sj, "fetch_notice_body", lambda s, u: (None, False))
    monkeypatch.setattr(sj, "solve_challenge", lambda s: True)  # 重试后仍失败
    fake_orch = SimpleNamespace(ingest_file=lambda *a, **kw: pytest.fail("不应入库"))
    r = sj._ingest_one_notice(None, fake_orch, _notice())
    assert r["outcome"] == "fail" and r["commit"] is None       # 临时失败 → 下次重试
    assert r["item"]["status"] == "error"
    assert "抓取失败" in r["reason"]


def test_ingest_one_retry_after_waf(monkeypatch):
    """临时失败 → 重新解挑战 → 第二次抓取成功入库。"""
    calls = {"n": 0}

    def _fb(s, u):
        calls["n"] += 1
        return (None, False) if calls["n"] == 1 else ("正文", False)

    monkeypatch.setattr(sj, "fetch_notice_body", _fb)
    solved = {"n": 0}
    monkeypatch.setattr(sj, "solve_challenge", lambda s: (solved.__setitem__("n", solved["n"] + 1) or True))
    fake_orch = SimpleNamespace(ingest_file=lambda *a, **kw: _Result("ingested"))
    r = sj._ingest_one_notice(None, fake_orch, _notice())
    assert r["outcome"] == "ok" and r["commit"] == _notice()
    assert calls["n"] == 2 and solved["n"] == 1   # 重试了一次抓取
    assert r["item"] == {"title": "关于课程调整的通知", "url": "https://x/n1.htm",
                         "status": "ingested", "nodes": 3}


def test_ingest_one_statuses_and_exception(monkeypatch):
    _no_waf(monkeypatch)
    notice = _notice()
    ok_orch = SimpleNamespace(ingest_file=lambda *a, **kw: _Result("replaced", nodes=5))
    r = sj._ingest_one_notice(None, ok_orch, notice)
    assert r["outcome"] == "ok" and r["item"]["status"] == "replaced"

    skip_orch = SimpleNamespace(ingest_file=lambda *a, **kw: _Result("skipped", nodes=7))
    r = sj._ingest_one_notice(None, skip_orch, notice)
    assert r["outcome"] == "skip" and r["commit"] == notice   # 已在库也记入防重复

    def _boom(*a, **kw):
        raise RuntimeError("chroma down")
    boom_orch = SimpleNamespace(ingest_file=_boom)
    r = sj._ingest_one_notice(None, boom_orch, notice)
    assert r["outcome"] == "fail" and r["commit"] is None
    assert r["reason"] == "入库异常 RuntimeError"
    # 临时文件已清理
    assert not list(sj.TMP_DIR.glob("jxtz_*"))


def test_ingest_one_writes_source_header(monkeypatch):
    """临时文件正文含来源头（供检索结果回链 来源 URL）。"""
    _no_waf(monkeypatch, body="课程调整正文")
    captured = {}

    def _ingest(uid, path, **kw):
        captured["path"] = path
        with open(path, encoding="utf-8") as f:
            captured["content"] = f.read()
        return _Result("ingested")

    sj._ingest_one_notice(None, SimpleNamespace(ingest_file=_ingest), _notice())
    assert captured["content"].startswith(
        "来源: https://x/n1.htm\n发布日期: 2026-09-26\n类别: 教务\n"
        "标题: 关于课程调整的通知\n来源类型: web_notice\n\n课程调整正文")
    assert "jxtz_n1_" in Path(captured["path"]).name   # record_id 命名


# ── _sync_once：主流程状态机（全桩） ──────────────────────────────


def _args():
    return SimpleNamespace(trigger="test", by="pytest")


def test_sync_once_no_new(monkeypatch):
    monkeypatch.setattr(sj.requests, "Session", lambda: SimpleNamespace(
        headers=SimpleNamespace(update=lambda *a: None)))
    monkeypatch.setattr(sj, "_load_existing_urls", lambda: {"https://x/1.htm"})
    monkeypatch.setattr(sj, "_fetch_latest_with_retry",
                        lambda s: [{"url": "https://x/1.htm", "title": "t",
                                    "category": "c", "date": "d"}])
    assert sj._sync_once(_args(), "run-1") == 0
    record = json.loads(sj.RUNS_FILE.read_text(encoding="utf-8"))
    assert record["status"] == "no_new" and record["trigger"] == "test"


def test_sync_once_all_fail_exit_1(monkeypatch):
    monkeypatch.setattr(sj.requests, "Session", lambda: SimpleNamespace(
        headers=SimpleNamespace(update=lambda *a: None)))
    monkeypatch.setattr(sj, "_load_existing_urls", lambda: set())
    monkeypatch.setattr(sj, "_fetch_latest_with_retry",
                        lambda s: [{"url": "https://x/1.htm", "title": "t",
                                    "category": "c", "date": "d"}])
    monkeypatch.setattr(sj, "_init_ingest_pipeline", lambda: object())
    monkeypatch.setattr(
        sj, "_ingest_one_notice",
        lambda s, o, n: {"outcome": "fail", "commit": None, "reason": "抓取失败 x",
                         "item": {"title": "t", "url": n["url"], "status": "error",
                                  "nodes": 0}})
    assert sj._sync_once(_args(), "run-2") == 1
    record = json.loads(sj.RUNS_FILE.read_text(encoding="utf-8"))
    assert record["status"] == "failed" and record["fail"] == 1
    assert record["error"] == "抓取失败 x"
    # 临时失败不入账本 → DATA_FILE 不存在
    assert not sj.DATA_FILE.exists()


def test_sync_once_pipeline_init_failure(monkeypatch):
    monkeypatch.setattr(sj.requests, "Session", lambda: SimpleNamespace(
        headers=SimpleNamespace(update=lambda *a: None)))
    monkeypatch.setattr(sj, "_load_existing_urls", lambda: set())
    monkeypatch.setattr(sj, "_fetch_latest_with_retry",
                        lambda s: [{"url": "https://x/1.htm", "title": "t",
                                    "category": "c", "date": "d"}])

    def _boom():
        raise RuntimeError("token leaked?")
    monkeypatch.setattr(sj, "_init_ingest_pipeline", _boom)
    assert sj._sync_once(_args(), "run-3") == 1
    record = json.loads(sj.RUNS_FILE.read_text(encoding="utf-8"))
    # 固定文案，不写含 token 的原始异常
    assert record["error"] == "入库管线初始化失败"


def test_fetch_latest_failure_exit_1(monkeypatch):
    monkeypatch.setattr(sj.requests, "Session", lambda: SimpleNamespace(
        headers=SimpleNamespace(update=lambda *a: None)))
    monkeypatch.setattr(sj, "_fetch_latest_with_retry", lambda s: [])
    assert sj._sync_once(_args(), "run-4") == 1
    record = json.loads(sj.RUNS_FILE.read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["error"] == "未获取到任何通知（WAF / 页面结构 / 网络）"
