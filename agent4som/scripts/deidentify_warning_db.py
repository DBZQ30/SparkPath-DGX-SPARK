#!/usr/bin/env python3
"""把评测夹具库 ``warning.db`` 里的学生个人信息替换为合成值（原地）。

为什么需要：Tier 3 的评测环境夹具必须放在 ``evals/environment/``（官方 hook 路径），
而该目录**会被 Tier-1 的 PII 扫描按设计跳过**，所以真实姓名/学号不会被任何自动化拦住。
脱敏后即使夹具被误打包出去，也不构成个人信息泄露。

替换规则（按 student_id 排序，稳定可复现）：
    student_id  9000000001 -> S0001
    name/student_name     -> 学生0001

只改个人信息列；``class_name`` / ``course_name`` / ``grade_master.name``（年级标签）保持不变。
评分只看人数与分派措辞，不看姓名，所以脱敏不影响评测结果。

用法：
    python3 deidentify_warning_db.py <warning.db> [--check]
    --check  只检查是否仍有疑似真实姓名，不做修改（退出码 1 表示仍有）
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys

# (表, 学号列, 姓名列)
TARGETS = [
    ("roster", "student_id", "name"),
    ("selection", "student_id", "name"),
    ("selection_check", "student_id", "name"),
    ("grade", "student_id", "student_name"),
    ("waiver", "student_id", None),
]

SYNTHETIC_NAME = re.compile(r"^学生\d{4}$")
SYNTHETIC_ID = re.compile(r"^S\d{4}$")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def collect_student_ids(conn: sqlite3.Connection) -> list[str]:
    ids: set[str] = set()
    for table, id_col, _ in TARGETS:
        if id_col not in _columns(conn, table):
            continue
        for (value,) in conn.execute(f"SELECT DISTINCT {id_col} FROM {table}"):
            if value:
                ids.add(str(value))
    return sorted(ids)


def check(conn: sqlite3.Connection) -> int:
    """Report how many rows still look like they hold real personal data."""
    remaining = 0
    for table, id_col, name_col in TARGETS:
        cols = _columns(conn, table)
        if name_col and name_col in cols:
            for (value,) in conn.execute(f"SELECT DISTINCT {name_col} FROM {table}"):
                if value and not SYNTHETIC_NAME.match(str(value)):
                    remaining += 1
        if id_col in cols:
            for (value,) in conn.execute(f"SELECT DISTINCT {id_col} FROM {table}"):
                if value and not SYNTHETIC_ID.match(str(value)):
                    remaining += 1
    print(f"  仍像真实个人信息的取值数：{remaining}")
    return 1 if remaining else 0


def deidentify(conn: sqlite3.Connection) -> None:
    students = collect_student_ids(conn)
    id_map = {sid: f"S{i:04d}" for i, sid in enumerate(students, start=1)}
    name_map = {sid: f"学生{i:04d}" for i, sid in enumerate(students, start=1)}
    print(f"  学生记录：{len(students)} 条，示例 {students[0]} -> {id_map[students[0]]} / {name_map[students[0]]}")

    total = 0
    # 阶段一：先改姓名（用**旧** student_id 匹配），否则改完学号就再也匹配不上
    for table, id_col, name_col in TARGETS:
        cols = _columns(conn, table)
        if not (name_col and name_col in cols and id_col in cols):
            continue
        for sid, new in name_map.items():
            total += conn.execute(
                f"UPDATE {table} SET {name_col}=? WHERE {id_col}=?", (new, sid)
            ).rowcount
    # 阶段二：再改学号
    for table, id_col, _ in TARGETS:
        if id_col not in _columns(conn, table):
            continue
        for sid, new in id_map.items():
            total += conn.execute(
                f"UPDATE {table} SET {id_col}=? WHERE {id_col}=?", (new, sid)
            ).rowcount
    conn.commit()
    print(f"  已更新 {total} 处")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db_path")
    ap.add_argument("--check", action="store_true", help="只检查，不修改")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db_path)
    try:
        if args.check:
            print(f"检查 {args.db_path}")
            return check(conn)
        print(f"脱敏 {args.db_path}")
        deidentify(conn)
        print("复检：")
        return check(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
