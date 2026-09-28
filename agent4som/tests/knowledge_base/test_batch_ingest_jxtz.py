"""scripts/batch_ingest_jxtz.py 纯逻辑测试（329 行，此前零覆盖）。

离线：requests / 入库管线全部不初始化，只测纯函数与 _fetch_notice 的
HTML 解析（fake session 不触外网）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "batch_ingest_jxtz.py"
_spec = importlib.util.spec_from_file_location("batch_ingest_jxtz_under_test", _SCRIPT)
batch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(batch)


# ── 纯函数 ──────────────────────────────────────────────────────────


def test_notice_filename_truncation():
    f = batch._notice_filename("教务", "关于2026年放假安排的通知")
    assert f == "[教务] 关于2026年放假安排的通知"
    # 无类别 → 裸标题
    assert batch._notice_filename("", "标题") == "标题"
    # 超 200 字截断为 197 + "..."
    long = batch._notice_filename("", "长" * 250)
    assert len(long) == 200 and long.endswith("...")


def test_pub_date_from_normalization():
    assert batch._pub_date_from("2024/3/5") == "2024-03-05"
    assert batch._pub_date_from("2024年3月5日") == "2024-03-05"
    assert batch._pub_date_from("2024-12-25") == "2024-12-25"
    assert batch._pub_date_from("") == ""   # 无匹配 → 空


def test_load_notices_and_write_load_resume_roundtrip(tmp_path, monkeypatch):
    in_file = tmp_path / "in.jsonl"
    in_file.write_text(
        '{"url": "https://x/1.htm", "title": "a"}\n\n'
        '{"url": "https://x/2.htm", "title": "b"}\n', encoding="utf-8")
    monkeypatch.setattr(batch, "INPUT_FILE", str(in_file))
    assert [n["title"] for n in batch._load_notices()] == ["a", "b"]   # 空行跳过

    out_file = tmp_path / "out.jsonl"
    monkeypatch.setattr(batch, "OUTPUT_FILE", str(out_file))
    batch._write_results([
        {"status": "ok"}, {"status": "fail"}, {"status": "skip"}])
    _results, resume_idx, ok, fail, skip = batch._load_resume()
    assert resume_idx == 3 and (ok, fail, skip) == (1, 1, 1)
    # 原子写：不留 .tmp 残留
    assert not (tmp_path / "out.jsonl.tmp").exists()


def test_load_resume_skips_corrupted_lines(tmp_path, monkeypatch):
    out_file = tmp_path / "out.jsonl"
    out_file.write_text(
        '{"status": "ok"}\n{{corrupted\n{"status": "skip"}\n', encoding="utf-8")
    monkeypatch.setattr(batch, "OUTPUT_FILE", str(out_file))
    _results, resume_idx, ok, fail, skip = batch._load_resume()
    assert resume_idx == 2 and (ok, fail, skip) == (1, 0, 1)


# ── _fetch_notice（fake session，不触外网） ─────────────────────────


class _Resp:
    def __init__(self, text: str, encoding: str = "utf-8"):
        self.content = text.encode(encoding)
        self.text = text


class _Session:
    def __init__(self, resp):
        self._resp = resp

    def get(self, url, timeout=0):
        return self._resp


def test_fetch_notice_parses_vsb_content():
    html = ("<html><title>关于放假的通知</title>"
            "<div id=\"vsb_content\"><p>2024年3月5日</p>"
            + "<p>通知正文，" + "内容" * 40 + "</p></div></div></div></html>")
    d = batch._fetch_notice(_Session(_Resp(html)), "https://x/1.htm")
    assert d["title"] == "关于放假的通知" and d["date"] == "2024年3月5"
    assert "通知正文" in d["body"] and d["url"] == "https://x/1.htm"


def test_fetch_notice_error_branches():
    # WAF 挑战页（challengeId + loader）
    waf = batch._fetch_notice(_Session(_Resp("<html>challengeId loader</html>")),
                              "https://x/")
    assert waf == {"error": "waf_blocked"}
    # 无内容 div
    no_div = batch._fetch_notice(
        _Session(_Resp("<html><title>通知</title><body>西安" + "x" * 3000
                       + "</body></html>")), "https://x/")
    assert no_div == {"error": "no_content_div"}
    # 内容过短
    short = batch._fetch_notice(
        _Session(_Resp("<html><div id=\"vsb_content\">通知短</div></div></div>"
                       "</html>")), "https://x/")
    assert short == {"error": "content_too_short"}
    # 网络异常 → fetch 错误记录
    class _Boom:
        def get(self, url, timeout=0):
            raise ConnectionError("reset")
    assert "fetch: reset" in batch._fetch_notice(_Boom(), "https://x/")["error"]
