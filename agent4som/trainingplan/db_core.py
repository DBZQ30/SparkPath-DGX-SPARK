"""培养方案 SQLite 存储层 —— 连接核心与表结构（`data/training_plan.db`，WAL）。

`trainingplan/db.py` 门面组装的各 DAO mixin 见同目录 `db_*` 模块；本模块
只负责连接创建、建表、旧库列迁移（applies_to 回填）与适用年级工具函数。
独立于 `warning.db`：本库只放培养方案（公开数据），不含任何学生数据。
**例外（设计 §6.7）**：`plan_student_binding` 仅存"openid ↔ 学号"绑定关系
（学号+姓名 经 `warning.db.roster` 校验），**不存成绩**——这是本功能读取个人
成绩的入口，未绑定时上游只给方案级结论。
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from datetime import datetime

ALL_GRADES = "全部"

SCHEMA_VERSION = 5

_DDL = """
CREATE TABLE IF NOT EXISTS plan_document (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  major         TEXT NOT NULL,
  entry_year    TEXT NOT NULL,
  version_label TEXT DEFAULT '',
  file_name     TEXT DEFAULT '',
  file_hash     TEXT DEFAULT '',
  file_path     TEXT DEFAULT '',
  upload_time   TEXT DEFAULT '',
  uploader      TEXT DEFAULT '',
  parsed_status TEXT DEFAULT 'queued',
  queue_seq     INTEGER DEFAULT 0,
  enqueued_at   TEXT DEFAULT '',
  started_at    TEXT DEFAULT '',
  finished_at   TEXT DEFAULT '',
  is_active     INTEGER DEFAULT 1,
  in_file_meta  TEXT DEFAULT '{}',
  -- 适用年级：JSON 数组。["2023级"] / ["2023级","2024级"] / ["全部"]（全部=所有年级）
  applies_to    TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_plan_doc_my   ON plan_document(major, entry_year);
CREATE INDEX IF NOT EXISTS idx_plan_doc_stat ON plan_document(parsed_status, queue_seq);

CREATE TABLE IF NOT EXISTS plan_credit_node (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id     INTEGER NOT NULL,
  parent_path TEXT DEFAULT '',
  path        TEXT DEFAULT '',
  name        TEXT DEFAULT '',
  credit      TEXT DEFAULT '',
  credit_note TEXT DEFAULT '',
  category    TEXT DEFAULT '',
  sort        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_credit_plan ON plan_credit_node(plan_id);

CREATE TABLE IF NOT EXISTS plan_course (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id       INTEGER NOT NULL,
  course_code   TEXT DEFAULT '',
  course_name   TEXT DEFAULT '',
  en_name       TEXT DEFAULT '',
  credit        REAL DEFAULT 0,
  required_flag TEXT DEFAULT '',
  course_type   TEXT DEFAULT '',
  semester      TEXT DEFAULT '',
  provider      TEXT DEFAULT '',
  note          TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_course_plan ON plan_course(plan_id);

CREATE TABLE IF NOT EXISTS plan_semester_course (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id     INTEGER NOT NULL,
  semester    TEXT DEFAULT '',
  course_code TEXT DEFAULT '',
  course_name TEXT DEFAULT '',
  credit      REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sem_plan ON plan_semester_course(plan_id);

CREATE TABLE IF NOT EXISTS plan_prereq_edge (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id          INTEGER NOT NULL,
  from_course_code TEXT DEFAULT '',
  from_course_name TEXT DEFAULT '',
  to_course_code   TEXT DEFAULT '',
  to_course_name   TEXT DEFAULT '',
  source           TEXT DEFAULT 'vl',
  confidence       REAL DEFAULT 0,
  verified         INTEGER DEFAULT 0,
  verified_by      TEXT DEFAULT '',
  verified_at      TEXT DEFAULT '',
  note             TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_prereq_plan ON plan_prereq_edge(plan_id, verified);

CREATE TABLE IF NOT EXISTS plan_graduation_req (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id  INTEGER NOT NULL,
  req_type TEXT DEFAULT '',
  item     TEXT DEFAULT '',
  value    TEXT DEFAULT '',
  unit     TEXT DEFAULT '',
  detail   TEXT DEFAULT '',
  sort     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_grad_plan ON plan_graduation_req(plan_id);

CREATE TABLE IF NOT EXISTS plan_interpret_run (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      TEXT DEFAULT '',
  major        TEXT DEFAULT '',
  entry_year   TEXT DEFAULT '',
  created_at   TEXT DEFAULT '',
  summary_json TEXT DEFAULT '{}'
);

-- 本功能自带的「原件登记表」：培养方案 / 教学计划 / 政策 / 大纲 / 清单
-- （只落本库，不入公共知识库 global、不写 warning.db —— 2026-09-23 用户明确要求）
CREATE TABLE IF NOT EXISTS plan_source_doc (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  category    TEXT DEFAULT '',
  file_name   TEXT DEFAULT '',
  file_path   TEXT DEFAULT '',
  file_hash   TEXT DEFAULT '',
  size_bytes  INTEGER DEFAULT 0,
  ext         TEXT DEFAULT '',
  major       TEXT DEFAULT '',
  entry_year  TEXT DEFAULT '',
  text_chars  INTEGER DEFAULT 0,
  text_content TEXT DEFAULT '',
  ingested_at TEXT DEFAULT '',
  note        TEXT DEFAULT '',
  -- 适用年级：JSON 数组；["全部"] 表示所有年级通用（政策/大纲类默认）
  applies_to  TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_srcdoc_cat ON plan_source_doc(category);
CREATE INDEX IF NOT EXISTS idx_srcdoc_path ON plan_source_doc(file_path);

-- 学生身份绑定（openid ↔ 学号）——设计 §6.7
-- 本人绑定：学号 + 姓名 经 warning.db.roster 校验后写入；**只存绑定关系，不存成绩**。
CREATE TABLE IF NOT EXISTS plan_student_binding (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  platform    TEXT NOT NULL DEFAULT 'miniapp',
  user_id     TEXT NOT NULL,
  student_id  TEXT NOT NULL,
  name        TEXT DEFAULT '',
  grade       TEXT DEFAULT '',
  major       TEXT DEFAULT '',
  status      TEXT DEFAULT 'active',
  verified_at TEXT DEFAULT '',
  created_at  TEXT DEFAULT '',
  UNIQUE (platform, user_id)
);
CREATE INDEX IF NOT EXISTS idx_binding_sid ON plan_student_binding(student_id);

-- 培养模式规则（四路径，设计 005 §4）：科学研究型/交叉融合型/创新创业型 + 通用
CREATE TABLE IF NOT EXISTS plan_mode_rule (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id   INTEGER NOT NULL,
  mode      TEXT DEFAULT '',
  rule_type TEXT DEFAULT '',
  item      TEXT DEFAULT '',
  value     TEXT DEFAULT '',
  unit      TEXT DEFAULT '',
  detail    TEXT DEFAULT '',
  sort      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mode_rule_plan ON plan_mode_rule(plan_id, mode);

-- 培养模式专属课程清单（如科学研究型-研究生进阶课程）
CREATE TABLE IF NOT EXISTS plan_mode_course (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id     INTEGER NOT NULL,
  mode        TEXT DEFAULT '',
  "group"     TEXT DEFAULT '',
  course_code TEXT DEFAULT '',
  course_name TEXT DEFAULT '',
  credit      REAL DEFAULT 0,
  provider    TEXT DEFAULT '',
  note        TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mode_course_plan ON plan_mode_course(plan_id, mode);

-- 交叉融合型-跨选专业范围
CREATE TABLE IF NOT EXISTS plan_mode_scope (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id       INTEGER NOT NULL,
  mode          TEXT DEFAULT '',
  allowed_major TEXT DEFAULT '',
  note          TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mode_scope_plan ON plan_mode_scope(plan_id);

-- 转专业接收计划（政策年 × 专业 × 年级 × 学院内/跨学院）
CREATE TABLE IF NOT EXISTS plan_transfer_plan (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_year  TEXT DEFAULT '',
  major        TEXT DEFAULT '',
  entry_year   TEXT DEFAULT '',
  scope        TEXT DEFAULT '',
  quota        INTEGER DEFAULT 0,
  source_doc_id INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_transfer_plan ON plan_transfer_plan(policy_year, major, entry_year);

-- 转专业规则（志愿/时间/考核/录取/补修/降级/公示…）
CREATE TABLE IF NOT EXISTS plan_transfer_rule (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_year  TEXT DEFAULT '',
  rule_type    TEXT DEFAULT '',
  item         TEXT DEFAULT '',
  value        TEXT DEFAULT '',
  detail       TEXT DEFAULT '',
  sort         INTEGER DEFAULT 0,
  source_doc_id INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_transfer_rule ON plan_transfer_rule(policy_year, rule_type);

-- 专业选择接收计划（年级 × 专业 × 计划数）
CREATE TABLE IF NOT EXISTS plan_major_selection_plan (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_year   TEXT DEFAULT '',
  major        TEXT DEFAULT '',
  quota        INTEGER DEFAULT 0,
  source_doc_id INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ms_plan ON plan_major_selection_plan(entry_year, major);

-- 专业选择规则（志愿数/录取原则/综合成绩构成/学业成绩口径/时间安排…）
CREATE TABLE IF NOT EXISTS plan_major_selection_rule (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_year   TEXT DEFAULT '',
  rule_type    TEXT DEFAULT '',
  item         TEXT DEFAULT '',
  value        TEXT DEFAULT '',
  detail       TEXT DEFAULT '',
  sort         INTEGER DEFAULT 0,
  source_doc_id INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ms_rule ON plan_major_selection_rule(entry_year, rule_type);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_applies_to(row: sqlite3.Row) -> list[str]:
    """读取 applies_to（列可能不存在于旧库 → 回退到 [entry_year]）。"""
    try:
        raw = row["applies_to"]
    except (IndexError, KeyError):
        raw = ""
    try:
        vals = json.loads(raw or "[]")
        if isinstance(vals, list) and vals:
            return [str(v) for v in vals]
    except Exception:
        pass
    entry_year = ""
    with contextlib.suppress(IndexError, KeyError):
        entry_year = row["entry_year"] or ""
    return [entry_year] if entry_year else [ALL_GRADES]


def _applies_to_grade(applies_to: list[str], grade: str) -> bool:
    return ALL_GRADES in applies_to or grade in applies_to


class TrainingPlanDBCore:
    """连接创建 + 建表 + 旧库列迁移（各 DAO mixin 的公共基座）。"""

    conn: sqlite3.Connection

    def __init__(self, db_path: str = "data/training_plan.db"):
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(_DDL)
        # 兼容旧库：补 v2 新增列（applies_to）
        self._ensure_column("plan_document", "applies_to", "TEXT DEFAULT '[]'")
        self._ensure_column("plan_source_doc", "applies_to", "TEXT DEFAULT '[]'")
        self._backfill_applies_to()
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.conn.commit()

    def _backfill_applies_to(self) -> None:
        """把空的 applies_to 回填为合理默认（幂等）：
        培养方案 → [主年级]；资料类 → ["全部"]。"""
        bad = ("", "[]", "null", "None")
        rows = self.conn.execute(
            "SELECT id, entry_year, applies_to FROM plan_document"
        ).fetchall()
        for r in rows:
            ap = r["applies_to"]
            if ap in bad or ap is None:
                want = [r["entry_year"]] if r["entry_year"] else [ALL_GRADES]
                self.conn.execute("UPDATE plan_document SET applies_to=? WHERE id=?",
                                  (json.dumps(want, ensure_ascii=False), r["id"]))
        rows = self.conn.execute(
            "SELECT id, category, entry_year, applies_to FROM plan_source_doc"
        ).fetchall()
        for r in rows:
            ap = r["applies_to"]
            if ap in bad or ap is None:
                want = ([r["entry_year"]] if (r["category"] == "培养方案" and r["entry_year"])
                        else [ALL_GRADES])
                self.conn.execute("UPDATE plan_source_doc SET applies_to=? WHERE id=?",
                                  (json.dumps(want, ensure_ascii=False), r["id"]))

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        try:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            return
        if column not in cols:
            with contextlib.suppress(sqlite3.OperationalError):
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()
