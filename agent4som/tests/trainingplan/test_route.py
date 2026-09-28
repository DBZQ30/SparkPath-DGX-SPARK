"""路线图引擎单测 —— 设计 005 §5/§7。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_DIR = "data/SmartGuide"
SAMPLE = {
    "工商管理": f"{SAMPLE_DIR}/2023版工商管理专业培养方案.docx",
    "工业工程": f"{SAMPLE_DIR}/2023版工业工程专业培养方案.docx",
    "会计学（ACCA）": f"{SAMPLE_DIR}/2023版会计学（ACCA）专业培养方案.docx",
    "大数据管理与应用": f"{SAMPLE_DIR}/2023版大数据管理与应用专业培养方案.docx",
}
pytestmark = [pytest.mark.samples, pytest.mark.skipif(
    not os.path.isfile(SAMPLE["工商管理"]), reason="缺少培养方案样本")]


def _seed(svc, major="工商管理", year="2023级"):
    from trainingplan.parsers import (
        parse_credit_structure, parse_graduation_reqs, parse_plan_data,
        parse_mode_rules, parse_course_notes)
    from trainingplan.service import _build_mode_data
    from trainingplan.models import PlanDocument
    path = SAMPLE[major]
    db = svc.TrainingPlanDB(svc.DB_PATH)
    try:
        doc = PlanDocument(major=major, entry_year=year, file_name=os.path.basename(path),
                           file_path=path, uploader="t")
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
    _seed(service)
    return service


def test_build_route_regular(svc):
    from trainingplan import route
    r = route.build_route("工商管理", "2023", "常规型")
    assert r["state"] == "done"
    assert len(r["semesters"]) == 8
    assert r["semesters"][0]["semester"] == "1-1"
    assert r["credit_limit"] == 25
    assert all(s["credits"] > 0 for s in r["semesters"])
    assert any(a["source"] == "官方" for a in r["actions"])
    assert "可参考方案" in r["summary_text"]
    # 课程名清洗（体育合并单元格）
    names = [c["course_name"] for s in r["semesters"] for c in s["courses"]]
    assert any("/" in n for n in names)


def test_build_route_science_overlay(svc):
    from trainingplan import route
    r = route.build_route("工商管理", "2023", "科学研究型")
    assert r["state"] == "done"
    names = [c["course_name"] for c in r["mode_overlay"]["courses"]]
    assert "管理研究方法论I" in names
    assert r["apply_node"] == "大三第二学期"


def test_build_route_cross_scopes(svc):
    from trainingplan import route
    r = route.build_route("工商管理", "2023", "交叉融合型")
    assert "工业工程" in r["mode_overlay"]["scopes"]


def test_build_route_cross_overlay_rules_exclude_scope(svc):
    """跨选范围只由 scopes 下发（.ui-note 换行展示），不再混进规则行导致溢出。"""
    from trainingplan import route
    r = route.build_route("工商管理", "2023", "交叉融合型")
    assert r["state"] == "done"
    assert all(x["rule_type"] not in ("说明", "跨选范围") for x in r["mode_overlay"]["rules"])
    assert "工业工程" in r["mode_overlay"]["scopes"]


def test_build_route_pressure_levels(svc):
    from trainingplan import route
    r = route.build_route("工商管理", "2023", "常规型")
    for s in r["semesters"]:
        assert s["load_level"] in ("轻松", "适中", "偏紧", "超限")
        assert s["load_ratio"] == pytest.approx(s["credits"] / 25, abs=0.01)


def test_build_route_bad_mode(svc):
    from trainingplan import route
    r = route.build_route("工商管理", "2023", "不存在的型")
    assert r["state"] == "bad_mode"


def test_build_route_need_year(svc):
    from trainingplan import route
    r = route.build_route("工商管理", "", "常规型")
    assert r["state"] == "need_year"
    assert "2023级" in r["message"]


def test_compare_modes(svc):
    from trainingplan import route
    r = route.compare_modes("工商管理", "2023")
    assert r["state"] == "done"
    assert len(r["modes"]) == 4
    modes = {m["mode"] for m in r["modes"]}
    assert modes == {"常规型", "科学研究型", "交叉融合型", "创新创业型"}


def test_route_cli(svc, capsys):
    from trainingplan import cli
    cli.main(["route", "--major", "工商管理", "--entry-year", "2023", "--mode", "科学研究型"])
    out = capsys.readouterr().out
    assert "四年路线图" in out and "科学研究型" in out and "大三第二学期" in out
    cli.main(["compare", "--major", "工商管理", "--entry-year", "2023"])
    out = capsys.readouterr().out
    assert "四方向对比" in out


def test_plan_route_api(svc, monkeypatch):
    from fastapi.testclient import TestClient
    import trainingplan.api as api_mod
    monkeypatch.setattr(api_mod, "_API_KEY", "testkey")
    with TestClient(api_mod.app) as client:
        h = {"X-API-Key": "testkey"}
        r = client.get("/api/plan/route?major=工商管理&entry_year=2023&mode=科学研究型", headers=h)
        assert r.status_code == 200 and r.json()["state"] == "done"
        assert "semesters" in r.json() and len(r.json()["semesters"]) == 8
        r = client.get("/api/plan/route/compare?major=工商管理&entry_year=2023", headers=h)
        assert len(r.json()["modes"]) == 4
        r = client.get("/api/plan/route/options?major=工商管理&entry_year=2023", headers=h)
        assert r.json()["state"] == "done" and "rules" in r.json()
