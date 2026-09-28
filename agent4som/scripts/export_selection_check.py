#!/usr/bin/env python3
"""导出选课检查名单详细报告（2026-08-31 学分结构 JSON 口径；v6 与 service
自动导出共用 academicwarning/export.py 的构建逻辑）。

用法：
    source venv/bin/activate
    python scripts/export_selection_check.py

输出三个 sheet（构建见 academicwarning/export.py）：
- 汇总：触发学生（学号/姓名/专业/触发类别/差额/缺修必修课/提醒内容）
- 学生类别明细：每学生每类别应修/已修/已选/差额 + 已修课程明细（课程+学分+成绩）+ 已选课程明细
- 全类别总览：触发学生的全部 8 类（差额>0 标红）

报告落盘：academicwarning/result/选课检查名单-{时间戳}.xlsx

数据源：data/warning.db（最新选课文件触发检查结果 selection_check 表 +
实时重算应修/已修/已选），口径见 docs/02-features/007 及学分结构 JSON。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from academicwarning.db import WarningDB
from academicwarning.service import _assemble_check_prep
from academicwarning.selection_check import check_selection_rationality
from academicwarning.export import build_report_xlsx


def main() -> None:
    db = WarningDB()
    sel_file = db.latest_source_file("selection")
    if sel_file is None:
        print("未上传选课结果文件，无法生成报告")
        return
    prep = _assemble_check_prep(db, sel_file)
    rows = check_selection_rationality(prep)
    print(f"触发 {len(rows)} 人")

    out = build_report_xlsx(prep, rows)
    print(f"已导出: {out}")
    print(f"学生 {len(rows)} 人 | sheets: 汇总 / 学生类别明细 / 全类别总览")
    db.close()


if __name__ == "__main__":
    main()
