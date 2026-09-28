"""trainingplan _render_summary 渲染测试（纯函数，合成 data 字典）。

覆盖四段渲染的分支：适用范围行、学分小计拼括号（None 组合）、
课程教学二级小计、学期地图 >6 门截断、先修三分支、硬条件缺省。
"""
from trainingplan.service import _credit_head, _render_summary


def _data(**over):
    d = {
        "major": "工商管理", "entry_year": "2023级",
        "top": {"total": "148+（8）", "course_teaching": 100.0,
                "practice": 40.0, "extra_practice": 8.0},
        "applies_to": None,
        "credit_tree": {"children": [{"name": "课程教学", "children": [
            {"name": "通识教育课程", "credit": 40}, {"name": "专业课程", "credit": 60}]}]},
        "course_count": 55,
        "semester_map": [
            {"semester": "第1学期", "credit": 24.0,
             "courses": [{"course_name": f"课程{i}"} for i in range(7)]},
            {"semester": "第2学期", "credit": 22.0,
             "courses": [{"course_name": "管理学"}, {"course_name": "经济学"}]},
        ],
        "prereq": {"verified": 2, "unverified": 1,
                   "chains": [["管理学", "运营管理", "供应链管理"]]},
        "graduation_requirements": [
            {"type": "毕业", "item": "总学分", "value": "148", "unit": "学分"},
        ],
    }
    d.update(over)
    return d


def test_render_full():
    out = _render_summary(_data())
    assert out == (
        "【工商管理 · 2023级 培养方案解读】\n"
        "一、学分结构：毕业总学分 148+8（课程教学 100 · 集中实践 40 · 课外实践 8）\n"
        "   通识教育课程 40\n"
        "   专业课程 60\n"
        "   课程总表 55 门\n"
        "二、四年课程地图：共 2 个学期\n"
        "   第1学期：课程0、课程1、课程2、课程3、课程4、课程5 等（24 学分）\n"
        "   第2学期：管理学、经济学（22 学分）\n"
        "三、先修关系（已校对）关键前导链：\n"
        "   管理学 → 运营管理 → 供应链管理\n"
        "   （另有 1 条待校对，暂不展示）\n"
        "四、毕业/授学位硬条件：\n"
        "   [毕业] 总学分：148学分\n"
        "完整图表请打开『功能』→『培养方案解读』。"
    )


def test_render_applies_line_variants():
    # 适用范围 == [year] → 不出适用于行；跨年级才出
    assert "适用于" not in _render_summary(_data(applies_to=["2023级"]))
    out = _render_summary(_data(applies_to=["2023级", "2024级"]))
    assert "（该方案适用于：2023级、2024级）" in out


def test_render_sparse_data():
    out = _render_summary(_data(
        top={},                      # 无小计：head 无括号，总学分为空串
        credit_tree={"children": []},   # 无课程教学节点：不出二级小计
        semester_map=[],             # 无学期地图：整段缺失
        prereq={"verified": 0, "unverified": 5, "chains": []},
        graduation_requirements=[],
    ))
    assert out == (
        "【工商管理 · 2023级 培养方案解读】\n"
        "一、学分结构：毕业总学分 \n"
        "   课程总表 55 门\n"
        "三、先修关系：已抽取 5 条待管理员校对，暂不展示。\n"
        "完整图表请打开『功能』→『培养方案解读』。"
    )


def test_render_prereq_no_verified_chains():
    out = _render_summary(_data(
        prereq={"verified": 1, "unverified": 0, "chains": []}))
    assert "三、先修关系：暂无已校对的前导链。" in out


def test_credit_head_none_combinations():
    # 仅课程教学小计 → 括号内只此一项
    assert _credit_head({"total": "160", "course_teaching": 120}) == \
        "一、学分结构：毕业总学分 160（课程教学 120）"
    # 课程教学 None、集中实践有小计 → 仍拼括号
    assert _credit_head({"total": "160", "course_teaching": None,
                         "practice": 30}) == \
        "一、学分结构：毕业总学分 160（集中实践 30）"
    # 全 None → 无括号
    assert _credit_head({"total": "160"}) == "一、学分结构：毕业总学分 160"
