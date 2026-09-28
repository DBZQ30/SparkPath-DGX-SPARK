#!/usr/bin/env python3
"""一次性迁移：年级分区（v6）。

1) init_schema 幂等补列（source_file/training_plan/selection/selection_check + grade）
2) 现有数据全部回填 '2023级'（当前唯一批次：选课文件"2023级26-27学年…"）
3) grade_master 预置 2023/2024/2025 级
用法：source venv/bin/activate && python scripts/migrate_grade_partition.py
数据库路径可用环境变量 WARNING_DB 覆盖（默认 data/warning.db）。
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from academicwarning.db import WarningDB

GRADES = ["2023级", "2024级", "2025级", "2026级"]

def main() -> None:
    db = WarningDB(os.environ.get("WARNING_DB", "data/warning.db"))
    try:
        db.init_schema()
        for t in ("source_file", "training_plan", "selection", "selection_check"):
            db.conn.execute(f"UPDATE {t} SET grade='2023级' WHERE grade=''")
        for g in GRADES:
            db.add_grade(g)
        db.conn.commit()
        print("迁移完成：grade 列回填 2023级，grade_master 预置", GRADES)
        print("当前选课文件:", db.latest_source_file("selection", "2023级").file_name)
    finally:
        db.close()

if __name__ == "__main__":
    main()
