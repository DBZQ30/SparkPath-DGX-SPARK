"""专业选择 / 转专业模拟单测 —— 设计 005 §6。"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_DIR = "data/SmartGuide"
PLAN = {
    "工商管理": f"{SAMPLE_DIR}/2023版工商管理专业培养方案.docx",
    "大数据管理与应用": f"{SAMPLE_DIR}/2023版大数据管理与应用专业培养方案.docx",
    "会计学（ACCA）": f"{SAMPLE_DIR}/2023版会计学（ACCA）专业培养方案.docx",
}
pytestmark = [pytest.mark.samples, pytest.mark.skipif(
    not os.path.isfile(PLAN["工商管理"]), reason="缺少培养方案样本")]


def _policy_available(*keys):
    """政策样本是否齐备（按文件名关键词判断；政策文件未提供时相关用例细粒度跳过，
    培养方案驱动的用例仍可跑）。"""
    try:
        names = os.listdir(SAMPLE_DIR)
    except OSError:
        return False
    return all(any(k in n for n in names) for k in keys)


def _seed(svc, major, year="2023级"):
    from trainingplan.parsers import (
        parse_credit_structure, parse_graduation_reqs, parse_plan_data,
        parse_mode_rules, parse_course_notes)
    from trainingplan.service import _build_mode_data
    from trainingplan.models import PlanDocument
    path = PLAN[major]
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        doc = PlanDocument(major=major, entry_year=year, file_name=os.path.basename(path),
                           file_path=path, uploader="t", applies_to=["全部"])
        pid = db.enqueue_document(doc)
        _meta, courses, sem, _bad = parse_plan_data(path)
        nodes, top = parse_credit_structure(path)
        reqs = parse_graduation_reqs(path, top)
        rules, mcourses, scopes = _build_mode_data(parse_mode_rules(path))
        db.replace_plan_data(pid, nodes, courses, sem, [], reqs,
                             course_notes=parse_course_notes(path))
        db.replace_mode_data(pid, rules, mcourses, scopes)
        db.finish_document(pid, "done", note="t", meta_extra={"top": top})
    finally:
        db.close()


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    from trainingplan import service
    monkeypatch.setattr(service, "DB_PATH", str(tmp_path / "tp.db"))
    for m in ("工商管理", "大数据管理与应用", "会计学（ACCA）"):
        _seed(service, m)
    # 政策（转专业 + 专业选择）
    service.ingest_policy_dir(SAMPLE_DIR)
    return service


@pytest.mark.skipif(not _policy_available("转专业"),
                    reason="缺少转专业政策样本（配额/降级规则断言依赖实施细则")
def test_simulate_transfer_plan_level(svc):
    from trainingplan import simulate
    r = simulate.simulate_transfer("工商管理", "大数据", "2024级")
    assert r["state"] == "done"
    assert r["to_major"] == "大数据管理与应用"
    assert r["source"] == "plan"
    assert r["quota"].get("学院内") == 1
    assert r["make_up_credits"] > 0
    p = r["pressure"]
    assert p["deadline_semesters"] == 2          # 2024级 → 大三，到大三末剩 2 学期
    assert "推免红线" in "".join(x["item"] for x in r["rules"])


def test_simulate_transfer_zero_quota(svc):
    from trainingplan import simulate
    r = simulate.simulate_transfer("大数据", "工商管理", "2023级")
    assert r["state"] == "done"
    assert r["eligible"] is False                # 大三工商计划为 0


@pytest.mark.skipif(not _policy_available("转专业"),
                    reason="缺少转专业政策样本（配额/降级规则断言依赖实施细则")
def test_simulate_transfer_acca_downgrade(svc):
    from trainingplan import simulate
    r = simulate.simulate_transfer("工商管理", "会计学（ACCA）", "2025级")
    assert r["downgrade"] is True
    assert any(x["rule_type"] == "降级规则" for x in r["rules"])


def test_simulate_transfer_with_student_grades(svc, tmp_path, monkeypatch):
    from trainingplan import identity, simulate
    wdb = str(tmp_path / "warning.db")
    conn = sqlite3.connect(wdb)
    conn.execute("CREATE TABLE grade (student_id TEXT, course_name TEXT, course_name_clean TEXT,"
                 " credit REAL, grade_raw TEXT, pass_flag INTEGER)")
    conn.executemany("INSERT INTO grade VALUES (?,?,?,?,?,?)", [
        ("9000000001", "高等数学I-1", "高等数学I-1", 6.5, "88", 1),
        ("9000000001", "数据结构与算法导论", "数据结构与算法导论", 3, "75", 1),
        ("9000000001", "机器学习-1", "机器学习-1", 2, "40", 0),
    ])
    conn.commit()
    conn.close()
    monkeypatch.setattr(identity, "WARNING_DB_PATH", wdb)

    r = simulate.simulate_transfer("工商管理", "大数据", "2024级", student_id="9000000001")
    assert r["state"] == "done"
    assert r["source"] == "student"
    names = [x["course_name"] for x in r["creditable"]]
    assert "数据结构与算法导论" in names
    assert "机器学习-1" in [x["course_name"] for x in r["failed"]]


@pytest.mark.skipif(not _policy_available("专业选择"),
                    reason="缺少专业选择政策样本（配额/成绩构成断言依赖实施方案")
def test_simulate_major_selection(svc):
    from trainingplan import simulate
    r = simulate.simulate_major_selection("2025级", choices=["工商管理", "工业工程", "大数据管理与应用"])
    assert r["state"] == "done"
    assert {p["major"]: p["quota"] for p in r["plans"]}["大数据管理与应用"] == 32
    assert "70%学业成绩" in r["score_formula"]
    assert len(r["strategy"]) == 3
    assert {s["tier"] for s in r["strategy"]} == {"保", "稳", "冲"}


def test_simulate_minor_unavailable(svc):
    from trainingplan import simulate
    r = simulate.simulate_minor("工商管理", "会计学（ACCA）", "2023级")
    assert r["state"] == "unavailable"


@pytest.mark.skipif(not _policy_available("转专业", "专业选择"),
                    reason="缺少转专业/专业选择政策样本")
def test_simulate_cli(svc, capsys):
    from trainingplan import cli
    cli.main(["simulate", "--from-major", "工商管理", "--to-major", "大数据",
              "--entry-year", "2024级"])
    out = capsys.readouterr().out
    assert "转专业模拟" in out and "需补修" in out
    cli.main(["select", "--entry-year", "2025级"])
    out = capsys.readouterr().out
    assert "专业选择" in out and "志愿策略" in out
