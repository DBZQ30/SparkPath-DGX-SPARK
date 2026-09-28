#!/usr/bin/env python3
"""成绩单重解析生产替换工具（2026-09-02，单流解析模型落地）。

背景：data/warning.db 中 2023级 四份成绩单（source_file file_type='grade'，
grade='2023级'）的 grade 行仍是旧"全局学期模型"解析结果（term_label 错位，
如 2023 级生出现"第二学年（2023-2024）"），需用当前 parse_grade_table
（单流拼接模型）重解析替换。

- 学生学号/姓名**不重跑 OCR**：从现库 grade 行按表序（插入序）映射——
  现库每生一段连续行（学生组序 = 表格序），doc.tables[ti] ↔ 第 ti 名学生；
  len(学生) != len(tables)（或组不连续）时报告并跳过该文件，不替换。
- source_file 记录保持不变（不做新上传），仅替换该 source_file_id 的 grade 行。
- 默认**预览**（打印逐生 diff 与全量 SQL 预览，不执行）；--execute 才执行
  （每文件一个事务：DELETE + INSERT）。

用法：
  ./venv/bin/python scripts/reparse_grades.py                          # 预览（生产库）
  ./venv/bin/python scripts/reparse_grades.py --db /tmp/x.db           # 对副本库预览/执行
  ./venv/bin/python scripts/reparse_grades.py --execute                # 执行（生产库）
  ./venv/bin/python scripts/reparse_grades.py --preview-file /tmp/p.sql
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document

from academicwarning.db import WarningDB
from academicwarning.parsers import parse_grade_table

REPO_ROOT = Path(__file__).resolve().parent.parent

_INS_COLS = ("student_id", "student_name", "term_label", "course_name",
             "course_name_clean", "credit", "grade_raw", "pass_flag",
             "marker", "source_file_id")


def _q(v) -> str:
    """SQL 字面量转义（仅用于预览输出，执行走参数绑定）。"""
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, (int, bool)):
        return str(int(v))
    return "'" + str(v).replace("'", "''") + "'"


def resolve_docx_path(sf_path: str) -> Path:
    p = Path(sf_path)
    if p.is_absolute() and p.exists():
        return p
    cands = [Path.cwd() / p, REPO_ROOT / p]
    for c in cands:
        if c.exists():
            return c
    raise FileNotFoundError(f"成绩单文件不存在: {sf_path}（cwd={Path.cwd()}）")


def load_students(db: WarningDB, file_id: int) -> tuple[list[tuple[str, str]], int, int]:
    """按插入序（=表序）取学生列表 [(sid, name), ...]；返回 (列表, 连续段数, 总行数)。

    每生一组连续行（db 插入序 = 表序）时 段数 == 生数；否则映射不可靠，
    由调用方跳过该文件。"""
    rows = db.get_grades(file_id)
    order: list[tuple[str, str]] = []
    seen: set[str] = set()
    prev = None
    segments = 0
    for g in rows:
        if g.student_id != prev:
            segments += 1
            prev = g.student_id
            if g.student_id not in seen:
                seen.add(g.student_id)
                order.append((g.student_id, g.student_name))
    return order, segments, len(rows)


def student_diff(old: list, new: list) -> dict:
    """逐生 diff 摘要。change=旧新同课不同 term 的行数；recovered/lost 为课名列表。"""
    from collections import Counter, deque
    queues: dict[str, deque] = {}
    for g in old:
        queues.setdefault(g.course_name_clean, deque()).append(g)
    changed = 0
    recovered: list[str] = []
    for g in new:
        q = queues.get(g.course_name_clean)
        if q:
            old_g = q.popleft()
            if old_g.term_label != g.term_label:
                changed += 1
        else:
            recovered.append(g.course_name_clean)
    old_cnt = Counter(g.course_name_clean for g in old)
    new_cnt = Counter(g.course_name_clean for g in new)
    lost = []
    for name, c in old_cnt.items():
        extra = c - new_cnt.get(name, 0)
        lost += [name] * extra
    return {"old_n": len(old), "new_n": len(new),
            "changed": changed, "recovered": recovered, "lost": lost}


def _map_tables_to_students(db: WarningDB, sf, out):
    """表序 ↔ 学生序 映射前置校验。返回 (orders, tables, old_total)，
    不可靠时打印原因返回 None。"""
    orders, segments, old_total = load_students(db, sf.id)
    path = resolve_docx_path(sf.file_path)
    major = (sf.in_file_meta or {}).get("major", "")
    print(f"\n== source_file #{sf.id} {major}（{sf.file_name}）", file=out)
    if segments != len(orders):
        print(f"  跳过：现库学生组不连续（组段 {segments} ≠ 学生 {len(orders)}），"
              f"表序映射不可靠", file=out)
        return None
    tables = Document(str(path)).tables
    if len(orders) != len(tables):
        print(f"  跳过：学生数 {len(orders)} ≠ 成绩单表数 {len(tables)}，不替换", file=out)
        return None
    print(f"  表数 {len(tables)} = 学生数 {len(orders)}，按表序映射学生 ✓", file=out)
    return orders, tables, old_total


def _group_old_by_student(db: WarningDB, sf, orders) -> list[list]:
    """旧行按学生分组（连续段按序切）；段数超学生列表 → 映射失败异常。"""
    rows = db.get_grades(sf.id)
    old_by_student: list[list] = [[] for _ in orders]
    cur = -1
    for g in rows:
        if cur < 0 or orders[cur][0] != g.student_id:
            cur += 1
            if cur >= len(orders):
                raise RuntimeError("现库学生段数超学生列表（映射失败）")
        old_by_student[cur].append(g)
    assert cur == len(orders) - 1
    return old_by_student


def _reparse_tables(tables, orders) -> tuple[list[list], int, int]:
    """逐表用当前解析器重跑。返回 (新行分组, 孤儿课数, 节序回退数)。"""
    new_by_student: list[list] = []
    n_orphan = 0
    n_seq_anom = 0
    for ti, table in enumerate(tables):
        sid, name = orders[ti]
        orphan_sink: list = []
        seq_sink: list = []
        grades = parse_grade_table(table, sid, name,
                                   orphan_sink=orphan_sink, seq_sink=seq_sink)
        n_orphan += len(orphan_sink)
        n_seq_anom += len(seq_sink)
        new_by_student.append(grades)
    return new_by_student, n_orphan, n_seq_anom


def _student_diff_lines(d: dict) -> str:
    """单生 diff 报告行（Δ/term变化/找回/丢失 段按需拼接）。"""
    rec = "、".join(d["recovered"][:8]) + ("…" if len(d["recovered"]) > 8 else "")
    lost = "、".join(d["lost"][:8]) + ("…" if len(d["lost"]) > 8 else "")
    return (f"  生 {d['sid']} {d['name'] or '?'}: 旧{d['old_n']}课 → 新{d['new_n']}课"
            + (f"（Δ{d['new_n'] - d['old_n']:+d}）" if d["new_n"] != d["old_n"] else "")
            + (f" | term变化 {d['changed']} 行" if d["changed"] else "")
            + (f" | 找回 {len(d['recovered'])}[{rec}]" if d["recovered"] else "")
            + (f" | 丢失 {len(d['lost'])}[{lost}]" if d["lost"] else ""))


def _diff_report(orders, old_by_student, new_by_student, old_total,
                 n_orphan, n_seq_anom, out) -> dict:
    """逐生 diff 打印 + 汇总 dict 累计。"""
    agg = {"old_total": old_total, "new_total": 0, "changed": 0,
           "recovered": 0, "lost": 0, "term_empty_old": 0, "term_empty_new": 0,
           "orphans": n_orphan, "seq_anomalies": n_seq_anom}
    for si, (sid, name) in enumerate(orders):
        d = student_diff(old_by_student[si], new_by_student[si])
        d["sid"], d["name"] = sid, name
        agg["new_total"] += d["new_n"]
        agg["changed"] += d["changed"]
        agg["recovered"] += len(d["recovered"])
        agg["lost"] += len(d["lost"])
        agg["term_empty_old"] += sum(1 for g in old_by_student[si] if not g.term_label)
        agg["term_empty_new"] += sum(1 for g in new_by_student[si] if not g.term_label)
        print(_student_diff_lines(d), file=out)

    print(f"  汇总: 旧 {agg['old_total']} 行 → 新 {agg['new_total']} 行"
          f"（Δ{agg['new_total'] - agg['old_total']:+d}）", file=out)
    print(f"  汇总: term 非空 旧 {old_total - agg['term_empty_old']}/{old_total}"
          f" → 新 {agg['new_total'] - agg['term_empty_new']}/{agg['new_total']}", file=out)
    print(f"  汇总: term 变化 {agg['changed']} 行 | 找回 {agg['recovered']} | "
          f"丢失 {agg['lost']} | 孤儿课 {n_orphan} | 节序回退 {n_seq_anom}", file=out)
    return agg


def _build_sql_lines(sf, new_by_student) -> tuple[list[str], int]:
    """生成替换 SQL 预览（DELETE + INSERT，source_file 记录不动）。"""
    lines = [f"-- source_file #{sf.id} {sf.in_file_meta.get('major', '') if sf.in_file_meta else ''} {sf.file_name}",
             f"DELETE FROM grade WHERE source_file_id = {sf.id};"]
    row_cnt = 0
    for grades in new_by_student:
        for g in grades:
            vals = [_q(g.student_id), _q(g.student_name), _q(g.term_label),
                    _q(g.course_name), _q(g.course_name_clean), _q(g.credit),
                    _q(g.grade_raw), _q(g.pass_flag), _q(g.marker), _q(sf.id)]
            lines.append("INSERT INTO grade (" + ", ".join(_INS_COLS) + ") VALUES ("
                         + ", ".join(vals) + ");")
            row_cnt += 1
    return lines, row_cnt


def _execute_replace(db: WarningDB, sf, new_by_student) -> int:
    """执行替换：DELETE + INSERT 单事务提交。返回删除行数。"""
    cur = db.conn.execute("DELETE FROM grade WHERE source_file_id = ?", (sf.id,))
    deleted = cur.rowcount
    db.conn.executemany(
        "INSERT INTO grade (student_id, student_name, term_label, course_name,"
        " course_name_clean, credit, grade_raw, pass_flag, marker, source_file_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(g.student_id, g.student_name, g.term_label, g.course_name,
          g.course_name_clean, g.credit, g.grade_raw, g.pass_flag, g.marker, sf.id)
         for grades in new_by_student for g in grades])
    db.conn.commit()  # 每文件一个事务
    return deleted


def process_file(db: WarningDB, sf, execute: bool, preview_file,
                 out) -> tuple[bool, dict]:
    """处理单份成绩单文件。返回 (是否替换成功, 摘要 dict)。"""
    mapped = _map_tables_to_students(db, sf, out)
    if mapped is None:
        return False, {}
    orders, tables, old_total = mapped

    old_by_student = _group_old_by_student(db, sf, orders)
    new_by_student, n_orphan, n_seq_anom = _reparse_tables(tables, orders)
    agg = _diff_report(orders, old_by_student, new_by_student, old_total,
                       n_orphan, n_seq_anom, out)

    lines, row_cnt = _build_sql_lines(sf, new_by_student)
    if preview_file:
        preview_file.write("\n".join(lines) + "\n")
        print(f"  [SQL预览] 已写入 {preview_file.name}（DELETE + INSERT×{row_cnt}，未执行）",
              file=out)

    if execute:
        deleted = _execute_replace(db, sf, new_by_student)
        print(f"  [执行] 已替换：DELETE {deleted} 行，INSERT {row_cnt} 行"
              f"（单事务提交）", file=out)
        agg["deleted"] = deleted
    agg["insert"] = row_cnt
    return True, agg


def main() -> int:
    ap = argparse.ArgumentParser(description="成绩单重解析替换（预览/执行）")
    ap.add_argument("--db", default="data/warning.db", help="目标库路径")
    ap.add_argument("--grade", default="2023级", help="年级（source_file.grade）")
    ap.add_argument("--execute", action="store_true", help="执行替换（默认仅预览）")
    ap.add_argument("--preview-file", default="", help="SQL 预览输出文件路径（可选）")
    args = ap.parse_args()

    db = WarningDB(args.db)
    sfs = db.latest_source_files_by_major("grade", args.grade)
    if not sfs:
        print(f"未找到 {args.grade} 成绩单文件（file_type=grade）")
        return 1
    pf = open(args.preview_file, "w", encoding="utf-8") if args.preview_file else None
    try:
        ok_files = 0
        for major, sf in sfs.items():
            print(f"----- {major}（#{sf.id}）-----")
            ok, _agg = process_file(db, sf, args.execute, pf, sys.stdout)
            ok_files += 1 if ok else 0
        print(f"\n===== {'执行' if args.execute else '预览'}完成：{ok_files}/{len(sfs)} 份文件"
              f"{'已替换' if args.execute else '可替换'} =====")
    finally:
        if pf:
            pf.close()
        db.conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
