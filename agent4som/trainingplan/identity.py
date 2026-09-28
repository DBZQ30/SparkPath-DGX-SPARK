"""学生身份解析（openid → 学号）—— 设计 §6.7。

**两级解析（2026-09-24，配合 011 档案重构）**：

1. **档案自动匹配（首选）**：读 `miniapp.db.student_archive`（管理员上传的学籍档案）
   + `admission_profiles`（openid → 已绑定手机号 / 学号），按**手机号优先、学号兜底**
   匹配 → 直接得到 学号 / 姓名 / 年级 / 专业 / 班级。**学生无需输入**。
2. **手工绑定（兜底）**：档案匹配不到时，学生提供「学号 + 姓名」，
   先与 `student_archive` 校验，再与 `warning.db.roster` 校验，通过后写入
   `training_plan.db.plan_student_binding`（显式同意 + 本地缓存）。

- 两个库均**只读**（`mode=ro`），**不写、不改**学业预警 / 档案数据；
- 本模块**只解析身份**，**不读成绩**；未解析到身份 → 上游只给方案级结论。
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Any, Optional

from . import service
from .db import TrainingPlanDB
from .models import StudentBinding

# 学籍名单库（只读，兜底校验）。可用 WARNING_DB 覆盖，便于测试。
WARNING_DB_PATH = os.environ.get("WARNING_DB", "data/warning.db")
# 小程序 methods DB（只读，含 student_archive / admission_profiles）。可用 MINIAPP_DB 覆盖。
MINIAPP_DB_PATH = os.environ.get("MINIAPP_DB", "data/sqlite/miniapp.db")

DEFAULT_PLATFORM = "miniapp"

# 学籍档案中与身份解析相关的列
_ARCHIVE_COLS = (
    "student_id", "name", "gender", "grade", "enroll_grade", "enrolled",
    "school", "department", "host_department", "major", "major_direction",
    "class_name", "phone", "residence_college",
)


def _norm_name(name: str) -> str:
    """姓名归一：去掉所有空白（含全角空格）。"""
    return re.sub(r"\s+", "", str(name or ""))


def _norm_id(sid: str) -> str:
    return re.sub(r"\s+", "", str(sid or ""))


def _ro_uri(path: str) -> str:
    return f"file:{os.path.abspath(path)}?mode=ro"


def _open_ro(path: str) -> Optional[sqlite3.Connection]:
    if not os.path.isfile(path):
        return None
    try:
        conn = sqlite3.connect(_ro_uri(path), uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


# ─────────────────────────── 专业名归一 ───────────────────────────

def normalize_archive_major(raw: str) -> dict[str, Any]:
    """学籍档案的专业名归一：去前导专业代码 → 标准专业名。

    例：``0824工商管理`` → ``工商管理``；``0842会计学（ACCA）`` → ``会计学（ACCA）``；
    ``0848管理类`` → 保留 ``管理类`` 并标 ``undetermined=True``（未定专业，需专业选择）。
    """
    s = re.sub(r"\s+", "", str(raw or ""))
    s = re.sub(r"^\d+", "", s)            # 去前导专业代码（0824/0842/…）
    std = service.normalize_major(s)
    if std:
        return {"major": std, "raw": str(raw or ""), "undetermined": False}
    return {"major": s or str(raw or ""), "raw": str(raw or ""),
            "undetermined": bool(s)}


# ─────────────────────── 档案自动匹配（首选） ───────────────────────

def _archive_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = {c: (row[c] if c in row.keys() and row[c] is not None else "") for c in _ARCHIVE_COLS}
    norm = normalize_archive_major(out.get("major", ""))
    out["major_normalized"] = norm["major"]
    out["undetermined_major"] = norm["undetermined"]
    return out


def resolve_archive(platform: str, user_id: str,
                    methods_db: Optional[str] = None) -> dict[str, Any]:
    """按**手机号优先、学号兜底**从 `student_archive` 匹配学籍档案（只读）。

    返回 `{ok, archive?, match?, reason?, message?}`。
    """
    platform = (platform or DEFAULT_PLATFORM).strip() or DEFAULT_PLATFORM
    user_id = str(user_id or "").strip()
    if not user_id:
        return {"ok": False, "reason": "no_user",
                "message": "无法识别当前用户（缺少会话身份），请在小程序内重试。"}

    path = methods_db or MINIAPP_DB_PATH
    conn = _open_ro(path)
    if conn is None:
        return {"ok": False, "reason": "no_db",
                "message": "学籍档案库暂不可用，请稍后再试或联系教务。"}
    try:
        phone = sid = ""
        try:
            prow = conn.execute(
                "SELECT phone, student_staff_id, phone_verified FROM admission_profiles"
                " WHERE platform=? AND user_id=?",
                (platform, user_id),
            ).fetchone()
            if prow:
                phone = str(prow["phone"] or "").strip()
                sid = str(prow["student_staff_id"] or "").strip()
        except sqlite3.Error:
            pass

        cols = ", ".join(_ARCHIVE_COLS)
        if phone:
            row = conn.execute(
                f"SELECT {cols} FROM student_archive WHERE phone=? LIMIT 1", (phone,)
            ).fetchone()
            if row:
                return {"ok": True, "archive": _archive_dict(row), "match": "phone"}
        if sid:
            row = conn.execute(
                f"SELECT {cols} FROM student_archive WHERE student_id=? LIMIT 1", (sid,)
            ).fetchone()
            if row:
                return {"ok": True, "archive": _archive_dict(row), "match": "student_id"}
    finally:
        conn.close()
    return {"ok": False, "reason": "no_match",
            "message": "未匹配到学籍档案：请先在「我的」页绑定手机号，或用学号匹配。"}


def find_archive_by_student_id(student_id: str,
                               methods_db: Optional[str] = None) -> Optional[dict[str, Any]]:
    """按学号查学籍档案（只读）；不存在返回 None。"""
    sid = _norm_id(student_id)
    if not sid:
        return None
    conn = _open_ro(methods_db or MINIAPP_DB_PATH)
    if conn is None:
        return None
    try:
        cols = ", ".join(_ARCHIVE_COLS)
        row = conn.execute(
            f"SELECT {cols} FROM student_archive WHERE student_id=? LIMIT 1", (sid,)
        ).fetchone()
        return _archive_dict(row) if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# ─────────────────────────── 校验（手工绑定用） ───────────────────────────

def verify_student(student_id: str, name: str,
                   warning_db: Optional[str] = None,
                   methods_db: Optional[str] = None) -> dict[str, Any]:
    """校验「学号 + 姓名」：**先学籍档案（权威），再 `warning.db.roster`（兜底）**。

    返回 `{ok, student?, source?, reason?, message?}`。
    """
    sid = _norm_id(student_id)
    nm = _norm_name(name)
    if not sid or not nm:
        return {"ok": False, "reason": "missing",
                "message": "请同时提供学号与姓名。"}

    # 1) 学籍档案（管理员上传，权威）
    arch = find_archive_by_student_id(sid, methods_db=methods_db)
    if arch:
        if _norm_name(arch.get("name", "")) != nm:
            return {"ok": False, "reason": "no_match",
                    "message": "学号与姓名不一致，请核对后重试或联系教务。"}
        return {"ok": True, "source": "archive", "student": {
            "student_id": arch["student_id"],
            "name": arch.get("name", ""),
            "grade": arch.get("grade", ""),
            "major": arch.get("major_normalized", ""),
            "class_name": arch.get("class_name", ""),
        }}

    # 2) warning.db.roster 兜底
    path = warning_db or WARNING_DB_PATH
    if not os.path.isfile(path):
        return {"ok": False, "reason": "no_db",
                "message": "学籍库暂不可用，请稍后再试或联系教务。"}
    conn = _open_ro(path)
    if conn is None:
        return {"ok": False, "reason": "no_db",
                "message": "学籍库暂不可用，请稍后再试或联系教务。"}
    try:
        rows = conn.execute(
            "SELECT student_id, name, grade, major, class_name, status"
            " FROM roster WHERE student_id=?",
            (sid,),
        ).fetchall()
    except sqlite3.Error:
        return {"ok": False, "reason": "no_db",
                "message": "学籍库暂不可用，请稍后再试或联系教务。"}
    finally:
        conn.close()

    if not rows:
        return {"ok": False, "reason": "no_match",
                "message": "未匹配到学籍：学号不存在或学籍名单尚未收录，"
                           "请核对后重试或联系教务。"}
    for r in rows:
        if _norm_name(r["name"]) == nm:
            return {"ok": True, "source": "roster", "student": {
                "student_id": r["student_id"] or "",
                "name": r["name"] or "",
                "grade": r["grade"] or "",
                "major": r["major"] or "",
                "class_name": r["class_name"] or "",
            }}
    return {"ok": False, "reason": "no_match",
            "message": "学号与姓名不一致，请核对后重试或联系教务。"}


# ─────────────────────────── 绑定 / 查询 / 解绑 ───────────────────────────

def bind(platform: str, user_id: str, student_id: str, name: str,
         db_path: Optional[str] = None,
         warning_db: Optional[str] = None,
         methods_db: Optional[str] = None) -> dict[str, Any]:
    """手工绑定当前会话（openid）↔ 学号（先校验再落库）。"""
    platform = (platform or DEFAULT_PLATFORM).strip() or DEFAULT_PLATFORM
    user_id = str(user_id or "").strip()
    if not user_id:
        return {"ok": False, "reason": "no_user",
                "message": "无法识别当前用户（缺少会话身份），请在小程序内重试。"}

    v = verify_student(student_id, name, warning_db=warning_db, methods_db=methods_db)
    if not v.get("ok"):
        return v

    st = v["student"]
    db = TrainingPlanDB(db_path or service.DB_PATH)
    try:
        db.upsert_binding(platform, user_id, st["student_id"], st["name"],
                          st["grade"], st["major"])
        b = db.get_binding(platform, user_id)
    finally:
        db.close()
    return {
        "ok": True,
        "binding": _binding_dict(b),
        "message": f"已绑定：学号 {st['student_id']} {st['name']}"
                   f"（{st['grade']} · {st['major']}）。",
    }


def whoami(platform: str, user_id: str,
           db_path: Optional[str] = None,
           methods_db: Optional[str] = None) -> dict[str, Any]:
    """解析当前会话身份：**档案自动匹配优先，本地绑定兜底**。"""
    platform = (platform or DEFAULT_PLATFORM).strip() or DEFAULT_PLATFORM
    user_id = str(user_id or "").strip()
    if not user_id:
        return {"ok": False, "bound": False, "reason": "no_user",
                "message": "无法识别当前用户（缺少会话身份），请在小程序内重试。"}

    # 1) 档案自动匹配
    a = resolve_archive(platform, user_id, methods_db=methods_db)
    if a.get("ok"):
        arch = a["archive"]
        student = {
            "student_id": arch["student_id"], "name": arch.get("name", ""),
            "grade": arch.get("grade", ""), "major": arch.get("major_normalized", ""),
            "major_raw": arch.get("major", ""), "class_name": arch.get("class_name", ""),
            "enroll_grade": arch.get("enroll_grade", ""),
            "enrolled": arch.get("enrolled", ""),
            "undetermined_major": arch.get("undetermined_major", False),
        }
        return {"ok": True, "bound": True, "source": "archive", "match": a.get("match"),
                "student": student,
                "message": f"当前身份：{student['name']} 学号 {student['student_id']}"
                           f"（{student['grade']} · {student['major']}）。"}

    # 2) 本地绑定兜底
    db = TrainingPlanDB(db_path or service.DB_PATH)
    try:
        b = db.get_binding(platform, user_id)
    finally:
        db.close()
    if b:
        return {"ok": True, "bound": True, "source": "binding",
                "binding": _binding_dict(b),
                "student": {"student_id": b.student_id, "name": b.name, "grade": b.grade,
                            "major": b.major, "class_name": "", "major_raw": "",
                            "undetermined_major": False},
                "message": f"当前身份：已绑定 学号 {b.student_id} {b.name}"
                           f"（{b.grade} · {b.major}）。"}

    return {"ok": False, "bound": False, "reason": "unbound",
            "message": "当前身份：未匹配到学籍。请在「我的」页绑定手机号或用学号匹配"
                       "（也可提供学号 + 姓名绑定）。"}


def unbind(platform: str, user_id: str,
           db_path: Optional[str] = None) -> dict[str, Any]:
    """解绑当前会话的**本地绑定**（不影响小程序档案匹配）。"""
    platform = (platform or DEFAULT_PLATFORM).strip() or DEFAULT_PLATFORM
    user_id = str(user_id or "").strip()
    if not user_id:
        return {"ok": False, "reason": "no_user",
                "message": "无法识别当前用户（缺少会话身份），请在小程序内重试。"}
    db = TrainingPlanDB(db_path or service.DB_PATH)
    try:
        b = db.get_binding(platform, user_id)
        if not b:
            return {"ok": False, "reason": "unbound", "message": "当前身份未绑定学号，无需解绑。"}
        db.delete_binding(platform, user_id)
    finally:
        db.close()
    return {"ok": True, "message": f"已解绑学号 {b.student_id}。"}


def resolve_student_id(platform: str, user_id: str,
                       db_path: Optional[str] = None,
                       methods_db: Optional[str] = None) -> str:
    """取当前会话学号（档案匹配优先，本地绑定兜底；都无 → ""）。"""
    r = whoami(platform, user_id, db_path=db_path, methods_db=methods_db)
    if r.get("bound"):
        return str((r.get("student") or {}).get("student_id", ""))
    return ""


def _binding_dict(b: Optional[StudentBinding]) -> Optional[dict[str, Any]]:
    if b is None:
        return None
    return {
        "platform": b.platform,
        "user_id": b.user_id,
        "student_id": b.student_id,
        "name": b.name,
        "grade": b.grade,
        "major": b.major,
        "status": b.status,
        "verified_at": b.verified_at,
    }
