"""学生身份绑定（openid → 学号）单测 —— 设计 §6.7（A 方案）。

用临时 `warning.db`（只含 roster）与临时 `training_plan.db`，不碰真实数据。
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_warning_db(path: str, rows: list[tuple]) -> None:
    """rows = [(student_id, name, grade, major, class_name, status)]"""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE roster (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " student_id TEXT NOT NULL, name TEXT DEFAULT '', grade TEXT DEFAULT '',"
            " major TEXT DEFAULT '', class_name TEXT DEFAULT '', status TEXT DEFAULT '',"
            " source_file_id INTEGER NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO roster (student_id,name,grade,major,class_name,status,source_file_id)"
            " VALUES (?,?,?,?,?,?,1)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离 DB 路径：临时 warning.db + 临时 training_plan.db。

    档案库（miniapp.db）指向不存在的路径 → 走 roster 兜底路径（隔离真实档案）。
    """
    from trainingplan import identity, service

    warning_db = str(tmp_path / "warning.db")
    _make_warning_db(warning_db, [
        ("9000000001", "学生甲", "2023级", "工商管理", "工商2301", "在读"),
        ("9000000002", "张三", "2023级", "工业工程", "工业2301", "在读"),
        ("9000000003", "李四", "2024级", "大数据管理与应用", "大数据2401", "在读"),
    ])
    tp_db = str(tmp_path / "training_plan.db")

    monkeypatch.setattr(identity, "WARNING_DB_PATH", warning_db)
    monkeypatch.setattr(identity, "MINIAPP_DB_PATH", str(tmp_path / "no_miniapp.db"))
    monkeypatch.setattr(service, "DB_PATH", tp_db)
    return {"warning_db": warning_db, "tp_db": tp_db, "identity": identity}


# 学籍档案列（与 011 一致）
_ARCHIVE_COLS = ["student_id", "name", "gender", "grade", "enroll_grade", "enrolled",
                 "school", "department", "host_department", "major", "major_direction",
                 "class_name", "phone", "residence_college"]


def _make_methods_db(path: str, profiles: list[tuple], archive: list[dict]) -> None:
    """profiles = [(platform, user_id, phone, student_staff_id, phone_verified)]"""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE admission_profiles (platform TEXT NOT NULL, user_id TEXT NOT NULL,"
            " phone TEXT, student_staff_id TEXT, phone_verified INTEGER DEFAULT 0,"
            " PRIMARY KEY (platform, user_id))"
        )
        conn.execute(
            "CREATE TABLE student_archive (" + ",".join(f"{c} TEXT" for c in _ARCHIVE_COLS)
            + ", PRIMARY KEY (student_id))"
        )
        conn.executemany(
            "INSERT INTO admission_profiles (platform,user_id,phone,student_staff_id,phone_verified)"
            " VALUES (?,?,?,?,?)", profiles)
        for a in archive:
            conn.execute(
                f"INSERT INTO student_archive ({','.join(_ARCHIVE_COLS)})"
                f" VALUES ({','.join('?' for _ in _ARCHIVE_COLS)})",
                [a.get(c, "") for c in _ARCHIVE_COLS])
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def env_archive(tmp_path, monkeypatch):
    """带学籍档案（miniapp.db）的环境：手机号优先、学号兜底。"""
    from trainingplan import identity, service

    methods_db = str(tmp_path / "miniapp.db")
    _make_methods_db(
        methods_db,
        profiles=[
            ("miniapp", "openid-phone", "13800000001", "", 1),
            ("miniapp", "openid-linked", "", "9000000001", 0),
            ("miniapp", "openid-none", "", "", 0),
        ],
        archive=[
            {"student_id": "9000000001", "name": "学生甲", "gender": "男",
             "grade": "2023级", "enroll_grade": "2023级", "enrolled": "是",
             "school": "管理学院", "major": "0824工商管理", "class_name": "工商2301",
             "phone": "13800000001", "residence_college": "彭康书院"},
            {"student_id": "9000000002", "name": "张三", "grade": "2023级",
             "major": "0848管理类", "class_name": "管理类2301", "phone": ""},
        ],
    )
    tp_db = str(tmp_path / "training_plan.db")
    monkeypatch.setattr(identity, "MINIAPP_DB_PATH", methods_db)
    monkeypatch.setattr(identity, "WARNING_DB_PATH", str(tmp_path / "no_warning.db"))
    monkeypatch.setattr(service, "DB_PATH", tp_db)
    return {"methods_db": methods_db, "tp_db": tp_db, "identity": identity}


