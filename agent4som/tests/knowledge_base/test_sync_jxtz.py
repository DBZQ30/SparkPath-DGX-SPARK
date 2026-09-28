"""scripts/sync_jxtz.py 纯逻辑测试（556 行，此前零覆盖）。

离线：requests.Session 全部用 fake 替换，不触外网；入库管线不初始化
（只测 URL 比对 / HTML 解析 / 重试语义 / 状态分类等纯函数）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sync_jxtz.py"
_spec = importlib.util.spec_from_file_location("sync_jxtz_under_test", _SCRIPT)
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


# ── simple_hash（WAF 挑战） ─────────────────────────────────────────


def test_simple_hash_matches_js_semantics():
    # 32 位有符号溢出回绕（JS simpleHash 行为），结果非负
    h = sync.simple_hash("a" * 100)
    assert isinstance(h, int) and 0 <= h <= 2**31 - 1
    # 确定性 + 对输入敏感
    assert sync.simple_hash("challenge1") == sync.simple_hash("challenge1")
    assert sync.simple_hash("challenge1") != sync.simple_hash("challenge2")


# ── parse_list_page ─────────────────────────────────────────────────


def _list_html(entries: list[tuple[str, str]]) -> str:
    """构造符合模式1的列表页 HTML：(href 相对路径, 标题)。"""
    parts = []
    for href, title in entries:
        parts.append(
            f'<a href="{href}">{title}</a>\n'
            f'<span class="date">2026-03-01</span>'
        )
    return "<html><body>" + "\n".join(parts) + "</body></html>"


def test_parse_list_page_extracts_entries():
    html = _list_html([
        ("../../info/1214/111.htm", "[教务]关于2026年春季学期选课的通知"),
        ("../../info/1214/222.htm", "关于期中考试安排的说明文件"),
    ])
    items = sync.parse_list_page(html)
    assert len(items) == 2
    first = items[0]
    assert first["title"] == "[教务]关于2026年春季学期选课的通知"
    assert first["category"] == "教务"
    assert first["date"] == "2026-03-01"
    assert first["url"] == f"{sync.BASE_URL}/info/1214/111.htm"
    # 无分类前缀 → category 为空
    assert items[1]["category"] == ""


def test_parse_list_page_skips_short_titles():
    html = _list_html([("../../info/1214/333.htm", "太短")])
    assert sync.parse_list_page(html) == []


def test_parse_list_page_supports_content_jsp_pattern():
    html = (
        '<a href="../../content.jsp?urltype=news.NewsContentUrl&wbtreeid=1234&wbnewsid=5678">'
        '关于教学实践活动的安排说明</a><span>2026-04-02</span>'
    )
    items = sync.parse_list_page(html)
    assert len(items) == 1
    # content.jsp 也归一化为 /info/{treeid}/{newsid}.htm
    assert items[0]["url"] == f"{sync.BASE_URL}/info/1234/5678.htm"


# ── fetch_latest_notices（fake session，含分页） ────────────────────


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code


class _FakeSession:
    """url → 响应/异常 的静态路由表版 requests.Session。"""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.requested: list[str] = []

    def get(self, url, timeout=None):
        self.requested.append(url)
        target = self.routes[url]
        if isinstance(target, Exception):
            raise target
        return target


def test_fetch_latest_notices_dedups_and_pages():
    first = _FakeResponse(
        _list_html([("../../info/1214/1.htm", "第一条通知标题甲")]) +
        '<a href="jxtz2/2.htm">2</a>')
    second = _FakeResponse(
        _list_html([("../../info/1214/1.htm", "第一条通知标题甲"),   # 与第 1 页重复
                    ("../../info/1214/2.htm", "第二条通知标题乙")]))
    session = _FakeSession({
        sync.LIST_URL: first,
        f"{sync.BASE_URL}/jxxx/jxtz2/2.htm": second,
    })
    items = sync.fetch_latest_notices(session, max_pages=3)
    urls = [n["url"] for n in items]
    assert urls.count(f"{sync.BASE_URL}/info/1214/1.htm") == 1  # 跨页去重
    assert f"{sync.BASE_URL}/info/1214/2.htm" in urls
    # 确实翻了第 2 页
    assert f"{sync.BASE_URL}/jxxx/jxtz2/2.htm" in session.requested


def test_fetch_latest_notices_empty_when_list_unreachable(monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "TMP_DIR", tmp_path)
    monkeypatch.setattr(sync, "SYNC_LOG", tmp_path / "sync.log")
    session = _FakeSession({sync.LIST_URL: ConnectionError("refused")})
    assert sync.fetch_latest_notices(session) == []


# ── fetch_notice_body（永久失败 vs 临时失败语义） ────────────────────


def _detail_html(body: str) -> str:
    return (
        '<html><body><div id="vsb_content">'
        f"<p>{'正文内容' * 20}</p><p>{body}</p>"
        "</div></div></div></body></html>"
    )


def test_fetch_notice_body_404_is_permanent():
    session = _FakeSession({"http://x/1.htm": _FakeResponse("", status_code=404)})
    body, permanent = sync.fetch_notice_body(session, "http://x/1.htm")
    assert body is None and permanent is True


def test_fetch_notice_body_network_error_is_temporary():
    session = _FakeSession({"http://x/1.htm": ConnectionError("reset")})
    body, permanent = sync.fetch_notice_body(session, "http://x/1.htm")
    assert body is None and permanent is False


def test_fetch_notice_body_waf_page_is_temporary(monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "TMP_DIR", tmp_path)
    monkeypatch.setattr(sync, "SYNC_LOG", tmp_path / "sync.log")
    session = _FakeSession({"http://x/1.htm": _FakeResponse(
        "<html>var challengeId='abc'; loader()</html>")})
    body, permanent = sync.fetch_notice_body(session, "http://x/1.htm")
    assert body is None and permanent is False


def test_fetch_notice_body_extracts_and_strips_html(monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "TMP_DIR", tmp_path)
    monkeypatch.setattr(sync, "SYNC_LOG", tmp_path / "sync.log")
    html = (
        '<html><body><div id="vsb_content">'
        "<script>alert(1)</script><style>.a{}</style>"
        "<p>第一段通知正文内容，长度需超过五十字符才算可靠正文，这里补足长度。</p>"
        "<p>第二段&nbsp;含实体与<b>加粗</b>标记，进一步增加正文长度。</p>"
        "</div></div></div></body></html>"
    )
    session = _FakeSession({"http://x/1.htm": _FakeResponse(html)})
    body, permanent = sync.fetch_notice_body(session, "http://x/1.htm")
    assert body is not None and permanent is False
    assert "alert" not in body          # script 已剥离
    assert ".a{}" not in body           # style 已剥离
    assert "<b>" not in body            # 标签替换为换行
    assert "加粗" in body               # 文本保留
    assert "\n\n" not in body           # 连续空行折叠


def test_fetch_notice_body_too_short_is_temporary(monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "TMP_DIR", tmp_path)
    monkeypatch.setattr(sync, "SYNC_LOG", tmp_path / "sync.log")
    html = '<html><body><div id="vsb_content">短</div></div></div></body></html>'
    session = _FakeSession({"http://x/1.htm": _FakeResponse(html)})
    body, permanent = sync.fetch_notice_body(session, "http://x/1.htm")
    assert body is None and permanent is False


# ── classify_status / 错误摘要 ───────────────────────────────────────


@pytest.mark.parametrize(
    "new,ok,skip,fail,expected",
    [
        (0, 0, 0, 0, "no_new"),
        (3, 3, 0, 0, "ok"),
        (3, 1, 1, 0, "ok"),          # skip 计入成功
        (3, 1, 1, 1, "partial"),
        (3, 0, 0, 3, "failed"),
    ],
)
def test_classify_status(new, ok, skip, fail, expected):
    assert sync.classify_status(new, ok, skip, fail) == expected


def test_sanitize_error_truncates_and_flattens():
    exc = ValueError("line1\nline2 " + "x" * 300)
    out = sync.sanitize_error(exc)
    assert out.startswith("ValueError: line1 line2")
    assert len(out) <= 200 + len("ValueError: ") + 1
    assert "\n" not in out


def test_summarize_error_joins_and_caps():
    assert sync.summarize_error([]) == ""
    assert sync.summarize_error(["a", "b"]) == "a; b"
    long = [str(i) for i in range(10)]
    assert len(sync.summarize_error(long)) <= 200


# ── parse_args / 互斥锁 ─────────────────────────────────────────────


def test_parse_args_defaults():
    args = sync.parse_args([])
    assert args.trigger == "schedule" and args.by == "" and args.run_id == ""


def test_acquire_lock_blocks_second_holder(monkeypatch, tmp_path):
    lock_file = tmp_path / "sync.lock"
    monkeypatch.setattr(sync, "LOCK_FILE", lock_file)
    fd1 = sync.acquire_lock()
    assert fd1 is not None
    try:
        assert sync.acquire_lock() is None  # 非阻塞，第二次获取失败
    finally:
        fd1.close()
    # 释放后可重新获取
    fd2 = sync.acquire_lock()
    assert fd2 is not None
    fd2.close()


def test_append_run_record_writes_jsonl(monkeypatch, tmp_path):
    runs = tmp_path / "runs.jsonl"
    monkeypatch.setattr(sync, "RUNS_FILE", runs)
    args = sync.parse_args(["--trigger", "manual", "--by", "openid-x", "--run-id", "rid-1"])
    sync.append_run_record("rid-1", "ok", args, 2, 1, 1, 0, "",
                           [{"title": "t", "url": "u", "status": "ingested", "nodes": 3}])
    record = json.loads(runs.read_text(encoding="utf-8").strip())
    assert record["run_id"] == "rid-1"
    assert record["trigger"] == "manual" and record["by"] == "openid-x"
    assert record["status"] == "ok" and record["new"] == 2
    assert record["items"][0]["nodes"] == 3
