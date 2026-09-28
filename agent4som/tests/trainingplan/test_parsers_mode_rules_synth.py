"""培养模式 parse_mode_rules 合成 docx 测试。

真实样本（data/SmartGuide/*.docx）不随仓库分发（test_route/test_simulate/
test_trainingplan 的 parse_mode_rules 相关用例全 skip），据此用 python-docx
内存构造正文段落覆盖各分支：三模式段头、课程行两种格式、跨选专业行、
③段头直接命中与附件5兜底、学分上限+例外注、缺失项不臆造。
"""
from docx import Document

from trainingplan.parsers import parse_mode_rules


def _save_docx(tmp_path, paragraphs, name="modes.docx"):
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    path = tmp_path / name
    doc.save(path)
    return str(path)


def test_parse_mode_rules_synth_full(tmp_path):
    """三模式段 + 学分上限全要素：课程行/跨选专业行/③段头直接命中。"""
    path = _save_docx(tmp_path, [
        "①科学研究型培养模式：修读 6 学分的研究生进阶课程",
        "082038\t管理研究方法论I\t2学分",
        "082002 高级统计分析 2学分",
        "前沿交叉讲座（不计学分）",                       # 非课程行 → 忽略
        "②交叉融合型培养模式：修读 6 学分，跨选课程专业范围如下",
        "工商管理专业核心课和专业选修课",
        "法学专业的专业核心课和专业选修课",
        "（三）集中实践环节说明",                          # 跨选段终止行
        "③创新创业型培养模式：创新创业成果可替换集中实践学分 4",
        "学生每学期修读课程原则上不超过 25 学分，前一学期学分绩高于 90 的学生"
        "可适当超出 2 学分",
    ])
    out = parse_mode_rules(path)
    sci = out["modes"]["科学研究型"]
    assert sci["credit"] == 6.0
    assert sci["courses"] == [
        {"course_code": "082038", "course_name": "管理研究方法论I", "credit": 2.0},
        {"course_code": "082002", "course_name": "高级统计分析", "credit": 2.0},
    ]
    cross = out["modes"]["交叉融合型"]
    assert cross["credit"] == 6.0 and cross["majors"] == ["工商管理", "法学"]
    assert out["modes"]["创新创业型"]["credit"] == 4.0   # ③ 段头直接命中，非兜底
    assert out["credit_limit"] == {"value": 25.0,
                                   "note": "前一学期学分绩高于90可适当超出2学分"}
    assert out["apply_node"] == "大三第二学期"


def test_parse_mode_rules_synth_inno_fallback(tmp_path):
    """③段头缺失 → 附件5正文兜底（不少于 X 学分）；其余项缺失不臆造。"""
    path = _save_docx(tmp_path, [
        "附件5：创新创业成果可替换集中实践学分不少于 6 学分（除毕业设计和军训外）",
    ], name="fallback.docx")
    out = parse_mode_rules(path)
    assert "创新创业型" in out["modes"]
    assert out["modes"]["创新创业型"]["credit"] == 6.0
    assert "科学研究型" not in out["modes"] and "交叉融合型" not in out["modes"]
    assert out["credit_limit"] is None
    assert out["apply_node"] == "大三第二学期"   # 恒定值（实施细则）


def test_parse_mode_rules_synth_empty(tmp_path):
    """无任何模式段 → 三键骨架 + 空内容。"""
    out = parse_mode_rules(_save_docx(tmp_path, ["（本方案暂无培养模式说明）"],
                                      name="empty.docx"))
    assert out == {"modes": {}, "credit_limit": None, "apply_node": "大三第二学期"}
