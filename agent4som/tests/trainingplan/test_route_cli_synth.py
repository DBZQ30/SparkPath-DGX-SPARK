"""trainingplan route / cli 纯逻辑测试（不依赖培养方案样本）。

覆盖从 build_route / cmd_modes 抽出的 helper：学期分桶与排序、
逐学期行组装（负荷比/等级/课程字段合并）、模式 overlay 组装
（规则剔除展示项、课程按学期排序）、培养模式文本渲染。
此前这些逻辑只有样本链路间接覆盖。
"""
from types import SimpleNamespace

from trainingplan import route
from trainingplan.cli import _print_mode_block

# ── route._semester_buckets / _render_semesters ───────────────────


def test_semester_buckets_filters_invalid():
    scs = [
        {"semester": "1-1", "course_code": "A", "credit": 3},
        {"semester": " 2-1 ", "course_code": "B", "credit": 2},   # strip 后合法
        {"semester": "大三上", "course_code": "C", "credit": 4},   # 非规范 → 跳过
        {"semester": None, "course_code": "D", "credit": 1},       # 空 → 跳过
        {"semester": "1-1", "course_code": "E", "credit": 5},      # 同学期追加
    ]
    buckets = route._semester_buckets(scs)
    assert set(buckets) == {"1-1", "2-1"}
    assert [c["course_code"] for c in buckets["1-1"]] == ["A", "E"]


def test_render_semesters_sort_merge_load():
    buckets = {
        "2-1": [{"semester": "2-1", "course_code": "B", "credit": 25.0, "course_name": "运营管理"}],
        "1-1": [{"semester": "1-1", "course_code": "A", "credit": 10.0, "course_name": "管理学"},
                {"semester": "1-1", "course_code": "X", "credit": 5.0, "course_name": "未知课"}],
    }
    courses = {
        # course_type / note / required_flag 从课程总表合并；粘连名按展示层拆分
        "A": {"course_type": "专业核心课程", "required_flag": "必修", "note": "研究生进阶"},
    }
    out = route._render_semesters(buckets, courses, 25.0)
    # 学期序排序：1-1 在前
    assert [s["semester"] for s in out] == ["1-1", "2-1"]
    first = out[0]
    assert first["credits"] == 15.0 and first["load_ratio"] == 0.6
    assert first["load_level"] == "适中"          # 0.60 → 适中（<0.80）
    a = first["courses"][0]
    assert a["course_type"] == "专业核心课程" and a["note"] == "研究生进阶"
    # 课程总表没有的编码 → 字段空串兜底，不抛
    x = first["courses"][1]
    assert x["course_type"] == "" and x["note"] == ""
    # 超限：25/25 = 1.0 → 偏紧
    assert out[1]["load_ratio"] == 1.0 and out[1]["load_level"] == "偏紧"
    # limit=0 → ratio 0 不除零
    out0 = route._render_semesters(buckets, courses, 0)
    assert all(s["load_ratio"] == 0 for s in out0)


def test_render_semesters_sticky_name_display():
    # 推荐课表粘连名 "体育-1体育-3" → "体育-1/体育-3"（展示层拆分）
    buckets = {"1-2": [{"semester": "1-2", "course_code": "P", "credit": 1,
                        "course_name": "体育-1体育-3"}]}
    out = route._render_semesters(buckets, {}, 25.0)
    assert out[0]["courses"][0]["course_name"] == "体育-1/体育-3"


# ── route._mode_overlay ───────────────────────────────────────────


def test_mode_overlay_assembly_and_sort():
    db = SimpleNamespace(
        get_mode_rules=lambda doc_id, mode: [
            {"rule_type": "学分替代", "item": "替代学分", "value": "6"},
            {"rule_type": "说明", "item": "纯展示", "value": "x"},       # 剔除
            {"rule_type": "跨选范围", "item": "范围", "value": "y"},     # 剔除（由 scopes 下发）
        ],
        get_mode_courses=lambda doc_id, mode: [
            {"course_code": "R2", "course_name": "科研课乙", "credit": 3.0},
            {"course_code": "R1", "course_name": "科研课甲", "credit": 2.0},
        ],
        get_mode_scopes=lambda doc_id, mode: [
            {"allowed_major": "工业工程"}, {"allowed_major": "大数据管理与应用"}],
    )
    courses = {"R2": {"semester": "2-2"}, "R1": {"semester": "2-1"}, "X": {}}
    ov = route._mode_overlay(db, 9, "科学研究型", courses)
    assert ov["mode"] == "科学研究型"
    assert [r["rule_type"] for r in ov["rules"]] == ["学分替代"]
    assert ov["scopes"] == ["工业工程", "大数据管理与应用"]
    # 模式课程按学期排序（R1 2-1 在 R2 2-2 前），学分保留
    assert [c["course_code"] for c in ov["courses"]] == ["R1", "R2"]
    assert ov["courses"][0]["semester"] == "2-1"


# ── cli._print_mode_block ─────────────────────────────────────────


def test_print_mode_block_sci_mode(capsys):
    rules = [
        {"mode": "科学研究型", "rule_type": "学分替代", "item": "替代学分",
         "value": "6", "unit": "学分", "detail": "研究生课程"},
        {"mode": "科学研究型", "rule_type": "跨选范围", "item": "范围",
         "value": "自动化", "unit": "", "detail": ""},
        {"mode": "其他", "rule_type": "学分替代", "item": "x", "value": "1",
         "unit": "", "detail": ""},                                  # 其他模式 → 不出
    ]
    courses = [{"mode": "科学研究型", "course_name": "科研课甲", "credit": 2.0},
               {"mode": "其他", "course_name": "乙", "credit": 1.0}]
    _print_mode_block("科学研究型", rules, courses)
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "· 科学研究型：替代学分 6学分（研究生课程）"
    assert out[1] == "    可选专业：自动化"
    assert out[2] == "    课程清单：科研课甲(2)"


def test_print_mode_block_no_rules(capsys):
    _print_mode_block("交叉融合型", [], [{"mode": "交叉融合型", "course_name": "x",
                                      "credit": 1.0}])
    assert capsys.readouterr().out == ""
