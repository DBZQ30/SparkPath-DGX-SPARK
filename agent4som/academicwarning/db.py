"""SQLite 存储层（data/warning.db，WAL）。仿 knowledge_base/core/audit_logger.py 的
连接模式：check_same_thread=False + PRAGMA journal_mode=WAL。"""
import contextlib
import json
import sqlite3
from datetime import datetime

from .models import (
    SourceFile, PlanCourse, PlanSemesterCourse, Student,
    Selection, Grade, SelectionCheckRow,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS waiver (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grade TEXT NOT NULL,
    student_id TEXT NOT NULL,
    kind TEXT NOT NULL,            -- course / credit
    course_code TEXT DEFAULT '',   -- kind=course：方案课 code
    course_name TEXT DEFAULT '',   -- A：方案课名；B：旧课名
    grade_source TEXT DEFAULT '',  -- A 平替源课名（免修空）
    category TEXT DEFAULT '',      -- B：归入类别
    credit REAL DEFAULT 0,         -- B：认可学分
    note TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_waiver_credit_dup
    ON waiver(grade, student_id, course_name) WHERE kind='credit';
CREATE UNIQUE INDEX IF NOT EXISTS idx_waiver_course_dup
    ON waiver(grade, student_id, course_code) WHERE kind='course' AND course_code != '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_waiver_grade_dup
    ON waiver(grade, student_id, course_name) WHERE kind='grade' AND course_name != '';
CREATE TABLE IF NOT EXISTS grade_master (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS source_file (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_type TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    file_path TEXT NOT NULL,
    upload_time TEXT NOT NULL,
    uploader TEXT NOT NULL,
    parsed_status TEXT DEFAULT 'done',
    in_file_meta TEXT DEFAULT '{}',
    grade TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS training_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    major_name TEXT NOT NULL,
    version_label TEXT DEFAULT '',
    file_name TEXT DEFAULT '',
    upload_time TEXT DEFAULT '',
    is_active INTEGER DEFAULT 0,
    entry_year TEXT DEFAULT '',
    created_at TEXT DEFAULT '',
    elective_req REAL DEFAULT 0,
    grade TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS plan_course (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    course_code TEXT NOT NULL,
    course_name TEXT NOT NULL,
    credit REAL NOT NULL,
    course_type TEXT NOT NULL,
    required_flag TEXT NOT NULL,
    semester TEXT NOT NULL,
    provider TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS plan_semester_course (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    semester TEXT NOT NULL,
    course_code TEXT NOT NULL,
    course_name TEXT NOT NULL,
    credit REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS selection (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    name TEXT DEFAULT '',
    major TEXT DEFAULT '',
    semester_label TEXT NOT NULL,
    semester_code TEXT NOT NULL,
    course_code TEXT NOT NULL,
    course_name TEXT NOT NULL,
    credit REAL DEFAULT 0,
    nature TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT DEFAULT '选中',
    retake TEXT DEFAULT '初修',
    source_file_id INTEGER NOT NULL,
    grade TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS roster (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    name TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    major TEXT DEFAULT '',
    class_name TEXT DEFAULT '',
    status TEXT DEFAULT '',
    source_file_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS grade (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    student_name TEXT DEFAULT '',
    term_label TEXT DEFAULT '',
    course_name TEXT NOT NULL,
    course_name_clean TEXT DEFAULT '',
    credit REAL DEFAULT 0,
    grade_raw TEXT NOT NULL,
    pass_flag INTEGER DEFAULT 1,
    marker TEXT DEFAULT '',
    source_file_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS selection_check (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id INTEGER NOT NULL,
    student_id TEXT NOT NULL,
    name TEXT DEFAULT '',
    major TEXT DEFAULT '',
    class_name TEXT DEFAULT '',
    category TEXT DEFAULT '',
    expected_credit REAL DEFAULT 0,
    gained_credit REAL DEFAULT 0,
    selected_credit REAL DEFAULT 0,
    gap REAL DEFAULT 0,
    message TEXT DEFAULT '',
    checked_at TEXT NOT NULL,
    grade TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_selection_check_file ON selection_check(source_file_id);
"""


class WarningDB:
    def __init__(self, db_path: str = "data/warning.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)  # M3：跨进程写库等待窗口

    def init_schema(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        # schema 迁移：grade 补 student_name（v2，2026-08-27）；selection 补 name/major
        # （v3，2026-08-31 精简：名单改从选课结果提取，需要姓名/专业）
        # v6（2026-09-01）：四表补 grade 列（年级分区）
        # v7（2026-09-04）：selection_check 补 details（结构化触发明细 JSON）
        for stmt in ("ALTER TABLE grade ADD COLUMN student_name TEXT DEFAULT ''",
                     "ALTER TABLE selection ADD COLUMN name TEXT DEFAULT ''",
                     "ALTER TABLE selection ADD COLUMN major TEXT DEFAULT ''",
                     "ALTER TABLE selection_check ADD COLUMN category TEXT DEFAULT ''",
                     "ALTER TABLE training_plan ADD COLUMN elective_req REAL DEFAULT 0",
                     "ALTER TABLE source_file ADD COLUMN grade TEXT DEFAULT ''",
                     "ALTER TABLE training_plan ADD COLUMN grade TEXT DEFAULT ''",
                     "ALTER TABLE selection ADD COLUMN grade TEXT DEFAULT ''",
                     "ALTER TABLE selection_check ADD COLUMN grade TEXT DEFAULT ''",
                     # v6.3（2026-09-02）：年级当前学期配置 + 人工确认覆盖学期
                     "ALTER TABLE grade_master ADD COLUMN current_semester TEXT DEFAULT ''",
                     "ALTER TABLE grade_master ADD COLUMN confirm_sem TEXT DEFAULT ''",
                     "ALTER TABLE selection_check ADD COLUMN details TEXT DEFAULT '{}'"):
            with contextlib.suppress(sqlite3.OperationalError):  # 列已存在
                self.conn.execute(stmt)
        self.conn.execute("PRAGMA user_version = 7")
        self.conn.commit()

    # ---- 文件档案 ----
    def insert_source_file(self, sf: SourceFile) -> int:
        cur = self.conn.execute(
            "INSERT INTO source_file (file_type, file_name, file_hash, file_path,"
            " upload_time, uploader, parsed_status, in_file_meta, grade)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (sf.file_type, sf.file_name, sf.file_hash, sf.file_path,
             sf.upload_time, sf.uploader, sf.parsed_status,
             __import__("json").dumps(sf.in_file_meta, ensure_ascii=False),
             sf.grade),
        )
        self.conn.commit()
        return cur.lastrowid

    def insert_queued_source_file(self, sf: SourceFile) -> int:
        """上传即占位：落一条 parsed_status='queued' 记录（锁外短写）。

        目的：文件已落盘但解析尚未完成（成绩单 OCR 可达数分钟）期间，/status
        即可见该文件（显示"解析中"），而非"未上传"。解析完成后由
        finish_source_file 原地 update 为 done/failed——占位行不新增第二条。

        与 insert_source_file 的唯一差别：parsed_status 强制 'queued'，
        且 file_hash 允许为空（判重所依据的 md5 在落盘时已可算，此处不强制）。"""
        cur = self.conn.execute(
            "INSERT INTO source_file (file_type, file_name, file_hash, file_path,"
            " upload_time, uploader, parsed_status, in_file_meta, grade)"
            " VALUES (?,?,?,?,?,?,'queued',?,?)",
            (sf.file_type, sf.file_name, sf.file_hash, sf.file_path,
             sf.upload_time, sf.uploader,
             __import__("json").dumps(sf.in_file_meta, ensure_ascii=False),
             sf.grade),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_source_file(self, sf_id: int, parsed_status: str,
                           in_file_meta: dict | None = None,
                           file_hash: str | None = None) -> None:
        """占位行收尾（解析完成/失败）：原地 update，不新增记录。

        in_file_meta 为解析阶段确定的元数据（major/note/error 等）；
        file_hash 在解析阶段算得后回填（判重一致性）。"""
        sets = ["parsed_status=?"]
        params: list = [parsed_status]
        if in_file_meta is not None:
            sets.append("in_file_meta=?")
            params.append(__import__("json").dumps(in_file_meta, ensure_ascii=False))
        if file_hash is not None:
            sets.append("file_hash=?")
            params.append(file_hash)
        params.append(sf_id)
        self.conn.execute(
            "UPDATE source_file SET " + ", ".join(sets) + " WHERE id=?", params)
        self.conn.commit()

    def fail_stale_queued(self) -> int:
        """启动清理：把遗留的 queued 占位行标记为 failed（解析进程被中断）。

        单 worker 部署（uvicorn --workers 1）：服务重启即代表此前解析已中断，
        启动时调用安全。返回清理行数。"""
        cur = self.conn.execute(
            "UPDATE source_file SET parsed_status='failed',"
            " in_file_meta=json_set(COALESCE(NULLIF(in_file_meta,''),'{}'),"
            " '$.error','解析中断（服务重启），请重新上传')"
            " WHERE parsed_status='queued'")
        self.conn.commit()
        return cur.rowcount

    def list_grades(self) -> list[str]:
        """年级列表（grade_master，按名称排序）。"""
        return [r[0] for r in self.conn.execute(
            "SELECT name FROM grade_master ORDER BY name")]

    def add_grade(self, name: str) -> None:
        """添加年级（重复 INSERT OR IGNORE）。"""
        self.conn.execute(
            "INSERT OR IGNORE INTO grade_master (name, created_at) VALUES (?, ?)",
            (name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        self.conn.commit()

    def delete_grade(self, name: str) -> bool:
        """从年级注册表移除（毕业年级滚出）。

        只删注册表条目，**不动历史分区数据**（成绩/选课等仍保留，可追溯）。
        不存在返回 False。
        """
        cur = self.conn.execute("DELETE FROM grade_master WHERE name=?", (name,))
        self.conn.commit()
        return cur.rowcount == 1

    # ---- v6.3：年级学期配置（当前学期 / 人工确认覆盖）----
    def get_grade_semesters(self, name: str) -> tuple[str, str]:
        """(current_semester, confirm_sem)；无该年级 → ("", "")。"""
        row = self.conn.execute(
            "SELECT current_semester, confirm_sem FROM grade_master WHERE name=?",
            (name,)).fetchone()
        return (row[0] or "", row[1] or "") if row else ("", "")

    def set_grade_semester(self, name: str, current_semester: str) -> None:
        self.conn.execute("UPDATE grade_master SET current_semester=? WHERE name=?",
                          (current_semester, name))
        self.conn.commit()

    def set_grade_confirm_sem(self, name: str, sem: str) -> None:
        self.conn.execute("UPDATE grade_master SET confirm_sem=? WHERE name=?",
                          (sem, name))
        self.conn.commit()

    def latest_source_file(self, file_type: str, grade: str = "") -> SourceFile | None:
        """最新文件（v6 按年级过滤；grade='' 兼容旧调用不带条件）。

        不按 parsed_status 过滤：failed 文件要返回给上层（service 检查状态给
        友好提示，test_service_upload 断言 failed 可被 latest 取到）。"""
        where = " AND grade=?" if grade else ""
        params = (file_type, grade) if grade else (file_type,)
        row = self.conn.execute(
            "SELECT id, file_type, file_name, file_hash, file_path, upload_time,"
            " uploader, parsed_status, in_file_meta, grade FROM source_file"
            f" WHERE file_type=?{where} ORDER BY id DESC LIMIT 1", params).fetchone()
        return self._sf_from_row(row) if row else None

    def latest_source_files_by_major(self, file_type: str, grade: str = "") -> dict[str, SourceFile]:
        """按专业取最新文件（v6 按年级过滤；grade='' 兼容旧调用）。

        不按 parsed_status 过滤：与 latest_source_file 一致，failed 文件保留
        （/status 显示"解析失败"而非无记录占位；service 侧自行过滤 done）。"""
        where = " AND grade=?" if grade else ""
        params = (file_type, grade) if grade else (file_type,)
        rows = self.conn.execute(
            "SELECT id, file_type, file_name, file_hash, file_path, upload_time,"
            " uploader, parsed_status, in_file_meta, grade FROM source_file"
            f" WHERE file_type=?{where} ORDER BY id", params).fetchall()
        out: dict[str, SourceFile] = {}
        for r in rows:
            sf = self._sf_from_row(r)
            major = (sf.in_file_meta or {}).get("major", "")
            if major:
                out[major] = sf
        return out

    def file_hash_exists(self, file_hash: str, file_type: str,
                         grade: str = "", major: str | None = None) -> SourceFile | None:
        """M2 同内容判重：匹配成功文件（I1：failed 记录不参与判重，允许重传）。
        v6 年级分区：grade 非空时仅匹配该年级分区（空 = 不限年级，兼容旧调用）。
        v1.9 major：非 None 时追加专业维度——否则「错误专业注册」的记录会把
        之后正确专业的上传挡在判重外（表现为前端立刻显示成功、成绩单分区仍
        显示未上传）。"""
        conds = ["file_hash=?", "file_type=?", "parsed_status='done'"]
        params: list = [file_hash, file_type]
        if grade:
            conds.append("grade=?")
            params.append(grade)
        if major is not None:
            conds.append("json_extract(in_file_meta, '$.major')=?")
            params.append(major)
        row = self.conn.execute(
            "SELECT * FROM source_file WHERE " + " AND ".join(conds)
            + " ORDER BY id DESC LIMIT 1",
            params,
        ).fetchone()
        return self._sf_from_row(row) if row else None

    def _cascade_delete_children(self, ids: list) -> None:
        """v1.9.1 级联删全部按 source_file_id 关联的子表，杜绝孤儿残留：
        roster（名单）/ grade（成绩）/ selection（选课）/ selection_check（检查结果）。
        此前只删 roster/plan → 删成绩单后 grade 表留下大量孤儿行（实测 32689 行）。"""
        for tbl in ("roster", "grade", "selection", "selection_check"):
            self.conn.executemany(
                "DELETE FROM " + tbl + " WHERE source_file_id=?",
                [(i,) for i in ids])

    def _cascade_delete_plans(self, rows, major: str | None,
                              grade: str | None) -> None:
        """plan 级联：按 major_name 精确匹配删 training_plan（含 is_active=0
        历史版，其他专业不受影响）+ plan_course/plan_semester_course；
        v6 grade 非空时仅限该年级分区。专业集合取指定 major，未指定时从
        被删 source_file 的 in_file_meta 逐行提取。"""
        majors = {major} if major else {json.loads(r[8] or "{}").get("major", "")
                                        for r in rows}
        for m in majors:
            if not m:
                continue
            if grade is not None:
                pids = [p[0] for p in self.conn.execute(
                    "SELECT id FROM training_plan WHERE major_name=? AND grade=?",
                    (m, grade)).fetchall()]
            else:
                pids = [p[0] for p in self.conn.execute(
                    "SELECT id FROM training_plan WHERE major_name=?", (m,)).fetchall()]
            if pids:
                self.conn.executemany(
                    "DELETE FROM plan_course WHERE plan_id=?", [(p,) for p in pids])
                self.conn.executemany(
                    "DELETE FROM plan_semester_course WHERE plan_id=?",
                    [(p,) for p in pids])
                self.conn.executemany(
                    "DELETE FROM training_plan WHERE id=?", [(p,) for p in pids])

    def delete_major_files(self, file_type: str, major: str | None = None,
                           grade: str | None = None) -> list[SourceFile]:
        """v1.7 按专业物理删除（事务）：plan/grade 删除该专业全部 source_file
        记录（含历史版本）；major 为空 = 该类型全部。plan 级联删 training_plan
        （_cascade_delete_plans，按 major_name 精确匹配，含 is_active=0 历史版，
        其他专业不受影响）。v6 年级分区：grade 非空时仅删该年级分区
        （source_file 加 AND grade=?，plan 级联同步限该年级）；grade 为空 =
        全删（兼容旧调用）。

        v1.9.1 级联删全部按 source_file_id 关联的子表（_cascade_delete_children，
        roster/grade/selection/selection_check），彻底杜绝孤儿行残留。plan 无
        source_file_id，仍按 major_name 级联 training_plan +
        plan_course/plan_semester_course。
        返回被删记录（按 id）。"""
        conds, params = ["file_type=?"], [file_type]
        if major is not None:
            conds.append("json_extract(in_file_meta, '$.major')=?")
            params.append(major)
        if grade is not None:
            conds.append("grade=?")
            params.append(grade)
        rows = self.conn.execute(
            "SELECT * FROM source_file WHERE " + " AND ".join(conds)
            + " ORDER BY id", params).fetchall()
        if not rows:
            return []
        ids = [r[0] for r in rows]
        self.conn.executemany("DELETE FROM source_file WHERE id=?", [(i,) for i in ids])
        self._cascade_delete_children(ids)
        if file_type == "plan":
            self._cascade_delete_plans(rows, major, grade)
        self.conn.commit()
        return [self._sf_from_row(r) for r in rows]

    @staticmethod
    def _sf_from_row(row) -> SourceFile:
        import json
        return SourceFile(
            id=row[0], file_type=row[1], file_name=row[2], file_hash=row[3],
            file_path=row[4], upload_time=row[5], uploader=row[6],
            parsed_status=row[7], in_file_meta=json.loads(row[8] or "{}"),
            grade=row[9],
        )

    # ---- 培养方案版本管理 ----
    def insert_plan_meta(self, major: str, version_label: str, file_name: str,
                         upload_time: str, *, grade: str = "",
                         elective_req: float = 0.0) -> int:
        """同专业同年级旧版 is_active 置 0，新版激活（v6 按 grade 分区）。

        elective_req（2026-08-31）：Table 0 专业选修毕业要求学分（工商 8/工业 12/
        大数据 10/会计 15）——应修口径从 Table 1 逐课程改为毕业要求。"""
        self.conn.execute(
            "UPDATE training_plan SET is_active=0 WHERE major_name=? AND grade=?",
            (major, grade))
        cur = self.conn.execute(
            "INSERT INTO training_plan (major_name, version_label, file_name,"
            " upload_time, is_active, grade, created_at, elective_req)"
            " VALUES (?,?,?,?,1,?,?,?)",
            (major, version_label, file_name, upload_time, grade,
             upload_time, elective_req))
        self.conn.commit()
        return cur.lastrowid

    def latest_active_plans(self, grade: str = "") -> dict[str, int]:
        """当前生效方案（v6 按年级；grade='' 兼容旧调用）。"""
        where = " AND grade=?" if grade else ""
        params = (grade,) if grade else ()
        rows = self.conn.execute(
            "SELECT id, major_name FROM training_plan WHERE is_active=1" + where
            + " ORDER BY id", params).fetchall()
        return {r[1]: r[0] for r in rows}

    def insert_plan_courses(self, plan_id: int, courses: list[PlanCourse]) -> None:
        self.conn.executemany(
            "INSERT INTO plan_course (plan_id, course_code, course_name, credit,"
            " course_type, required_flag, semester, provider) VALUES (?,?,?,?,?,?,?,?)",
            [(plan_id, c.course_code, c.course_name, c.credit, c.course_type,
              c.required_flag, c.semester, c.provider) for c in courses],
        )
        self.conn.commit()

    def insert_plan_semester_courses(self, plan_id: int, courses: list[PlanSemesterCourse]) -> None:
        self.conn.executemany(
            "INSERT INTO plan_semester_course (plan_id, semester, course_code,"
            " course_name, credit) VALUES (?,?,?,?,?)",
            [(plan_id, c.semester, c.course_code, c.course_name, c.credit) for c in courses],
        )
        self.conn.commit()

    # ---- 学生/选课/成绩/学籍异动 ----
    def insert_selections(self, rows: list[Selection]) -> None:
        self.conn.executemany(
            "INSERT INTO selection (student_id, name, major, grade, semester_label,"
            " semester_code, course_code, course_name, credit, nature, category,"
            " status, retake, source_file_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r.student_id, r.name, r.major, r.grade, r.semester_label,
              r.semester_code, r.course_code, r.course_name, r.credit, r.nature,
              r.category, r.status, r.retake, r.source_file_id) for r in rows],
        )
        self.conn.commit()
    # ---- 学籍名单（v6.5，2026-09-04：提醒名单权威基准）----
    def _insert_roster_rows(self, rows: list[dict]) -> None:
        self.conn.executemany(
            "INSERT INTO roster (student_id, name, grade, major, class_name,"
            " status, source_file_id) VALUES (?,?,?,?,?,?,?)",
            [(r["student_id"], r.get("name", ""), r.get("grade", ""),
              r.get("major", ""), r.get("class_name", ""), r.get("status", ""),
              r["source_file_id"]) for r in rows],
        )

    def insert_roster(self, rows: list[dict]) -> None:
        """学籍名单行落库（rows = parse_roster 行 dict，已带 source_file_id）。"""
        self._insert_roster_rows(rows)
        self.conn.commit()

    def replace_roster(self, grade: str, rows: list[dict]) -> None:
        """同年级 roster 全量替换（M-1 单事务：先删旧行再插新——
        要么全成功要么全不生效，防坏文件清空旧名单的中间态）。"""
        try:
            self.conn.execute("DELETE FROM roster WHERE grade=?", (grade,))
            self._insert_roster_rows(rows)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def delete_roster(self, grade: str) -> None:
        """删除某年级全部 roster 行（同年级重传 = 名单版本更新：先删旧再插新）。"""
        self.conn.execute("DELETE FROM roster WHERE grade=?", (grade,))
        self.conn.commit()

    def latest_roster(self, grade: str = "") -> list[Student]:
        """该年级最新学籍名单（在籍在校过滤已由 parse_roster 完成）→ Student 列表。

        名单绑定最新 roster 文件（parsed_status='done'）取数——重传解析失败的
        文件不遮蔽上一版有效名单；无 roster 文件 → []（调用方降级提示）。
        grade='' 兼容旧调用不带条件。"""
        where = " AND grade=?" if grade else ""
        params = (grade,) if grade else ()
        row = self.conn.execute(
            "SELECT id FROM source_file WHERE file_type='roster'"
            " AND parsed_status='done'" + where + " ORDER BY id DESC LIMIT 1",
            params).fetchone()
        if row is None:
            return []
        rows = self.conn.execute(
            "SELECT student_id, name, grade, major, class_name FROM roster"
            " WHERE source_file_id=? ORDER BY id", (row[0],)).fetchall()
        return [Student(student_id=r[0], name=r[1] or "", grade=r[2] or "",
                        major=r[3] or "", class_name=r[4] or "",
                        enrolled_status="在籍") for r in rows]

    def insert_grades(self, rows: list[Grade]) -> None:
        self.conn.executemany(
            "INSERT INTO grade (student_id, student_name, term_label, course_name, course_name_clean,"
            " credit, grade_raw, pass_flag, marker, source_file_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(r.student_id, r.student_name, r.term_label, r.course_name, r.course_name_clean,
              r.credit, r.grade_raw, r.pass_flag, r.marker, r.source_file_id) for r in rows],
        )
        self.conn.commit()

    def refresh_selection_credits(self, file_id: int,
                                  credit_by_code: dict[str, float]) -> None:
        """I8：按最新培养方案刷新选课学分（方案后传导致学分 0.0 时补链）。
        仅刷新指定文件（最新选课文件）的行。"""
        if not credit_by_code:
            return
        self.conn.executemany(
            "UPDATE selection SET credit=? WHERE course_code=? AND source_file_id=?",
            [(credit, code, file_id) for code, credit in credit_by_code.items()],
        )
        self.conn.commit()

    def refresh_gen_ed_credits(self, file_id: int,
                               id_credit: list[tuple[int, float]]) -> None:
        """Task 5：通识选课学分补全落库（按 selection.id 精确更新指定文件行）。"""
        self.conn.executemany("UPDATE selection SET credit=? WHERE id=? AND source_file_id=?",
                              [(c, i, file_id) for i, c in id_credit])
        self.conn.commit()

    # ---- 读取 ----
    def get_selections(self, file_id: int) -> list[Selection]:
        # 显式列名：v3 ALTER 补 name/major 列后 SELECT * 按位置映射会错位（同 grade）
        rows = self.conn.execute(
            "SELECT id, student_id, name, major, grade, semester_label, semester_code,"
            " course_code, course_name, credit, nature, category, status, retake,"
            " source_file_id FROM selection WHERE source_file_id=?", (file_id,)).fetchall()
        return [Selection(id=r[0], student_id=r[1], name=r[2], major=r[3], grade=r[4],
                          semester_label=r[5], semester_code=r[6],
                          course_code=r[7], course_name=r[8], credit=r[9], nature=r[10],
                          category=r[11], status=r[12], retake=r[13], source_file_id=r[14])
                for r in rows]

    def get_grades(self, file_id: int) -> list[Grade]:
        # 显式列名：旧库 ALTER 后 student_name 位于末尾，SELECT * 按位置映射会错位
        rows = self.conn.execute(
            "SELECT student_id, student_name, term_label, course_name, course_name_clean,"
            " credit, grade_raw, pass_flag, marker, source_file_id"
            " FROM grade WHERE source_file_id=?", (file_id,)).fetchall()
        return [Grade(student_id=r[0], student_name=r[1], term_label=r[2], course_name=r[3],
                      course_name_clean=r[4], credit=r[5], grade_raw=r[6],
                      pass_flag=r[7], marker=r[8], source_file_id=r[9]) for r in rows]

    def get_plan_courses(self, plan_id: int) -> list[PlanCourse]:
        rows = self.conn.execute(
            "SELECT * FROM plan_course WHERE plan_id=?", (plan_id,)).fetchall()
        return [PlanCourse(plan_id=r[1], course_code=r[2], course_name=r[3], credit=r[4],
                           course_type=r[5], required_flag=r[6], semester=r[7], provider=r[8])
                for r in rows]

    def get_plan_semester_courses(self, plan_id: int) -> list[PlanSemesterCourse]:
        rows = self.conn.execute(
            "SELECT * FROM plan_semester_course WHERE plan_id=?", (plan_id,)).fetchall()
        return [PlanSemesterCourse(plan_id=r[1], semester=r[2], course_code=r[3],
                                   course_name=r[4], credit=r[5]) for r in rows]

    # ---- 运行与结果 ----
    def insert_selection_check_rows(self, rows: list[SelectionCheckRow],
                                    grade: str = "") -> None:
        """落库检查结果（只存触发学生；v6 落 grade 分区）。"""
        self.conn.executemany(
            "INSERT INTO selection_check (source_file_id, student_id, name, major,"
            " class_name, category, expected_credit, gained_credit, selected_credit,"
            " gap, message, checked_at, grade, details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r.source_file_id, r.student_id, r.name, r.major, r.class_name,
              r.category, r.expected_credit, r.gained_credit, r.selected_credit,
              r.gap, r.message, r.checked_at, grade,
              r.details) for r in rows],   # v7：details 结构化明细 JSON（模型默认 '{}'）
        )
        self.conn.commit()

    def latest_selection_check(self, grade: str = "") -> tuple[int | None, str, list[SelectionCheckRow]]:
        """最新选课文件（v6 按年级；parsed_status='done'）对应的检查结果。

        返回 (source_file_id, checked_at, rows)；无选课文件或无检查 → (None, "", [])。
        取数按最新 source_file_id 过滤——规避历史重复 selection 数据（5/51 双份）；
        grade='' 兼容旧调用不带条件。"""
        where = " AND grade=?" if grade else ""
        params = (grade,) if grade else ()
        row = self.conn.execute(
            "SELECT id FROM source_file WHERE file_type='selection'"
            " AND parsed_status='done'" + where + " ORDER BY id DESC LIMIT 1",
            params).fetchone()
        if row is None:
            return None, "", []
        sf_id = row[0]
        # 只取最新一批（同 checked_at 的批次；多次 run 会落库多批同文件记录）
        checked_at = self.conn.execute(
            "SELECT MAX(checked_at) FROM selection_check WHERE source_file_id=?",
            (sf_id,),
        ).fetchone()[0]
        if not checked_at:
            return sf_id, "", []
        rows = self.conn.execute(
            # 显式列名：ALTER 加 category/details 列后旧库列序与新库不同，SELECT * 会错位
            "SELECT id, source_file_id, student_id, name, major, class_name,"
            " category, expected_credit, gained_credit, selected_credit, gap,"
            " message, checked_at, details FROM selection_check"
            " WHERE source_file_id=? AND checked_at=? ORDER BY id",
            (sf_id, checked_at),
        ).fetchall()
        if not rows:
            return sf_id, "", []
        out = []
        for r in rows:
            row = SelectionCheckRow(
                id=r[0], source_file_id=r[1], student_id=r[2], name=r[3],
                major=r[4], class_name=r[5], category=r[6], expected_credit=r[7],
                gained_credit=r[8], selected_credit=r[9], gap=r[10],
                message=r[11], checked_at=r[12], details=r[13])
            out.append(row)
        return sf_id, checked_at, out

    # ---- 豁免（v7，2026-09-04：类型A 课程豁免 / 类型B 旧课学分认可）----
    def list_waivers(self, grade: str = "") -> list[dict]:
        """豁免记录列表（按 id 升序）；grade 非空时只列该年级。"""
        where = "WHERE grade=?" if grade else ""
        params = (grade,) if grade else ()
        rows = self.conn.execute(
            "SELECT id, grade, student_id, kind, course_code, course_name,"
            " grade_source, category, credit, note, created_by, created_at"
            f" FROM waiver {where} ORDER BY id", params).fetchall()
        cols = ["id", "grade", "student_id", "kind", "course_code", "course_name",
                "grade_source", "category", "credit", "note", "created_by", "created_at"]
        return [dict(zip(cols, r, strict=False)) for r in rows]

    def add_waiver(self, w: dict) -> int:
        """新增豁免记录；重复 → sqlite3.IntegrityError（唯一索引：kind='credit'
        同 (grade, student_id, course_name) → idx_waiver_credit_dup；kind='course'
        同 (grade, student_id, course_code) → idx_waiver_course_dup）。"""
        cur = self.conn.execute(
            "INSERT INTO waiver (grade, student_id, kind, course_code, course_name,"
            " grade_source, category, credit, note, created_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (w["grade"], w["student_id"], w["kind"], w.get("course_code", ""),
             w.get("course_name", ""), w.get("grade_source", ""),
             w.get("category", ""), w.get("credit", 0.0), w.get("note", ""),
             w.get("created_by", ""), w.get("created_at", "")))
        self.conn.commit()
        return cur.lastrowid

    def delete_waiver(self, wid: int) -> None:
        """撤销豁免（物理删除；id 不存在静默无操作）。"""
        self.conn.execute("DELETE FROM waiver WHERE id=?", (wid,))
        self.conn.commit()

    # ---- 查询（007 小程序 API 用，2026-08-28 新增）----
    def close(self) -> None:
        self.conn.close()
