"""trainingplan simulate 纯逻辑测试（不依赖培养方案样本）。

覆盖从 simulate_transfer / simulate_major_selection 抽出的 helper：
学期推算、方案级/学生级 diff 口径选择、接收计划汇总、压力评估、
志愿策略分层、年级解析。此前这些逻辑只有样本链路间接覆盖。
"""
from types import SimpleNamespace

from trainingplan import simulate


# ── 学期位置推算 ──────────────────────────────────────────────────


def test_current_semester_index():
    assert simulate.current_semester_index("2026级") == 1     # 大一上
    assert simulate.current_semester_index("2025级") == 3     # 大二上
    assert simulate.current_semester_index("2023级") == 7     # 大四上
    assert simulate.current_semester_index("2020级") == 8     # 触顶裁剪
    assert simulate.current_semester_index("乱写") == 1       # 无法解析 → 第 1 学期


def test_remaining_and_deadline_semesters():
    # 2026级：第 1 学期 → 剩 8，到大三末(3-2 序号 6)剩 6
    assert simulate._remaining_semesters("2026级") == 8
    assert simulate._deadline_semesters("2026级") == 6
    # 2023级：第 7 学期 → 剩 2，已过大三末
    assert simulate._remaining_semesters("2023级") == 2
    assert simulate._deadline_semesters("2023级") == 0


# ── diff 口径选择 ─────────────────────────────────────────────────


def test_plan_and_student_diff():
    src = [{"course_name": "管理学", "credit": 3, "course_type": "专业核心课程"},
           {"course_name": "微积分I", "credit": 5, "course_type": "公共课程"}]
    tgt = [{"course_name": "管理学", "credit": 3, "course_type": "专业大类基础课程"},
           {"course_name": "运营管理", "credit": 3, "course_type": "专业核心课程"},
           {"course_name": "体育", "credit": 0, "course_type": "公共课程"}]   # 0 学分跳过
    creditable, must = simulate._plan_diff(src, tgt)
    assert [c["course_name"] for c in creditable] == ["管理学"]
    assert creditable[0]["from"] == "管理学"
    assert [c["course_name"] for c in must] == ["运营管理"]

    grades = [{"course_name": "管理学", "credit": 3, "grade_raw": "88", "pass_flag": 1},
              {"course_name": "运营管理", "credit": 3, "grade_raw": "55", "pass_flag": 0}]
    creditable, must, failed = simulate._student_diff(grades, tgt)
    assert [c["course_name"] for c in creditable] == ["管理学"]
    assert [c["course_name"] for c in failed] == ["运营管理"]
    assert failed[0]["grade"] == "55"
    assert must == []                                          # 目标课都有已修记录


def test_diff_for_source_selection(monkeypatch):
    tgt = [{"course_name": "运营管理", "credit": 3, "course_type": "专业核心课程"}]
    src = []
    # 无学号 → 方案级
    source, _creditable, must, failed = simulate._diff_for("", src, tgt)
    assert source == "plan" and failed == [] and len(must) == 1
    # 有学号但读不到成绩 → 退方案级
    monkeypatch.setattr(simulate, "_student_grades", lambda sid: [])
    source, *_ = simulate._diff_for("2023001", src, tgt)
    assert source == "plan"
    # 有学号且读到成绩 → 学生级（含挂科桶）
    monkeypatch.setattr(simulate, "_student_grades",
                        lambda sid: [{"course_name": "运营管理", "credit": 3,
                                      "grade_raw": "55", "pass_flag": 0}])
    source, _creditable, must, failed = simulate._diff_for("2023001", src, tgt)
    assert source == "student" and len(failed) == 1 and must == []


def test_academic_score():
    grades = [
        {"credit": 3, "grade_raw": "85", "pass_flag": 1},
        {"credit": 2, "grade_raw": "补考 62", "pass_flag": 1},   # 补考及格按 60 计
        {"credit": 1, "grade_raw": "缺考", "pass_flag": 0},      # 无数字 → 不计
    ]
    r = simulate._academic_score(grades)
    # (85*3 + 60*2) / (3+2) = 75.0
    assert r["estimated"] == 75.0 and r["credits"] == 5
    assert simulate._academic_score([])["estimated"] is None


