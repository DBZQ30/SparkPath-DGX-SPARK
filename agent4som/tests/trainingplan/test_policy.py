"""政策解析（转专业 / 专业选择）单测 —— 设计 005 §6。

用 `data/SmartGuide` 的真实政策文件；库操作用临时库。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "data/SmartGuide"
TRANSFER = f"{BASE}/【发布】管理学院2026年本科生转专业实施细则.pdf"
EXAM = f"{BASE}/【发布】-2026年管理学院接收本科生转专业考核安排.pdf"
SELECT = f"{BASE}/【发布版】管理学院2025级本科生专业选择实施方案.pdf"
GUIDE = f"{BASE}/2023级管理类本科生专业选择志愿填报操作流程.docx"

pytestmark = [pytest.mark.samples, pytest.mark.skipif(
    not os.path.isfile(TRANSFER), reason="缺少政策样本")]


def test_classify_policy():
    from trainingplan import policy
    assert policy.classify_policy("管理学院2026年本科生转专业实施细则.pdf") == "转专业政策"
    assert policy.classify_policy("2026年接收本科生转专业考核安排.pdf") == "转专业考核安排"
    assert policy.classify_policy("管理学院2025级本科生专业选择实施方案.pdf") == "专业选择方案"
    assert policy.classify_policy("专业选择志愿填报操作流程.docx") == "操作指引"
    assert policy.classify_policy("高等数学大纲.docx") is None


def test_parse_transfer_quota():
    from trainingplan import policy
    r = policy.parse_transfer_doc(TRANSFER)
    assert r["policy_year"] == "2026"
    plans = {(p["major"], p["entry_year"], p["scope"]): p["quota"] for p in r["plans"]}
    assert plans[("大数据管理与应用", "2025级", "学院内")] == 3
    assert plans[("大数据管理与应用", "2025级", "跨学院")] == 5
    assert plans[("会计学（ACCA）", "2025级", "学院内")] == 5
    assert plans[("工商管理", "2023级", "学院内")] == 0   # 大三工商计划为 0
    assert len(r["plans"]) == 24


def test_parse_transfer_quota_header_driven():
    """年级列序由表头决定（不再硬编码 2025/2024/2023）。"""
    from trainingplan import policy
    text = (
        "专业名称\n"
        "2027 级（大一） 2026 级（大二） 2025 级（大三）\n"
        "学院内\n接收计划\n跨学院\n接收计划\n合计\n"
        "会计学(ACCA) 1 2 3 4 5 6 7 8 9\n"
        "总计 1 2 3 4 5 6 7 8 9\n"
    )
    plans = {(p["major"], p["entry_year"], p["scope"]): p["quota"]
             for p in policy.parse_transfer_quota(text)}
    assert plans[("会计学（ACCA）", "2027级", "学院内")] == 1
    assert plans[("会计学（ACCA）", "2027级", "跨学院")] == 2
    assert plans[("会计学（ACCA）", "2026级", "学院内")] == 4
    assert plans[("会计学（ACCA）", "2025级", "跨学院")] == 8


def test_parse_transfer_quota_fallback_without_header():
    """表头缺失时回退默认列序，保持既有行为。"""
    from trainingplan import policy
    plans = {(p["entry_year"], p["scope"]): p["quota"]
             for p in policy.parse_transfer_quota("工商管理 1 2 3 4 5 6 7 8 9\n")}
    assert plans[("2025级", "学院内")] == 1
    assert plans[("2023级", "跨学院")] == 8


def test_parse_transfer_rules():
    from trainingplan import policy
    r = policy.parse_transfer_doc(TRANSFER)
    items = {x["item"]: x for x in r["rules"]}
    assert items["专业志愿数"]["value"] == "1"
    assert "第三学年结束前" in items["推免红线"]["value"]
    assert "降级" in items["会计学（ACCA）"]["value"]
    assert items["拟录取公示"]["value"] == "2026-07-31 ~ 08-02"
    assert "应修课程表" in items["免补修条件"]["value"]


def test_parse_transfer_exam():
    from trainingplan import policy
    r = policy.parse_transfer_exam_doc(EXAM)
    assert r["policy_year"] == "2026"
    vals = {x["item"]: x["value"] for x in r["rules"]}
    assert vals["笔试-数学"] == "2026-07-22 8:30-10:00"
    assert vals["笔试-英语"] == "2026-07-22 10:30-12:00"
    assert vals["面试"].startswith("2026-07-22 14:30")


def test_parse_major_selection():
    from trainingplan import policy
    r = policy.parse_major_selection_doc(SELECT)
    assert r["entry_year"] == "2025级"
    quotas = {p["major"]: p["quota"] for p in r["plans"]}
    assert quotas == {"工商管理": 33, "工业工程": 33, "大数据管理与应用": 32}
    items = {x["item"]: x for x in r["rules"]}
    assert items["专业志愿数"]["value"] == "3"
    assert "70%学业成绩" in items["综合成绩"]["value"]
    assert "补考/重修及格按 60 分计" in items["计算规则"]["value"]


def test_parse_guide_registers_only():
    from trainingplan import policy
    if not os.path.isfile(GUIDE):
        pytest.skip("缺少操作流程样本")
    assert policy.parse_policy_file(GUIDE) is None   # 操作指引不做结构化解析


# ─────────────────────────── 入库往返 ───────────────────────────

@pytest.fixture()
def tmp_svc(tmp_path, monkeypatch):
    from trainingplan import service
    monkeypatch.setattr(service, "DB_PATH", str(tmp_path / "tp.db"))
    return service


def test_policy_ingest_roundtrip(tmp_svc):
    svc = tmp_svc
    r = svc.ingest_policy_dir(BASE)
    assert r["count"] >= 3
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        plans = db.get_transfer_plans(policy_year="2026", major="大数据管理与应用", entry_year="2025级")
        assert {p["scope"]: p["quota"] for p in plans} == {"学院内": 3, "跨学院": 5}
        rules = db.get_transfer_rules(policy_year="2026")
        types = {x["rule_type"] for x in rules}
        assert {"志愿规则", "补修规则", "降级规则", "考核安排"} <= types
        assert db.latest_transfer_policy_year() == "2026"
        ms = db.get_major_selection_plans("2025级")
        assert {p["major"]: p["quota"] for p in ms}["大数据管理与应用"] == 32
        assert db.latest_major_selection_entry_year() == "2025级"
    finally:
        db.close()


def test_policy_ingest_idempotent(tmp_svc):
    svc = tmp_svc
    svc.ingest_policy_dir(BASE)
    svc.ingest_policy_dir(BASE)   # 重复导入不翻倍
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        assert len(db.get_transfer_plans(policy_year="2026")) == 24
        assert len(db.get_transfer_rules(policy_year="2026")) == 17   # 11 + 6(考核安排)
    finally:
        db.close()


def test_ingest_policy_path_registers_and_parses(tmp_svc):
    """单文件入库（文件中心入口）：登记 plan_source_doc + 解析规则，且幂等。"""
    svc = tmp_svc
    r = svc.ingest_policy_path(TRANSFER)
    assert r["status"] == "ok" and r["source_doc_id"]
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        assert any(d["id"] == r["source_doc_id"] for d in db.list_source_docs("政策"))
        plans = db.get_transfer_plans(policy_year="2026", major="大数据管理与应用",
                                      entry_year="2025级")
        assert {p["scope"]: p["quota"] for p in plans} == {"学院内": 3, "跨学院": 5}
        # text_content 已留存（供溯源）
        assert db.get_source_doc_text(r["source_doc_id"])
    finally:
        db.close()
    assert svc.ingest_policy_path(TRANSFER)["source_doc_id"] == r["source_doc_id"]