# ─────────────────────────── 校验 ───────────────────────────

def test_verify_student_ok(env):
    r = env["identity"].verify_student("9000000001", "学生甲")
    assert r["ok"] is True
    assert r["student"]["student_id"] == "9000000001"
    assert r["student"]["major"] == "工商管理"
    assert r["student"]["grade"] == "2023级"


def test_verify_student_tolerates_whitespace(env):
    r = env["identity"].verify_student(" 9000000001 ", " 学 生 甲 ")
    assert r["ok"] is True


def test_verify_student_name_mismatch(env):
    r = env["identity"].verify_student("9000000001", "王五")
    assert r["ok"] is False
    assert r["reason"] == "no_match"
    assert "不一致" in r["message"]


def test_verify_student_id_not_found(env):
    r = env["identity"].verify_student("9999999999", "学生甲")
    assert r["ok"] is False
    assert r["reason"] == "no_match"


def test_verify_student_missing_input(env):
    assert env["identity"].verify_student("", "学生甲")["reason"] == "missing"
    assert env["identity"].verify_student("9000000001", "")["reason"] == "missing"


def test_verify_student_no_db(tmp_path, monkeypatch):
    from trainingplan import identity
    monkeypatch.setattr(identity, "MINIAPP_DB_PATH", str(tmp_path / "no_miniapp.db"))
    r = identity.verify_student("9000000001", "学生甲",
                                warning_db=str(tmp_path / "nope.db"))
    assert r["ok"] is False
    assert r["reason"] == "no_db"


# ─────────────────────────── 绑定 / 查询 / 解绑 ───────────────────────────

def test_bind_whoami_resolve(env):
    ident = env["identity"]
    r = ident.bind("miniapp", "openid-1", "9000000001", "学生甲")
    assert r["ok"] is True
    assert r["binding"]["student_id"] == "9000000001"
    assert "已绑定" in r["message"]

    w = ident.whoami("miniapp", "openid-1")
    assert w["ok"] is True and w["bound"] is True
    assert w["binding"]["major"] == "工商管理"
    assert ident.resolve_student_id("miniapp", "openid-1") == "9000000001"


def test_bind_name_mismatch_does_not_write(env):
    ident = env["identity"]
    r = ident.bind("miniapp", "openid-2", "9000000001", "王五")
    assert r["ok"] is False
    # 未写入
    assert ident.whoami("miniapp", "openid-2")["bound"] is False


def test_bind_requires_user_id(env):
    r = env["identity"].bind("miniapp", "", "9000000001", "学生甲")
    assert r["ok"] is False
    assert r["reason"] == "no_user"


def test_whoami_unbound(env):
    w = env["identity"].whoami("miniapp", "nobody")
    assert w["ok"] is False and w["bound"] is False
    assert w["reason"] == "unbound"


def test_rebind_updates_student(env):
    ident = env["identity"]
    ident.bind("miniapp", "openid-3", "9000000001", "学生甲")
    ident.bind("miniapp", "openid-3", "9000000002", "张三")  # 换绑
    w = ident.whoami("miniapp", "openid-3")
    assert w["binding"]["student_id"] == "9000000002"
    assert w["binding"]["name"] == "张三"


def test_binding_is_per_user(env):
    ident = env["identity"]
    ident.bind("miniapp", "openid-a", "9000000001", "学生甲")
    assert ident.whoami("miniapp", "openid-b")["bound"] is False
    assert ident.resolve_student_id("miniapp", "openid-b") == ""


def test_unbind(env):
    ident = env["identity"]
    ident.bind("miniapp", "openid-4", "9000000003", "李四")
    r = ident.unbind("miniapp", "openid-4")
    assert r["ok"] is True
    assert ident.whoami("miniapp", "openid-4")["bound"] is False
    # 重复解绑 → 明确提示
    assert ident.unbind("miniapp", "openid-4")["ok"] is False


