"""文件中心 —— SQLite 存储层（`data/doc_center.db`，WAL）。

表：doc_file（原件身份）/ doc_applicability（功能×年级×专业）/ doc_parse_job（解析任务）。
只放公共教学文件的**目录与适用性**；解析产物在 `training_plan.db` / `warning.db`。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Optional

from .models import (
    ALL_GRADES,
    FEATURE_PIPELINE,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PARSING,
    STATUS_QUEUED,
    Applicability,
    DocFile,
    ParseJob,
)
import contextlib

DB_PATH = os.environ.get("DOC_CENTER_DB", "data/doc_center.db")

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS doc_file (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  file_hash    TEXT NOT NULL UNIQUE,
  file_name    TEXT DEFAULT '',
  file_path    TEXT DEFAULT '',
  ext          TEXT DEFAULT '',
  size_bytes   INTEGER DEFAULT 0,
  doc_type     TEXT DEFAULT '其他',
  subject_major TEXT DEFAULT '',
  subject_grade TEXT DEFAULT '',
  title        TEXT DEFAULT '',
  uploaded_by  TEXT DEFAULT '',
  uploaded_at  TEXT DEFAULT '',
  note         TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_docfile_type ON doc_file(doc_type);

CREATE TABLE IF NOT EXISTS doc_applicability (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id    INTEGER NOT NULL,
  feature     TEXT NOT NULL,
  grade      TEXT NOT NULL DEFAULT '全部',
  major      TEXT NOT NULL DEFAULT '',
  created_at TEXT DEFAULT '',
  UNIQUE (file_id, feature, grade, major)
);
CREATE INDEX IF NOT EXISTS idx_appl_lookup ON doc_applicability(feature, grade, major);

CREATE TABLE IF NOT EXISTS doc_parse_job (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id     INTEGER NOT NULL,
  pipeline    TEXT NOT NULL,
  status      TEXT DEFAULT 'queued',
  queue_seq   INTEGER DEFAULT 0,
  output_ref  TEXT DEFAULT '{}',
  note        TEXT DEFAULT '',
  error       TEXT DEFAULT '',
  enqueued_at TEXT DEFAULT '',
  started_at  TEXT DEFAULT '',
  finished_at TEXT DEFAULT '',
  UNIQUE (file_id, pipeline)
);
CREATE INDEX IF NOT EXISTS idx_job_status ON doc_parse_job(status, queue_seq);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _as_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


class DocCenterDB:
    def __init__(self, db_path: Optional[str] = None):
        db_path = db_path or DB_PATH
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_DDL)
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.conn.commit()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()

    # ─────────────────────────── 原件 ───────────────────────────

    def get_file_by_hash(self, file_hash: str) -> Optional[DocFile]:
        row = self.conn.execute("SELECT * FROM doc_file WHERE file_hash=?", (file_hash,)).fetchone()
        return self._row_to_file(row) if row else None

    def get_file(self, file_id: int) -> Optional[DocFile]:
        row = self.conn.execute("SELECT * FROM doc_file WHERE id=?", (file_id,)).fetchone()
        return self._row_to_file(row) if row else None

    def upsert_file(self, f: DocFile) -> tuple[int, bool]:
        """按 file_hash 去重：已存在返回 (id, False)；否则插入 (id, True)。"""
        existed = self.get_file_by_hash(f.file_hash)
        if existed:
            return int(existed.id), False
        cur = self.conn.execute(
            "INSERT INTO doc_file (file_hash,file_name,file_path,ext,size_bytes,doc_type,"
            "subject_major,subject_grade,title,uploaded_by,uploaded_at,note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f.file_hash, f.file_name, f.file_path, f.ext, int(f.size_bytes), f.doc_type,
             f.subject_major, f.subject_grade, f.title, f.uploaded_by, f.uploaded_at or _now(),
             f.note),
        )
        self.conn.commit()
        return int(cur.lastrowid), True

    def update_file(self, file_id: int, **fields: Any) -> bool:
        allowed = {"doc_type", "subject_major", "subject_grade", "title", "note", "file_name"}
        cols = [k for k in fields if k in allowed]
        if not cols:
            return False
        sets = ", ".join(f"{c}=?" for c in cols)
        cur = self.conn.execute(f"UPDATE doc_file SET {sets} WHERE id=?",
                                [fields[c] for c in cols] + [file_id])
        self.conn.commit()
        return cur.rowcount == 1

    def list_files(self, doc_type: str = "", feature: str = "", grade: str = "",
                   major: str = "", status: str = "") -> list[dict[str, Any]]:
        sql = ("SELECT f.* FROM doc_file f")
        conds, params = [], []
        if feature:
            sql += (" JOIN doc_applicability a ON a.file_id=f.id")
            conds.append("a.feature=?"); params.append(feature)
            if grade:
                conds.append("(a.grade=? OR a.grade=?)"); params += [grade, ALL_GRADES]
        if doc_type:
            conds.append("f.doc_type=?"); params.append(doc_type)
        if major:
            conds.append("f.subject_major=?"); params.append(major)
        if status:
            sql += " JOIN doc_parse_job j ON j.file_id=f.id"
            conds.append("j.status=?"); params.append(status)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " GROUP BY f.id ORDER BY f.id DESC"
        rows = self.conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["applicability"] = self.get_applicability(file_id=item["id"])
            item["jobs"] = self.get_jobs(file_id=item["id"])
            out.append(item)
        return out

    def delete_file(self, file_id: int) -> None:
        """删除原件 + 级联删除适用性与 job（产物由调用方按功能级联处理）。"""
        self.conn.execute("DELETE FROM doc_applicability WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM doc_parse_job WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM doc_file WHERE id=?", (file_id,))
        self.conn.commit()

    @staticmethod
    def _row_to_file(row: sqlite3.Row) -> DocFile:
        return DocFile(
            id=int(row["id"]), file_hash=row["file_hash"], file_name=row["file_name"] or "",
            file_path=row["file_path"] or "", ext=row["ext"] or "",
            size_bytes=int(row["size_bytes"] or 0), doc_type=row["doc_type"] or "其他",
            subject_major=row["subject_major"] or "", subject_grade=row["subject_grade"] or "",
            title=row["title"] or "", uploaded_by=row["uploaded_by"] or "",
            uploaded_at=row["uploaded_at"] or "", note=row["note"] or "",
        )

    # ─────────────────────────── 适用性 ───────────────────────────

    def set_applicability(self, file_id: int, rows: list[Applicability]) -> None:
        """整体替换某文件的适用性（幂等）。"""
        self.conn.execute("DELETE FROM doc_applicability WHERE file_id=?", (file_id,))
        now = _now()
        seen = set()
        for r in rows:
            key = (r.feature, r.grade or ALL_GRADES, r.major or "")
            if key in seen:
                continue
            seen.add(key)
            self.conn.execute(
                "INSERT OR IGNORE INTO doc_applicability (file_id,feature,grade,major,created_at)"
                " VALUES (?,?,?,?,?)",
                (file_id, r.feature, r.grade or ALL_GRADES, r.major or "", now),
            )
        self.conn.commit()

    def get_applicability(self, file_id: int = 0, feature: str = "", grade: str = "",
                          major: str = "") -> list[dict[str, Any]]:
        conds, params = [], []
        if file_id:
            conds.append("file_id=?"); params.append(file_id)
        if feature:
            conds.append("feature=?"); params.append(feature)
            if grade:
                conds.append("(grade=? OR grade=?)"); params += [grade, ALL_GRADES]
            if major:
                conds.append("(major=? OR major='')"); params.append(major)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        rows = self.conn.execute(
            f"SELECT * FROM doc_applicability{where} ORDER BY feature, grade, major", params
        ).fetchall()
        return [dict(r) for r in rows]

    # ─────────────────────────── 解析任务 ───────────────────────────

    def _next_seq(self) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(queue_seq),0)+1 AS s FROM doc_parse_job").fetchone()
        return int(row["s"])

    def ensure_job(self, file_id: int, pipeline: str, reset: bool = False) -> None:
        """建/重置解析任务（同 file×pipeline 唯一）。"""
        row = self.conn.execute(
            "SELECT id FROM doc_parse_job WHERE file_id=? AND pipeline=?", (file_id, pipeline)
        ).fetchone()
        now = _now()
        if row:
            if reset:
                self.conn.execute(
                    "UPDATE doc_parse_job SET status=?, queue_seq=?, error='', started_at='',"
                    " finished_at='' WHERE id=?",
                    (STATUS_QUEUED, self._next_seq(), row["id"]),
                )
        else:
            self.conn.execute(
                "INSERT INTO doc_parse_job (file_id,pipeline,status,queue_seq,output_ref,enqueued_at)"
                " VALUES (?,?,?,?,'{}',?)",
                (file_id, pipeline, STATUS_QUEUED, self._next_seq(), now),
            )
        self.conn.commit()

    def claim_next_job(self, pipelines: list[str]) -> Optional[ParseJob]:
        if not pipelines:
            return None
        marks = ",".join("?" for _ in pipelines)
        row = self.conn.execute(
            f"SELECT id FROM doc_parse_job WHERE status=? AND pipeline IN ({marks})"
            " ORDER BY queue_seq ASC LIMIT 1",
            [STATUS_QUEUED, *list(pipelines)],
        ).fetchone()
        if not row:
            return None
        jid = int(row["id"])
        cur = self.conn.execute(
            "UPDATE doc_parse_job SET status=?, started_at=? WHERE id=? AND status=?",
            (STATUS_PARSING, _now(), jid, STATUS_QUEUED),
        )
        self.conn.commit()
        if cur.rowcount != 1:
            return None
        return self.get_job(jid)

    def finish_job(self, job_id: int, status: str, output_ref: dict | None = None,
                   note: str = "", error: str = "") -> None:
        self.conn.execute(
            "UPDATE doc_parse_job SET status=?, output_ref=?, note=?, error=?, finished_at=?"
            " WHERE id=?",
            (status, json.dumps(output_ref or {}, ensure_ascii=False), note, error, _now(), job_id),
        )
        self.conn.commit()

    def get_job(self, job_id: int) -> Optional[ParseJob]:
        row = self.conn.execute("SELECT * FROM doc_parse_job WHERE id=?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def get_jobs(self, file_id: int = 0, pipeline: str = "") -> list[dict[str, Any]]:
        conds, params = [], []
        if file_id:
            conds.append("file_id=?"); params.append(file_id)
        if pipeline:
            conds.append("pipeline=?"); params.append(pipeline)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        rows = self.conn.execute(
            f"SELECT * FROM doc_parse_job{where} ORDER BY pipeline", params
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> ParseJob:
        try:
            ref = json.loads(row["output_ref"] or "{}")
        except Exception:
            ref = {}
        return ParseJob(
            id=int(row["id"]), file_id=int(row["file_id"]), pipeline=row["pipeline"],
            status=row["status"] or STATUS_QUEUED, queue_seq=int(row["queue_seq"] or 0),
            output_ref=ref, note=row["note"] or "", error=row["error"] or "",
            enqueued_at=row["enqueued_at"] or "", started_at=row["started_at"] or "",
            finished_at=row["finished_at"] or "",
        )

    def queue_summary(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS c FROM doc_parse_job GROUP BY status").fetchall()
        out = {"total": 0, "queued": 0, "parsing": 0, "done": 0, "failed": 0}
        for r in rows:
            out[r["status"]] = int(r["c"])
            out["total"] += int(r["c"])
        return out

    def recover_stale(self) -> int:
        """启动清理：遗留 parsing → failed；**已委派**（output_ref 非空）的 job 跳过，
        交给 `sync_plan_jobs` 依 `plan_document` 状态收尾。"""
        cur = self.conn.execute(
            "UPDATE doc_parse_job SET status=?, error='解析进程中断，可重试', finished_at=?"
            " WHERE status=? AND (output_ref IS NULL OR output_ref IN ('', '{}'))",
            (STATUS_FAILED, _now(), STATUS_PARSING),
        )
        self.conn.commit()
        return int(cur.rowcount)

    # ─────────────────────────── 自动引用 ───────────────────────────

    def resolve(self, feature: str, grade: str = "", major: str = "") -> list[dict[str, Any]]:
        """按 (feature, grade, major) 取适用文件 + 该功能所需管线的产物定位。

        只返回**该功能所需管线已 done** 的文件；未就绪的文件也返回（`ready=false`），
        由调用方决定是否降级。
        """
        appl = self.get_applicability(feature=feature, grade=grade, major=major)
        # 同一文件可能命中多行（通配「全部」/「不限」+ 具体项）；按 file_id 去重，
        # 保留更精确的一行（精确年级/专业优先于通配），避免调用方重复处理。
        best: dict[int, tuple[tuple[bool, bool], dict[str, Any]]] = {}
        for a in appl:
            fid = a["file_id"]
            score = (bool(grade) and a.get("grade") == grade,
                     bool(major) and a.get("major") == major)
            cur = best.get(fid)
            if cur is None or score > cur[0]:
                best[fid] = (score, a)
        out = []
        want_pipe = FEATURE_PIPELINE.get(feature, "")
        for _score, a in best.values():
            f = self.get_file(a["file_id"])
            if not f:
                continue
            jobs = {j["pipeline"]: j for j in self.get_jobs(file_id=f.id)}
            # 优先该功能的主管线；否则回退任一已完成管线（如大纲/政策走 text）
            job = jobs.get(want_pipe)
            if job is None or job["status"] != STATUS_DONE:
                done_jobs = [j for j in jobs.values() if j["status"] == STATUS_DONE]
                if done_jobs:
                    job = done_jobs[0]
                elif job is None and jobs:
                    job = next(iter(jobs.values()))
            out.append({
                "file_id": f.id, "file_hash": f.file_hash, "file_name": f.file_name,
                "file_path": f.file_path, "doc_type": f.doc_type,
                "subject_major": f.subject_major, "subject_grade": f.subject_grade,
                "feature": a["feature"], "grade": a["grade"], "major": a["major"],
                "pipeline": (job or {}).get("pipeline", want_pipe),
                "ready": bool(job and job["status"] == STATUS_DONE),
                "status": (job or {}).get("status", ""),
                "output_ref": _as_dict((job or {}).get("output_ref")),
            })
        return out