# ── 接收计划 / 压力评估 ───────────────────────────────────────────


def test_transfer_quota():
    db = SimpleNamespace(get_transfer_plans=lambda **kw: [
        {"scope": "学院内", "quota": 1}, {"scope": "跨院", "quota": 0}])
    quota, eligible = simulate._transfer_quota(db, "工商管理", "2023级", "2026")
    assert quota == {"学院内": 1, "跨院": 0} and eligible is True
    db0 = SimpleNamespace(get_transfer_plans=lambda **kw: [])
    quota, eligible = simulate._transfer_quota(db0, "工商管理", "2023级", "2026")
    assert quota == {} and eligible is False


def test_pressure_branches():
    db = SimpleNamespace(get_mode_rules=lambda doc_id, mode: [
        {"rule_type": "学期学分上限", "value": "25"}])
    # 新生：余量大 → 可完成
    p = simulate._pressure(db, 1, 20.0, "2026级")
    assert p["remaining_semesters"] == 8 and p["deadline_semesters"] == 6
    assert p["avg_load"] == 3.3 and p["credit_limit"] == 25.0
    assert p["deadline_risk"] == "第三学年末前可完成"
    # 已过大三：红线文案
    p = simulate._pressure(db, 1, 9.9, "2023级")
    assert p["deadline_risk"] == "已过大三（须按学院安排，可能影响推免）"
    # 平摊超上限：风险文案
    p = simulate._pressure(db, 1, 200.0, "2025级")
    assert "超上限 25" in p["deadline_risk"] and "4 学期" in p["deadline_risk"]


# ── 专业选择：年级解析 + 志愿策略 ────────────────────────────────


def test_resolve_selection_year():
    db = SimpleNamespace(latest_major_selection_entry_year=lambda: "2025级")
    assert simulate._resolve_selection_year(db, "") == ("2025级", None)
    assert simulate._resolve_selection_year(db, "2024级") == ("2024级", None)
    empty = SimpleNamespace(latest_major_selection_entry_year=lambda: "")
    year, err = simulate._resolve_selection_year(empty, "")
    assert year == "" and err["state"] == "unavailable"


def test_selection_strategy():
    plans = [{"major": "A", "quota": 1}, {"major": "B", "quota": 5},
             {"major": "C", "quota": 3}, {"major": "D", "quota": 9}]
    # 无 choices：按 quota 降序取前 3 → D/B/C
    strategy = simulate._selection_strategy(plans, None)
    assert [s["major"] for s in strategy] == ["D", "B", "C"]
    assert [s["tier"] for s in strategy] == ["保", "稳", "冲"]
    # 指定 choices：过滤到已收录专业后排序分层
    strategy = simulate._selection_strategy(plans, ["A", "C", "未收录专业"])
    assert [s["major"] for s in strategy] == ["C", "A"]
    assert [s["tier"] for s in strategy] == ["保", "稳"]


# ── 转专业：方案可用性判定 ────────────────────────────────────────


def test_find_parsed_doc(monkeypatch):
    docs = {"工商管理·2023级": SimpleNamespace(parsed_status="done"),
            "工商管理·2024级": SimpleNamespace(parsed_status="parsing"),
            "会计学（ACCA）·2023级": None}
    monkeypatch.setattr(simulate.service, "find_document",
                        lambda db, major, year: docs.get(f"{major}·{year}"))
    assert simulate._find_parsed_doc(None, "工商管理", "2023级") is not None
    assert simulate._find_parsed_doc(None, "工商管理", "2024级") is None   # 未完成解析
    assert simulate._find_parsed_doc(None, "会计学（ACCA）", "2023级") is None  # 未收录
