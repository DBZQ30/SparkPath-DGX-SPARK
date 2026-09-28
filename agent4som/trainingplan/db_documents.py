"""培养方案存储层 —— 文档/队列 DAO（plan_document 及其派生表）。

`trainingplan/db.py` 门面按 mixin 组装；本模块只动 plan_document 与
plan_source_doc 的登记/删除/队列语义（状态机：queued → parsing → done/failed）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Optional

from .models import STATUS_DONE, STATUS_FAILED, STATUS_PARSING, STATUS_QUEUED, PlanDocument
from .db_core import ALL_GRADES, _load_applies_to, _now


class DocumentDaoMixin:
    """plan_document / 队列 / 方案删除 / 适用年级。"""

    conn: sqlite3.Connection

    def _next_queue_seq(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(queue_seq), 0) + 1 AS s FROM plan_document"
        ).fetchone()
        return int(row["s"])

    def enqueue_document(self, doc: PlanDocument) -> int:
        """入队：插入一条 `queued` 记录并分配递增 queue_seq。"""
        seq = self._next_queue_seq()
        now = _now()
        applies = doc.applies_to or ([doc.entry_year] if doc.entry_year else [ALL_GRADES])
        cur = self.conn.execute(
            "INSERT INTO plan_document (major, entry_year, version_label, file_name,"
            " file_hash, file_path, upload_time, uploader, parsed_status, queue_seq,"
            " enqueued_at, started_at, finished_at, is_active, in_file_meta, applies_to)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,'','',?,?,?)",
            (
                doc.major, doc.entry_year, doc.version_label, doc.file_name,
                doc.file_hash, doc.file_path, doc.upload_time or now, doc.uploader,
                STATUS_QUEUED, seq, now, doc.is_active,
                json.dumps(doc.in_file_meta or {}, ensure_ascii=False),
                json.dumps(applies, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def insert_rejected(self, doc: PlanDocument, reason: str) -> int:
        """校验拒绝：直接落 `rejected`（不入队），供状态区展示。"""
        now = _now()
        meta = dict(doc.in_file_meta or {})
        meta["error"] = reason
        cur = self.conn.execute(
            "INSERT INTO plan_document (major, entry_year, version_label, file_name,"
            " file_hash, file_path, upload_time, uploader, parsed_status, queue_seq,"
            " enqueued_at, started_at, finished_at, is_active, in_file_meta)"
            " VALUES (?,?,?,?,?,?,?,?,?,0,?,'',?,0,?)",
            (
                doc.major, doc.entry_year, doc.version_label, doc.file_name,
                doc.file_hash, doc.file_path, doc.upload_time or now, doc.uploader,
                "rejected", now, now, json.dumps(meta, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def deactivate_other_versions(self, major: str, entry_year: str, keep_id: int) -> None:
        self.conn.execute(
            "UPDATE plan_document SET is_active=0 WHERE major=? AND entry_year=? AND id<>?",
            (major, entry_year, keep_id),
        )
        self.conn.commit()

    def claim_next(self) -> Optional[PlanDocument]:
        """原子领取队列中 queue_seq 最小的一条 queued → parsing。"""
        row = self.conn.execute(
            "SELECT id FROM plan_document WHERE parsed_status=? ORDER BY queue_seq ASC LIMIT 1",
            (STATUS_QUEUED,),
        ).fetchone()
        if not row:
            return None
        pid = int(row["id"])
        now = _now()
        cur = self.conn.execute(
            "UPDATE plan_document SET parsed_status=?, started_at=? WHERE id=? AND parsed_status=?",
            (STATUS_PARSING, now, pid, STATUS_QUEUED),
        )
        self.conn.commit()
        if cur.rowcount != 1:
            return None  # 竞态：已被别的 worker 领走
        return self.get_document(pid)

    def finish_document(self, doc_id: int, status: str, note: str = "",
                        error: str = "", meta_extra: dict[str, Any] | None = None) -> None:
        doc = self.get_document(doc_id)
        meta = dict(doc.in_file_meta) if doc else {}
        if note:
            meta["note"] = note
        if error:
            meta["error"] = error
        if meta_extra:
            meta.update(meta_extra)
        self.conn.execute(
            "UPDATE plan_document SET parsed_status=?, finished_at=?, in_file_meta=? WHERE id=?",
            (status, _now(), json.dumps(meta, ensure_ascii=False), doc_id),
        )
        self.conn.commit()

    def recover_stale(self) -> int:
        """启动清理：遗留 parsing → failed（进程被中断），queued 保留。"""
        cur = self.conn.execute(
            "UPDATE plan_document SET parsed_status=?, finished_at=?,"
            " in_file_meta=json_set(COALESCE(in_file_meta,'{}'), '$.error', '解析进程中断，可重试')"
            " WHERE parsed_status=?",
            (STATUS_FAILED, _now(), STATUS_PARSING),
        )
        self.conn.commit()
        return int(cur.rowcount)

    def retry(self, doc_id: int) -> bool:
        doc = self.get_document(doc_id)
        if not doc or doc.parsed_status not in (STATUS_FAILED,):
            return False
        seq = self._next_queue_seq()
        self.conn.execute(
            "UPDATE plan_document SET parsed_status=?, queue_seq=?, enqueued_at=?,"
            " started_at='', finished_at='' WHERE id=?",
            (STATUS_QUEUED, seq, _now(), doc_id),
        )
        self.conn.commit()
        return True

    # ────────────────────────── 查询 ──────────────────────────

    @staticmethod
    def _row_to_doc(row: sqlite3.Row) -> PlanDocument:
        return PlanDocument(
            id=int(row["id"]),
            major=row["major"],
            entry_year=row["entry_year"],
            version_label=row["version_label"] or "",
            file_name=row["file_name"] or "",
            file_hash=row["file_hash"] or "",
            file_path=row["file_path"] or "",
            upload_time=row["upload_time"] or "",
            uploader=row["uploader"] or "",
            parsed_status=row["parsed_status"] or "",
            queue_seq=int(row["queue_seq"] or 0),
            enqueued_at=row["enqueued_at"] or "",
            started_at=row["started_at"] or "",
            finished_at=row["finished_at"] or "",
            is_active=int(row["is_active"] or 0),
            in_file_meta=json.loads(row["in_file_meta"] or "{}"),
            applies_to=_load_applies_to(row),
        )

    def get_document(self, doc_id: int) -> Optional[PlanDocument]:
        row = self.conn.execute("SELECT * FROM plan_document WHERE id=?", (doc_id,)).fetchone()
        return self._row_to_doc(row) if row else None

    def get_active_document(self, major: str, entry_year: str) -> Optional[PlanDocument]:
        row = self.conn.execute(
            "SELECT * FROM plan_document WHERE major=? AND entry_year=? AND is_active=1"
            " ORDER BY id DESC LIMIT 1",
            (major, entry_year),
        ).fetchone()
        return self._row_to_doc(row) if row else None

    def find_document_for_grade(self, major: str, grade: str) -> Optional[PlanDocument]:
        """按年级解析方案：**精确匹配 applies_to 优先，其次 applies_to 含"全部"**。

        返回 is_active=1 中最新的一条；无匹配返回 None。
        """
        rows = self.conn.execute(
            "SELECT * FROM plan_document WHERE major=? AND is_active=1 ORDER BY id DESC",
            (major,),
        ).fetchall()
        exact = allg = None
        for r in rows:
            ap = _load_applies_to(r)
            if grade in ap:
                exact = exact or self._row_to_doc(r)
            elif ALL_GRADES in ap:
                allg = allg or self._row_to_doc(r)
        return exact or allg

    def set_document_applies_to(self, doc_id: int, applies_to: list[str]) -> bool:
        """设置培养方案适用年级。空列表 = 重置为「主年级」（而非"全部"）。"""
        applies = [str(x) for x in (applies_to or [])]
        if not applies:
            doc = self.get_document(doc_id)
            applies = [doc.entry_year] if (doc and doc.entry_year) else [ALL_GRADES]
        cur = self.conn.execute(
            "UPDATE plan_document SET applies_to=? WHERE id=?",
            (json.dumps(applies, ensure_ascii=False), doc_id),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def set_source_doc_applies_to(self, doc_id: int, applies_to: list[str]) -> bool:
        applies = [str(x) for x in (applies_to or [])]
        if not applies:
            applies = [ALL_GRADES]
        cur = self.conn.execute(
            "UPDATE plan_source_doc SET applies_to=? WHERE id=?",
            (json.dumps(applies, ensure_ascii=False), doc_id),
        )
        self.conn.commit()
        return cur.rowcount == 1

    # 培养方案解析产物子表（删除方案时级联清理；doccenter 也复用此清单）
    PLAN_CHILD_TABLES = (
        "plan_credit_node", "plan_course", "plan_semester_course", "plan_prereq_edge",
        "plan_graduation_req", "plan_mode_rule", "plan_mode_course", "plan_mode_scope",
    )

    def delete_document(self, doc_id: int) -> bool:
        """删除培养方案版本及其全部解析产物。不存在返回 False。"""
        row = self.conn.execute("SELECT id FROM plan_document WHERE id=?", (doc_id,)).fetchone()
        if not row:
            return False
        for table in self.PLAN_CHILD_TABLES:
            self.conn.execute(f"DELETE FROM {table} WHERE plan_id=?", (doc_id,))
        self.conn.execute("DELETE FROM plan_document WHERE id=?", (doc_id,))
        self.conn.commit()
        return True

    def delete_source_doc(self, doc_id: int) -> bool:
        """删除已入库资料及其派生的转专业/专业选择规则。不存在返回 False。"""
        row = self.conn.execute("SELECT id FROM plan_source_doc WHERE id=?", (doc_id,)).fetchone()
        if not row:
            return False
        for table in ("plan_transfer_plan", "plan_transfer_rule",
                      "plan_major_selection_plan", "plan_major_selection_rule"):
            self.conn.execute(f"DELETE FROM {table} WHERE source_doc_id=?", (doc_id,))
        self.conn.execute("DELETE FROM plan_source_doc WHERE id=?", (doc_id,))
        self.conn.commit()
        return True

    def find_done_by_hash(self, major: str, entry_year: str, file_hash: str) -> Optional[PlanDocument]:
        row = self.conn.execute(
            "SELECT * FROM plan_document WHERE major=? AND entry_year=? AND file_hash=?"
            " AND parsed_status=? ORDER BY id DESC LIMIT 1",
            (major, entry_year, file_hash, STATUS_DONE),
        ).fetchone()
        return self._row_to_doc(row) if row else None

    def list_documents(self, major: str = "", entry_year: str = "",
                       status: str = "") -> list[PlanDocument]:
        conds, params = [], []
        if major:
            conds.append("major=?"); params.append(major)
        if entry_year:
            conds.append("entry_year=?"); params.append(entry_year)
        if status:
            conds.append("parsed_status=?"); params.append(status)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        rows = self.conn.execute(
            f"SELECT * FROM plan_document{where} ORDER BY id DESC", params
        ).fetchall()
        return [self._row_to_doc(r) for r in rows]

    def list_major_years(self) -> list[dict[str, Any]]:
        """可用（专业, 年级）清单 + 状态 + 先修校对进度（取每个组合的最新一条）。"""
        rows = self.conn.execute(
            "SELECT d.* FROM plan_document d JOIN ("
            "  SELECT major, entry_year, MAX(id) AS mid FROM plan_document"
            "  GROUP BY major, entry_year"
            ") t ON d.id=t.mid ORDER BY d.major, d.entry_year DESC"
        ).fetchall()
        out = []
        for r in rows:
            doc = self._row_to_doc(r)
            # 先修校对进度（仅 done 方案有意义）
            pv = pt = 0
            if doc.parsed_status == STATUS_DONE:
                row = self.conn.execute(
                    "SELECT COUNT(*) AS total, SUM(verified) AS verified"
                    " FROM plan_prereq_edge WHERE plan_id=?",
                    (doc.id,),
                ).fetchone()
                pt = int(row["total"] or 0)
                pv = int(row["verified"] or 0)
            out.append({
                "major": doc.major,
                "entry_year": doc.entry_year,
                "status": doc.parsed_status,
                "version_label": doc.version_label,
                "file_name": doc.file_name,
                "upload_time": doc.upload_time,
                "note": doc.note,
                "error": doc.error,
                "id": doc.id,
                "applies_to": doc.applies_to,
                "prereq_total": pt,
                "prereq_verified": pv,
            })
        return out

    def available_grades(self, major: str) -> tuple[list[str], bool]:
        """该专业已覆盖的年级：返回 (具体年级列表降序, 是否存在"全部年级"方案)。"""
        grades: set[str] = set()
        has_all = False
        for d in self.list_documents(major=major, status=STATUS_DONE):
            for g in (d.applies_to or []):
                if g == ALL_GRADES:
                    has_all = True
                else:
                    grades.add(g)
        return sorted(grades, reverse=True), has_all

    def queue_summary(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT parsed_status, COUNT(*) AS c FROM plan_document"
            " WHERE parsed_status IN ('queued','parsing','done','failed')"
            " GROUP BY parsed_status"
        ).fetchall()
        s = {"total": 0, "done": 0, "parsing": 0, "queued": 0, "failed": 0}
        for r in rows:
            k = r["parsed_status"]
            if k in s:
                s[k] = int(r["c"])
            s["total"] += int(r["c"])
        return s

    def queue_items(self, major: str = "", entry_year: str = "") -> list[dict[str, Any]]:
        """队列逐项（含 position/ahead/elapsed_s），供进度面板。

        只返回**队列相关**的行（queued/parsing/failed/rejected 等非 done 状态），
        已完成的方案不需要占用队列位置（2026-09-23 修正：混入 done 会让 position 语义错乱）。
        """
        conds, params = [], []
        if major:
            conds.append("major=?"); params.append(major)
        if entry_year:
            conds.append("entry_year=?"); params.append(entry_year)
        where = (" AND " + " AND ".join(conds)) if conds else ""
        rows = self.conn.execute(
            f"SELECT * FROM plan_document WHERE parsed_status<>'done'{where}"
            " ORDER BY queue_seq ASC",
            params,
        ).fetchall()
        docs = [self._row_to_doc(r) for r in rows]
        # position 只针对"还在排队"的项（parsing 项不占排队序号）
        queued = sorted([d for d in docs if d.parsed_status == STATUS_QUEUED],
                        key=lambda d: d.queue_seq)
        pos_of = {d.id: i for i, d in enumerate(queued)}
        now = datetime.now()
        out = []
        for d in docs:
            item: dict[str, Any] = {
                "id": d.id, "major": d.major, "entry_year": d.entry_year,
                "state": d.parsed_status, "version_label": d.version_label,
                "file_name": d.file_name, "upload_time": d.upload_time,
                "note": d.note, "error": d.error,
            }
            if d.parsed_status == STATUS_QUEUED:
                idx = pos_of.get(d.id, 0)
                item["position"] = idx + 1
                item["ahead"] = idx
            if d.parsed_status == STATUS_PARSING and d.started_at:
                try:
                    st = datetime.strptime(d.started_at[:19], "%Y-%m-%d %H:%M:%S")
                    item["elapsed_s"] = int((now - st).total_seconds())
                except Exception:
                    item["elapsed_s"] = None
            if d.parsed_status == STATUS_QUEUED and d.enqueued_at:
                try:
                    st = datetime.strptime(d.enqueued_at[:19], "%Y-%m-%d %H:%M:%S")
                    item["waited_s"] = int((now - st).total_seconds())
                except Exception:
                    item["waited_s"] = None
            out.append(item)
        return out
