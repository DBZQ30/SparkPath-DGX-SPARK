"""培养方案存储层 —— 方案数据 DAO（学分树/课程/推荐课表/先修/毕业要求 + 培养模式）。

`trainingplan/db.py` 门面按 mixin 组装；课程/推荐课表模型复用
academicwarning（同源解析口径）。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from academicwarning.models import PlanCourse, PlanSemesterCourse

from .db_core import _now
from .models import (
    CreditNode,
    GraduationReq,
    ModeCourse,
    ModeRule,
    ModeScope,
    PrereqEdge,
)


class PlanDataDaoMixin:
    """plan_credit_node / plan_course / plan_semester_course / plan_prereq_edge /
    plan_graduation_req / plan_mode_* 写入与读取（均幂等：先删该 plan_id 旧数据）。"""

    conn: sqlite3.Connection

    def replace_plan_data(
        self,
        plan_id: int,
        credit_nodes: list[CreditNode],
        courses: list[PlanCourse],
        sem_courses: list[PlanSemesterCourse],
        prereq_edges: list[PrereqEdge],
        grad_reqs: list[GraduationReq],
        course_notes: dict[str, str] | None = None,
    ) -> None:
        """写入（幂等：先删该 plan_id 旧数据）。

        `course_notes`：课程编码 → 「课程信息备注」（研究生进阶/前沿交叉/创新创业…），
        由 `parsers.parse_course_notes` 抽取后传入。
        """
        notes = course_notes or {}
        for table in ("plan_credit_node", "plan_course", "plan_semester_course",
                      "plan_prereq_edge", "plan_graduation_req"):
            self.conn.execute(f"DELETE FROM {table} WHERE plan_id=?", (plan_id,))

        self.conn.executemany(
            "INSERT INTO plan_credit_node (plan_id,parent_path,path,name,credit,"
            "credit_note,category,sort) VALUES (?,?,?,?,?,?,?,?)",
            [(plan_id, n.parent_path, n.path, n.name, n.credit, n.credit_note,
              n.category, n.sort) for n in credit_nodes],
        )
        self.conn.executemany(
            "INSERT INTO plan_course (plan_id,course_code,course_name,en_name,credit,"
            "required_flag,course_type,semester,provider,note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(plan_id, c.course_code, c.course_name, getattr(c, "en_name", ""), c.credit,
              c.required_flag, c.course_type, c.semester, getattr(c, "provider", ""),
              notes.get(c.course_code, ""))
             for c in courses],
        )
        self.conn.executemany(
            "INSERT INTO plan_semester_course (plan_id,semester,course_code,course_name,credit)"
            " VALUES (?,?,?,?,?)",
            [(plan_id, s.semester, s.course_code, s.course_name, s.credit)
             for s in sem_courses],
        )
        self.conn.executemany(
            "INSERT INTO plan_prereq_edge (plan_id,from_course_code,from_course_name,"
            "to_course_code,to_course_name,source,confidence,verified,verified_by,"
            "verified_at,note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(plan_id, e.from_course_code, e.from_course_name, e.to_course_code,
              e.to_course_name, e.source, e.confidence, e.verified, e.verified_by,
              e.verified_at, e.note) for e in prereq_edges],
        )
        self.conn.executemany(
            "INSERT INTO plan_graduation_req (plan_id,req_type,item,value,unit,detail,sort)"
            " VALUES (?,?,?,?,?,?,?)",
            [(plan_id, g.req_type, g.item, g.value, g.unit, g.detail, g.sort)
             for g in grad_reqs],
        )
        self.conn.commit()

    def get_credit_nodes(self, plan_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM plan_credit_node WHERE plan_id=? ORDER BY sort, id", (plan_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_courses(self, plan_id: int, semester: str = "", category: str = "") -> list[dict[str, Any]]:
        conds, params = ["plan_id=?"], [plan_id]
        if semester:
            conds.append("semester=?"); params.append(semester)
        if category:
            conds.append("course_type=?"); params.append(category)
        rows = self.conn.execute(
            f"SELECT * FROM plan_course WHERE {' AND '.join(conds)} ORDER BY id", params
        ).fetchall()
        return [dict(r) for r in rows]

    def get_semester_courses(self, plan_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM plan_semester_course WHERE plan_id=? ORDER BY semester, id", (plan_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_prereq_edges(self, plan_id: int, verified_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM plan_prereq_edge WHERE plan_id=?"
        if verified_only:
            sql += " AND verified=1"
        sql += " ORDER BY id"
        rows = self.conn.execute(sql, (plan_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_graduation_reqs(self, plan_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM plan_graduation_req WHERE plan_id=? ORDER BY sort, id", (plan_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ────────────────────── 培养模式（四路径规则，设计 005 §4） ──────────────────────

    def replace_mode_data(self, plan_id: int, mode_rules: list[ModeRule],
                          mode_courses: list[ModeCourse],
                          mode_scopes: list[ModeScope]) -> None:
        """写入培养模式规则/课程清单/跨选范围（幂等：先删该 plan_id 旧数据）。"""
        for table in ("plan_mode_rule", "plan_mode_course", "plan_mode_scope"):
            self.conn.execute(f"DELETE FROM {table} WHERE plan_id=?", (plan_id,))
        self.conn.executemany(
            "INSERT INTO plan_mode_rule (plan_id,mode,rule_type,item,value,unit,detail,sort)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(plan_id, r.mode, r.rule_type, r.item, r.value, r.unit, r.detail, r.sort)
             for r in mode_rules],
        )
        self.conn.executemany(
            'INSERT INTO plan_mode_course (plan_id,mode,"group",course_code,course_name,'
            "credit,provider,note) VALUES (?,?,?,?,?,?,?,?)",
            [(plan_id, c.mode, c.group, c.course_code, c.course_name, c.credit,
              c.provider, c.note) for c in mode_courses],
        )
        self.conn.executemany(
            "INSERT INTO plan_mode_scope (plan_id,mode,allowed_major,note) VALUES (?,?,?,?)",
            [(plan_id, s.mode, s.allowed_major, s.note) for s in mode_scopes],
        )
        self.conn.commit()

    def get_mode_rules(self, plan_id: int, mode: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM plan_mode_rule WHERE plan_id=?"
        params: list[Any] = [plan_id]
        if mode:
            sql += " AND mode=?"
            params.append(mode)
        sql += " ORDER BY sort, id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_mode_courses(self, plan_id: int, mode: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM plan_mode_course WHERE plan_id=?"
        params: list[Any] = [plan_id]
        if mode:
            sql += " AND mode=?"
            params.append(mode)
        sql += " ORDER BY id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_mode_scopes(self, plan_id: int, mode: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM plan_mode_scope WHERE plan_id=?"
        params: list[Any] = [plan_id]
        if mode:
            sql += " AND mode=?"
            params.append(mode)
        sql += " ORDER BY id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def update_course_notes(self, plan_id: int, notes: dict[str, str]) -> int:
        """仅更新课程备注列（不动其他字段/表）——供模式回填用。"""
        n = 0
        for code, note in (notes or {}).items():
            if not code or code.startswith("name:"):
                continue
            cur = self.conn.execute(
                "UPDATE plan_course SET note=? WHERE plan_id=? AND course_code=?",
                (note, plan_id, code),
            )
            n += cur.rowcount
        self.conn.commit()
        return n

    def set_prereq_verified(self, plan_id: int, edges: list[dict[str, Any]], verifier: str) -> int:
        """管理员校对：按 (from,to) 置 verified=1，并支持删除/新增。"""
        now = _now()
        n = 0
        for e in edges:
            action = str(e.get("action", "confirm"))
            frm = str(e.get("from", "")).strip()
            to = str(e.get("to", "")).strip()
            if not frm or not to:
                continue
            if action == "delete":
                self.conn.execute(
                    "DELETE FROM plan_prereq_edge WHERE plan_id=? AND from_course_name=? AND to_course_name=?",
                    (plan_id, frm, to),
                )
                n += 1
            elif action == "add":
                self.conn.execute(
                    "INSERT INTO plan_prereq_edge (plan_id,from_course_name,to_course_name,"
                    "source,verified,verified_by,verified_at) VALUES (?,?,?,'manual',1,?,?)",
                    (plan_id, frm, to, verifier, now),
                )
                n += 1
            else:  # confirm / 修正
                self.conn.execute(
                    "UPDATE plan_prereq_edge SET verified=1, verified_by=?, verified_at=?"
                    " WHERE plan_id=? AND from_course_name=? AND to_course_name=?",
                    (verifier, now, plan_id, frm, to),
                )
                n += 1
        self.conn.commit()
        return n
