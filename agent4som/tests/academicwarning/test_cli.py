"""cli.py 权限门与年级透传（miniapp 对话触发入口）。"""
import academicwarning.cli as cli


def _stub_run(record):
    def _run(grade=""):
        record.append(grade)
        return 0, "选课检查：选课不合理 0 人"
    return _run


def test_check_denies_non_admin(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "miniapp")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "stu1")
    monkeypatch.setattr(cli, "is_admin", lambda p, u: False)
    calls = []
    monkeypatch.setattr(cli, "run_selection_check", _stub_run(calls))

    rc = cli.main(["check", "--grade", "2023级"])

    assert rc == 1
    assert calls == []
    assert "无权限" in capsys.readouterr().out


def test_check_denies_when_user_missing(monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "miniapp")
    monkeypatch.delenv("HERMES_SESSION_USER_ID", raising=False)
    monkeypatch.setattr(cli, "is_admin", lambda p, u: False)
    calls = []
    monkeypatch.setattr(cli, "run_selection_check", _stub_run(calls))

    assert cli.main(["check", "--grade", "2023级"]) == 1
    assert calls == []


def test_check_allows_admin_and_passes_grade(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "miniapp")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "admin1")
    monkeypatch.setattr(cli, "is_admin", lambda p, u: True)
    calls = []
    monkeypatch.setattr(cli, "run_selection_check", _stub_run(calls))

    rc = cli.main(["check", "--grade", "2023级"])

    assert rc == 0
    assert calls == ["2023级"]
    assert "选课检查" in capsys.readouterr().out


def test_check_allows_without_session_context(monkeypatch):
    monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
    monkeypatch.delenv("HERMES_SESSION_USER_ID", raising=False)
    calls = []
    monkeypatch.setattr(cli, "run_selection_check", _stub_run(calls))

    assert cli.main(["check"]) == 0
    assert calls == [""]


# ---- precheck（2026-09-22：数据齐全性预检子命令）----

def _stub_precheck(record, ready=True, pending=False, text="预检结果"):
    def _pre(grade=""):
        record.append(grade)
        return ready, pending, text
    return _pre


def test_precheck_denies_non_admin(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "miniapp")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "stu1")
    monkeypatch.setattr(cli, "is_admin", lambda p, u: False)
    calls = []
    monkeypatch.setattr(cli, "precheck_selection", _stub_precheck(calls))

    rc = cli.main(["precheck", "--grade", "2023级"])

    assert rc == 1
    assert calls == []
    assert "无权限" in capsys.readouterr().out


def test_precheck_ready_flag(monkeypatch, capsys):
    """齐备 → 末行 [precheck] READY。"""
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "miniapp")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "admin1")
    monkeypatch.setattr(cli, "is_admin", lambda p, u: True)
    calls = []
    monkeypatch.setattr(cli, "precheck_selection",
                        _stub_precheck(calls, ready=True, pending=False,
                                       text="数据齐备"))

    rc = cli.main(["precheck", "--grade", "2023级"])

    assert rc == 0
    assert calls == ["2023级"]
    out = capsys.readouterr().out
    assert "数据齐备" in out and "[precheck] READY" in out


def test_precheck_pending_flag(monkeypatch, capsys):
    """不齐备但为解析中 → [precheck] PENDING（skill 应答"稍后再试"）。"""
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "miniapp")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "admin1")
    monkeypatch.setattr(cli, "is_admin", lambda p, u: True)
    monkeypatch.setattr(cli, "precheck_selection",
                        _stub_precheck([], ready=False, pending=True,
                                       text="选课结果：✗ 解析中"))

    assert cli.main(["precheck", "--grade", "2023级"]) == 0
    assert "[precheck] PENDING" in capsys.readouterr().out


def test_precheck_incomplete_flag_rc0(monkeypatch, capsys):
    """缺数据（非解析中）→ [precheck] INCOMPLETE；只读提示 rc 仍为 0。"""
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "miniapp")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "admin1")
    monkeypatch.setattr(cli, "is_admin", lambda p, u: True)
    monkeypatch.setattr(cli, "precheck_selection",
                        _stub_precheck([], ready=False, pending=False,
                                       text="成绩单：✗ 缺 工业工程"))

    assert cli.main(["precheck", "--grade", "2023级"]) == 0
    out = capsys.readouterr().out
    assert "缺 工业工程" in out and "[precheck] INCOMPLETE" in out
