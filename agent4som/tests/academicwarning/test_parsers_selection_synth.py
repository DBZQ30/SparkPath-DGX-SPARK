"""选课结果 parse_selection 合成 xlsx 测试。

真实样本（academicwarning/docs/*.xlsx）不随仓库分发（test_parsers_selection.py
全 skip），据此用 openpyxl 内存构造覆盖各分支：学期标签首行锁定、多年级取最小
入学年、学号数值型去 ".0"（M4）、空行跳过、字段缺失列兜底、状态分布回显。
"""
import openpyxl

from academicwarning.parsers import parse_selection

# 真实样本列序（B1：普通模式全列扫描）
_HDR = ["学年学期", "学号", "姓名", "年级", "专业名称", "课程号",
        "课程名", "课程性质", "课程类别", "选课状态", "重修重考"]


def _save_xlsx(tmp_path, rows, name="sel.xlsx", header=_HDR):
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return str(path)


def test_parse_selection_synth_full(tmp_path):
    """全字段解析：学期标签仅取首行、多年级取最小入学年、学号去 .0、状态分布。"""
    path = _save_xlsx(tmp_path, [
        _HDR,
        # 学号写数值型 → 读回 float 223001.0 → "223001"（M4 口径）
        ["2026-2027学年 第一学期", 223001, "张三", "2023级", "工商管理",
         "REQ1", "必修高数A", "必修", "专业核心课程", "选中", ""],
        # 第二行学期不同：semester_label 已锁定，不再覆盖
        ["2099-2100学年 第一学期", "224002", "李四", "2024级", "会计学",
         "EL1", "选修课", "选修", "专业选修课程", "退课", "是"],
        ["2026-2027学年 第一学期", "223001", "张三", "2023级", "工商管理",
         "EL2", "另一门", "选修", "专业选修课程", "选中", ""],
    ])
    meta, rows, note = parse_selection(path)
    assert meta == {
        "semester_label": "2026-2027学年 第一学期",
        "semester_code": "4-1",          # 多年级 {2023级, 2024级} → 取最小 2023
        "grades": ["2023级", "2024级"],
        "entry_year": 2023,
    }
    assert len(rows) == 3
    r0 = rows[0]
    assert (r0["student_id"], r0["name"], r0["course_code"],
            r0["category"], r0["status"], r0["retake"]) == \
        ("223001", "张三", "REQ1", "专业核心课程", "选中", "")
    assert rows[1]["student_id"] == "224002"
    assert rows[1]["grade"] == "2024级" and rows[1]["major"] == "会计学"
    assert "选课状态分布" in note and "选中=2" in note and "退课=1" in note


def test_parse_selection_synth_skips_blank_and_missing_columns(tmp_path):
    """row[0] 空行跳过；表头缺列（idx 无键）与行尾缺列（越界）均兜底空串。"""
    # 表头缺 "重修重考" 列 + 行尾少一列
    hdr = _HDR[:-1]
    path = _save_xlsx(tmp_path, [
        hdr,
        [None] + [None] * 10,                                      # row[0] None → 跳过
        ["2025-2026学年 第二学期", "223001", "张三", "2023级",
         "工商管理", "REQ1", "必修高数A", "必修", "专业核心课程", "选中"],  # 尾列缺
    ], header=hdr)
    meta, rows, note = parse_selection(path)
    assert len(rows) == 1
    r = rows[0]
    assert r["retake"] == ""          # 表头无该列 → None → ""
    assert r["status"] == "选中"      # 越界前的列正常
    assert meta["semester_label"] == "2025-2026学年 第二学期"
    assert meta["semester_code"] == "3-2"
    assert "选课状态分布: 选中=1" in note


def test_parse_selection_synth_no_grade_rows(tmp_path):
    """全部行年级列空 → entry_year 0、semester_code 空、grades 含空串。"""
    path = _save_xlsx(tmp_path, [
        _HDR,
        ["2026-2027学年 第一学期", "", "张三", "", "", "REQ1", "课", "", "", "", ""],
    ])
    meta, rows, _note = parse_selection(path)
    assert meta["entry_year"] == 0 and meta["semester_code"] == ""
    assert meta["grades"] == [""]
    assert rows[0]["student_id"] == "" and rows[0]["name"] == "张三"
