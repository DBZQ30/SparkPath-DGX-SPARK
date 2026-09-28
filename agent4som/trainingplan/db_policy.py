"""培养方案存储层 —— 政策 DAO：转专业 / 专业选择（设计 005 §6）。

`trainingplan/db.py` 门面按 mixin 组装；来源均为 plan_source_doc 派生
（source_doc_id 关联，delete_source_doc 级联清理）。
"""
from __future__ import annotations

import sqlite3
from typing import Any


class PolicyDaoMixin:
    """plan_transfer_plan / plan_transfer_rule / plan_major_selection_* 读写。"""

    conn: sqlite3.Connection

    def replace_transfer_policy(self, policy_year: str, plans: list[dict[str, Any]],
                                rules: list[dict[str, Any]],
                                source_doc_id: int = 0) -> None:
        """写入转专业政策（幂等：先删该 policy_year 旧数据）。"""
        self.conn.execute("DELETE FROM plan_transfer_plan WHERE policy_year=?", (policy_year,))
        self.conn.execute("DELETE FROM plan_transfer_rule WHERE policy_year=?", (policy_year,))
        self.conn.executemany(
            "INSERT INTO plan_transfer_plan (policy_year,major,entry_year,scope,quota,source_doc_id)"
            " VALUES (?,?,?,?,?,?)",
            [(policy_year, p["major"], p["entry_year"], p["scope"], int(p["quota"]), source_doc_id)
             for p in plans],
        )
        self.conn.executemany(
            "INSERT INTO plan_transfer_rule (policy_year,rule_type,item,value,detail,sort,source_doc_id)"
            " VALUES (?,?,?,?,?,?,?)",
            [(policy_year, r["rule_type"], r["item"], r.get("value", ""), r.get("detail", ""),
              i, source_doc_id) for i, r in enumerate(rules)],
        )
        self.conn.commit()

    def get_transfer_plans(self, policy_year: str = "", major: str = "",
                           entry_year: str = "") -> list[dict[str, Any]]:
        conds, params = [], []
        if policy_year:
            conds.append("policy_year=?"); params.append(policy_year)
        if major:
            conds.append("major=?"); params.append(major)
        if entry_year:
            conds.append("entry_year=?"); params.append(entry_year)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        rows = self.conn.execute(
            f"SELECT * FROM plan_transfer_plan{where} ORDER BY major, entry_year, scope", params
        ).fetchall()
        return [dict(r) for r in rows]

    def get_transfer_rules(self, policy_year: str = "", rule_type: str = "") -> list[dict[str, Any]]:
        conds, params = [], []
        if policy_year:
            conds.append("policy_year=?"); params.append(policy_year)
        if rule_type:
            conds.append("rule_type=?"); params.append(rule_type)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        rows = self.conn.execute(
            f"SELECT * FROM plan_transfer_rule{where} ORDER BY sort, id", params
        ).fetchall()
        return [dict(r) for r in rows]

    def add_transfer_rules(self, policy_year: str, rules: list[dict[str, Any]],
                           source_doc_id: int = 0) -> int:
        """追加转专业规则（不删接收计划）；同 (rule_type,item) 先删再插（幂等）。"""
        for r in rules:
            self.conn.execute(
                "DELETE FROM plan_transfer_rule WHERE policy_year=? AND rule_type=? AND item=?",
                (policy_year, r["rule_type"], r["item"]))
        self.conn.executemany(
            "INSERT INTO plan_transfer_rule (policy_year,rule_type,item,value,detail,sort,source_doc_id)"
            " VALUES (?,?,?,?,?,?,?)",
            [(policy_year, r["rule_type"], r["item"], r.get("value", ""), r.get("detail", ""),
              i, source_doc_id) for i, r in enumerate(rules)],
        )
        self.conn.commit()
        return len(rules)

    def latest_transfer_policy_year(self) -> str:
        row = self.conn.execute(
            "SELECT policy_year FROM plan_transfer_rule WHERE policy_year<>''"
            " ORDER BY policy_year DESC LIMIT 1").fetchone()
        if row:
            return row["policy_year"]
        row = self.conn.execute(
            "SELECT policy_year FROM plan_transfer_plan WHERE policy_year<>''"
            " ORDER BY policy_year DESC LIMIT 1").fetchone()
        return row["policy_year"] if row else ""

    def replace_major_selection_policy(self, entry_year: str, plans: list[dict[str, Any]],
                                       rules: list[dict[str, Any]],
                                       source_doc_id: int = 0) -> None:
        self.conn.execute("DELETE FROM plan_major_selection_plan WHERE entry_year=?", (entry_year,))
        self.conn.execute("DELETE FROM plan_major_selection_rule WHERE entry_year=?", (entry_year,))
        self.conn.executemany(
            "INSERT INTO plan_major_selection_plan (entry_year,major,quota,source_doc_id)"
            " VALUES (?,?,?,?)",
            [(entry_year, p["major"], int(p["quota"]), source_doc_id) for p in plans],
        )
        self.conn.executemany(
            "INSERT INTO plan_major_selection_rule (entry_year,rule_type,item,value,detail,sort,source_doc_id)"
            " VALUES (?,?,?,?,?,?,?)",
            [(entry_year, r["rule_type"], r["item"], r.get("value", ""), r.get("detail", ""),
              i, source_doc_id) for i, r in enumerate(rules)],
        )
        self.conn.commit()

    def get_major_selection_plans(self, entry_year: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM plan_major_selection_plan"
        params: list[Any] = []
        if entry_year:
            sql += " WHERE entry_year=?"
            params.append(entry_year)
        sql += " ORDER BY major"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_major_selection_rules(self, entry_year: str = "",
                                  rule_type: str = "") -> list[dict[str, Any]]:
        conds, params = [], []
        if entry_year:
            conds.append("entry_year=?"); params.append(entry_year)
        if rule_type:
            conds.append("rule_type=?"); params.append(rule_type)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        rows = self.conn.execute(
            f"SELECT * FROM plan_major_selection_rule{where} ORDER BY sort, id", params
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_major_selection_entry_year(self) -> str:
        row = self.conn.execute(
            "SELECT entry_year FROM plan_major_selection_rule WHERE entry_year<>''"
            " ORDER BY entry_year DESC LIMIT 1").fetchone()
        return row["entry_year"] if row else ""
