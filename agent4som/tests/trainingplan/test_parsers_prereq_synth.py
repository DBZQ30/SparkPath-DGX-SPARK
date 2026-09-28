"""trainingplan extract_prereq_candidates 合成 docx 测试。

真实样本（data/SmartGuide）不随仓库分发，且该函数只读 zip 内的
`word/document.xml` 与 `word/_rels/document.xml.rels`，据此手工构造
最小 zip 覆盖：章节边界（中文序号标题收束）、章节外图片不收、
blip/VML imagedata 双通道、未登记 rId 跳过、rels 缺失幂等。
"""
import zipfile

from trainingplan.parsers import extract_prereq_candidates

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_V = "urn:schemas-microsoft-com:vml"


def _p(text: str) -> str:
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


def _blip(rid: str) -> str:
    return f'<w:p><w:r><w:drawing><a:blip r:embed="{rid}"/></w:drawing></w:r></w:p>'


def _imagedata(rid: str) -> str:
    return f'<w:p><w:r><w:object><v:imagedata r:id="{rid}"/></w:object></w:r></w:p>'


def _document(body: str) -> str:
    return (f'<w:document xmlns:w="{_W}" xmlns:r="{_R}" xmlns:a="{_A}" xmlns:v="{_V}">'
            f'<w:body>{body}</w:body></w:document>')


def _rels(items: list[tuple[str, str, str]]) -> str:
    rels = "".join(
        f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/'
        f'officeDocument/2006/relationships/{kind}" Target="{target}"/>'
        for rid, kind, target in items)
    return ('<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            f'2006/relationships">{rels}</Relationships>')


def _docx(tmp_path, entries: dict) -> str:
    p = tmp_path / "plan.docx"
    with zipfile.ZipFile(p, "w") as z:
        for name, content in entries.items():
            z.writestr(name, content)
    return str(p)


def test_extract_prereq_candidates_section_bounds(tmp_path):
    """章节内 blip+VML 按序收取；章节外/未登记 rId/非图片关系不收。"""
    body = (
        _p("七、课程结构") + _blip("rId9")              # 章节外（含图片关系）→ 不收
        + _p("八、专业课程先修关系图")                    # 章节起点
        + _blip("rId1")
        + _p("拓扑示意图说明（非中文序号行，不收束章节）")
        + _imagedata("rId2")                            # Visio/OLE 通道
        + _blip("rId404")                               # rels 未登记 → 跳过
        + _p("一、学分认定与转换")                        # 中文序号标题 → 章节收束
        + _blip("rId3")                                 # 章节后 → 不收
    )
    rels = _rels([
        ("rId1", "image", "media/prereq_a.png"),
        ("rId2", "image", "media/prereq_b.emf"),
        ("rId3", "image", "media/after.png"),
        ("rId9", "image", "media/before.png"),
        ("rId5", "hyperlink", "http://x"),              # 非图片关系 → 不入映射
    ])
    out = extract_prereq_candidates(
        _docx(tmp_path, {"word/document.xml": _document(body),
                         "word/_rels/document.xml.rels": rels}))
    assert out == [
        {"media": "word/media/prereq_a.png", "rId": "rId1"},
        {"media": "word/media/prereq_b.emf", "rId": "rId2"},
    ]


def test_extract_prereq_candidates_header_with_prereq_word_not_closing(tmp_path):
    """收束标题须含中文序号且不含"先修"；含"先修"的序号行不收束。"""
    body = (
        _p("第八章 专业课程先修关系图")
        + _blip("rId1")
        + _p("九、先修关系补充图")                        # 含"先修" → 不收束，继续收图
        + _blip("rId2")
        + _p("十、毕业设计")                              # 收束
        + _blip("rId3")
    )
    rels = _rels([
        ("rId1", "image", "media/a.png"),
        ("rId2", "image", "media/b.png"),
        ("rId3", "image", "media/c.png"),
    ])
    out = extract_prereq_candidates(
        _docx(tmp_path, {"word/document.xml": _document(body),
                         "word/_rels/document.xml.rels": rels}))
    assert [c["rId"] for c in out] == ["rId1", "rId2"]


def test_extract_prereq_candidates_no_section(tmp_path):
    """全文无先修关系图章节 → 空。"""
    body = _p("一、培养目标") + _blip("rId1")
    rels = _rels([("rId1", "image", "media/a.png")])
    assert extract_prereq_candidates(
        _docx(tmp_path, {"word/document.xml": _document(body),
                         "word/_rels/document.xml.rels": rels})) == []


def test_extract_prereq_candidates_missing_rels(tmp_path):
    """zip 无 rels 条目 → 空映射，无图可收（幂等不抛）。"""
    body = _p("八、专业课程先修关系图") + _blip("rId1")
    assert extract_prereq_candidates(
        _docx(tmp_path, {"word/document.xml": _document(body)})) == []