def test_binding_persists_across_db_instances(env):
    ident = env["identity"]
    ident.bind("miniapp", "openid-5", "9000000001", "学生甲")
    # 新开一个 DB 连接（模拟进程重启）仍能读到
    from trainingplan.db import TrainingPlanDB
    db = TrainingPlanDB(env["tp_db"])
    try:
        b = db.get_binding("miniapp", "openid-5")
        assert b is not None and b.student_id == "9000000001"
    finally:
        db.close()


# ─────────────────────────── CLI ───────────────────────────

def test_cli_bind_whoami_unbind(env, monkeypatch, capsys):
    from trainingplan import cli
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "miniapp")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "openid-cli")

    rc = cli.main(["bind", "--student-id", "9000000001", "--name", "学生甲"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "已绑定" in out

    cli.main(["whoami"])
    out = capsys.readouterr().out
    assert "9000000001" in out and "工商管理" in out

    cli.main(["unbind"])
    out = capsys.readouterr().out
    assert "已解绑" in out

    cli.main(["whoami"])
    out = capsys.readouterr().out
    assert "未匹配到学籍" in out


def test_cli_bind_bad_name(env, monkeypatch, capsys):
    from trainingplan import cli
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "miniapp")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "openid-cli2")
    cli.main(["bind", "--student-id", "9000000001", "--name", "王五"])
    out = capsys.readouterr().out
    assert "不一致" in out


# ─────────────────────── 学籍档案自动匹配（011） ───────────────────────

def test_normalize_archive_major():
    from trainingplan.identity import normalize_archive_major
    assert normalize_archive_major("0824工商管理") == {
        "major": "工商管理", "raw": "0824工商管理", "undetermined": False}
    assert normalize_archive_major("0842会计学（ACCA）")["major"] == "会计学（ACCA）"
    m = normalize_archive_major("0848管理类")
    assert m["major"] == "管理类" and m["undetermined"] is True


def test_resolve_archive_by_phone(env_archive):
    r = env_archive["identity"].resolve_archive("miniapp", "openid-phone")
    assert r["ok"] is True and r["match"] == "phone"
    assert r["archive"]["student_id"] == "9000000001"
    assert r["archive"]["major_normalized"] == "工商管理"


def test_resolve_archive_by_student_id(env_archive):
    r = env_archive["identity"].resolve_archive("miniapp", "openid-linked")
    assert r["ok"] is True and r["match"] == "student_id"
    assert r["archive"]["name"] == "学生甲"


def test_resolve_archive_no_match(env_archive):
    r = env_archive["identity"].resolve_archive("miniapp", "openid-none")
    assert r["ok"] is False and r["reason"] == "no_match"


def test_whoami_prefers_archive(env_archive):
    ident = env_archive["identity"]
    w = ident.whoami("miniapp", "openid-phone")
    assert w["ok"] is True and w["source"] == "archive"
    assert w["student"]["student_id"] == "9000000001"
    assert w["student"]["major"] == "工商管理"
    assert ident.resolve_student_id("miniapp", "openid-phone") == "9000000001"


def test_find_archive_marks_undetermined_major(env_archive):
    a = env_archive["identity"].find_archive_by_student_id("9000000002")
    assert a["major_normalized"] == "管理类" and a["undetermined_major"] is True


def test_verify_student_archive_is_authoritative(env_archive):
    ident = env_archive["identity"]
    r = ident.verify_student("9000000001", "学生甲")
    assert r["ok"] is True and r["source"] == "archive"
    assert r["student"]["major"] == "工商管理"
    # 姓名不符
    assert ident.verify_student("9000000001", "王五")["reason"] == "no_match"


def test_bind_via_archive_verify(env_archive):
    ident = env_archive["identity"]
    r = ident.bind("miniapp", "openid-new", "9000000001", "学生甲")
    assert r["ok"] is True
    # 绑定后 whoami 仍以档案为准（source=archive），学号一致
    w = ident.whoami("miniapp", "openid-new")
    assert w["bound"] is True and w["student"]["student_id"] == "9000000001"


def test_whoami_falls_back_to_binding_without_archive(env):
    """无档案库时：绑定后 whoami 走本地绑定（source=binding）。"""
    ident = env["identity"]
    ident.bind("miniapp", "openid-fb", "9000000001", "学生甲")
    w = ident.whoami("miniapp", "openid-fb")
    assert w["bound"] is True and w["source"] == "binding"
    assert w["student"]["student_id"] == "9000000001"

