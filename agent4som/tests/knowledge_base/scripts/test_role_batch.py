"""Tests for batch role import/export."""

import json

from knowledge_base.scripts.role_batch import export_roles_csv, import_roles_csv


def test_export_empty_platform(monkeypatch, tmp_path):
    roles_file = tmp_path / "roles.json"
    roles_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("knowledge_base.auth.role_store.ROLES_PATH", roles_file)

    csv_out = export_roles_csv("wecom")
    assert "platform,user_id,role" in csv_out
    assert len(csv_out.strip().splitlines()) == 1  # header only


def test_export_with_assignments(monkeypatch, tmp_path):
    roles_file = tmp_path / "roles.json"
    roles_file.write_text(
        json.dumps({"wecom": {"zhangsan": "admin", "lisi": "teacher"}}), encoding="utf-8"
    )
    monkeypatch.setattr("knowledge_base.auth.role_store.ROLES_PATH", roles_file)

    csv_out = export_roles_csv("wecom")
    assert "zhangsan" in csv_out
    assert "lisi" in csv_out
    assert csv_out.count("\n") == 3  # header + 2 users


def test_import_valid_roles(monkeypatch, tmp_path):
    roles_file = tmp_path / "roles.json"
    roles_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("knowledge_base.auth.role_store.ROLES_PATH", roles_file)

    csv_content = """platform,user_id,role
wecom,zhangsan,admin
wecom,lisi,teacher
"""
    imported, skipped, errors = import_roles_csv(csv_content)
    assert imported == 2
    assert skipped == 0
    assert errors == []


def test_import_skips_duplicate(monkeypatch, tmp_path):
    roles_file = tmp_path / "roles.json"
    roles_file.write_text(
        json.dumps({"wecom": {"zhangsan": "admin"}}), encoding="utf-8"
    )
    monkeypatch.setattr("knowledge_base.auth.role_store.ROLES_PATH", roles_file)

    csv_content = """platform,user_id,role
wecom,zhangsan,admin
"""
    imported, skipped, _errors = import_roles_csv(csv_content)
    assert imported == 0
    assert skipped == 1


def test_import_reports_errors(monkeypatch, tmp_path):
    roles_file = tmp_path / "roles.json"
    roles_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("knowledge_base.auth.role_store.ROLES_PATH", roles_file)

    csv_content = """platform,user_id,role
wecom,,admin
,zhangsan,
wecom,lisi,superadmin
"""
    imported, _skipped, errors = import_roles_csv(csv_content)
    assert imported == 0
    assert len(errors) == 3
