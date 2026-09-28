"""培养方案存储层 —— 学生侧 DAO：解读留痕 / 身份绑定 / 原件登记。

`trainingplan/db.py` 门面按 mixin 组装。绑定只存"openid ↔ 学号"关系
（设计 §6.7，校验由上层 identity 经 warning.db.roster 负责）；原件登记表
只落本库，不入公共知识库、不写 warning.db（2026-09-23 用户明确要求）。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from .db_core import ALL_GRADES, _now
from .models import StudentBinding


class StudentDaoMixin:
    """plan_interpret_run / plan_student_binding / plan_source_doc。"""

    conn: sqlite3.Connection

    def record_interpret_run(self, user_id: str, major: str, entry_year: str,
                             summary: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO plan_interpret_run (user_id,major,entry_year,created_at,summary_json)"
            " VALUES (?,?,?,?,?)",
            (user_id, major, entry_year, _now(), json.dumps(summary, ensure_ascii=False)),
        )
        self.conn.commit()

    # ────────────────── 学生身份绑定（openid ↔ 学号，设计 §6.7） ──────────────────

    @staticmethod
    def _row_to_binding(row: sqlite3.Row) -> StudentBinding:
        return StudentBinding(
            id=int(row["id"]),
            platform=row["platform"] or "",
            user_id=row["user_id"] or "",
            student_id=row["student_id"] or "",
            name=row["name"] or "",
            grade=row["grade"] or "",
            major=row["major"] or "",
            status=row["status"] or "active",
            verified_at=row["verified_at"] or "",
            created_at=row["created_at"] or "",
        )

    def get_binding(self, platform: str, user_id: str) -> Optional[StudentBinding]:
        row = self.conn.execute(
            "SELECT * FROM plan_student_binding WHERE platform=? AND user_id=?",
            (platform, user_id),
        ).fetchone()
        return self._row_to_binding(row) if row else None

    def upsert_binding(self, platform: str, user_id: str, student_id: str,
                       name: str = "", grade: str = "", major: str = "") -> None:
        """写入/更新绑定（同 (platform, user_id) 覆盖）。校验由上层负责。"""
        now = _now()
        self.conn.execute(
            "INSERT INTO plan_student_binding"
            " (platform,user_id,student_id,name,grade,major,status,verified_at,created_at)"
            " VALUES (?,?,?,?,?,?,'active',?,?)"
            " ON CONFLICT(platform, user_id) DO UPDATE SET"
            " student_id=excluded.student_id, name=excluded.name, grade=excluded.grade,"
            " major=excluded.major, status='active', verified_at=excluded.verified_at",
            (platform, user_id, student_id, name, grade, major, now, now),
        )
        self.conn.commit()

    def delete_binding(self, platform: str, user_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM plan_student_binding WHERE platform=? AND user_id=?",
            (platform, user_id),
        )
        self.conn.commit()
        return cur.rowcount == 1

    # ────────────────────── 原件登记（本功能自用，不入公共库） ──────────────────────

    def upsert_source_doc(self, doc: dict[str, Any]) -> Optional[int]:
        """幂等登记：按 **(文件名, 内容哈希)** 去重（与存放路径无关）。

        这样同一份文件无论从命令行 ingest-dir 还是小程序上传，都不会重复入库。
        """
        row = self.conn.execute(
            "SELECT id FROM plan_source_doc WHERE file_name=? AND file_hash=?",
            (doc.get("file_name", ""), doc.get("file_hash", "")),
        ).fetchone()
        if row:
            return None
        applies = doc.get("applies_to") or [ALL_GRADES]
        cur = self.conn.execute(
            "INSERT INTO plan_source_doc (category,file_name,file_path,file_hash,size_bytes,"
            "ext,major,entry_year,text_chars,text_content,ingested_at,note,applies_to)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc.get("category", ""), doc.get("file_name", ""), doc.get("file_path", ""),
             doc.get("file_hash", ""), int(doc.get("size_bytes", 0)), doc.get("ext", ""),
             doc.get("major", ""), doc.get("entry_year", ""), int(doc.get("text_chars", 0)),
             doc.get("text_content", ""), _now(), doc.get("note", ""),
             json.dumps([str(x) for x in applies], ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_source_docs(self, category: str = "", major: str = "") -> list[dict[str, Any]]:
        sql = ("SELECT id,category,file_name,size_bytes,major,entry_year,text_chars,"
               "ingested_at,note,applies_to FROM plan_source_doc")
        conds, params = [], []
        if category:
            conds.append("category=?"); params.append(category)
        if major:
            conds.append("major=?"); params.append(major)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY category, id"
        out = []
        for r in self.conn.execute(sql, params).fetchall():
            item = dict(r)
            try:
                item["applies_to"] = json.loads(item.get("applies_to") or "[]") or [ALL_GRADES]
            except Exception:
                item["applies_to"] = [ALL_GRADES]
            out.append(item)
        return out

    def source_doc_stats(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT category, COUNT(*) AS c FROM plan_source_doc GROUP BY category"
        ).fetchall()
        out = {r["category"]: int(r["c"]) for r in rows}
        out["_total"] = sum(out.values())
        return out

    def get_source_doc_text(self, doc_id: int) -> str:
        row = self.conn.execute(
            "SELECT text_content FROM plan_source_doc WHERE id=?", (doc_id,)
        ).fetchone()
        return row["text_content"] if row else ""
