"""Tests for display-name resolution (D8 prefix derivation + D10 ledger title)."""

import json

import pytest

from knowledge_base.core import display_names


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Point CHROMA_DB_PATH at a tmp dir holding a small jxtz ledger."""
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    monkeypatch.setenv("CHROMA_DB_PATH", str(chroma_dir))
    display_names._ledger_cache["mtime"] = None
    display_names._ledger_cache["index"] = {}

    path = tmp_path / display_names.LEDGER_FILENAME
    entries = [
        {"title": "[学籍管理]关于转专业的通知", "url": "https://jw.xjtu.edu.cn/info/1/10392.htm"},
        {"title": "原始标题（未截断）", "url": "https://jw.xjtu.edu.cn/info/1/10393.htm"},
    ]
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8"
    )
    return path


def test_ledger_title_wins(ledger):
    assert display_names.resolve_display_name(
        "global", "jxtz_10392_unzv285n.txt"
    ) == "[学籍管理]关于转专业的通知"


def test_ledger_matches_by_url_tail(ledger):
    assert display_names.resolve_display_name(
        "global", "jxtz_10393_8k9zntod.txt"
    ) == "原始标题（未截断）"


def test_missing_ledger_entry_falls_back_to_stripped_stem(ledger):
    assert display_names.resolve_display_name("global", "jxtz_99999_abcdefgh.txt") == "abcdefgh"


def test_non_jxtz_keeps_filename(ledger):
    assert display_names.resolve_display_name("global", "培养方案.pdf") == "培养方案.pdf"


def test_ledger_absent_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_DB_PATH", str(tmp_path / "chroma"))
    display_names._ledger_cache["mtime"] = None
    display_names._ledger_cache["index"] = {}
    assert display_names.resolve_display_name("global", "jxtz_1_abc.txt") == "abc"


def test_ledger_only_consulted_for_global(ledger):
    """A personal file starting with jxtz_ must not borrow a notice's title."""
    assert display_names.resolve_display_name("users/XiongWei", "jxtz_10392_x.txt") == "x"


def test_is_jxtz():
    assert display_names.is_jxtz("jxtz_10392_unzv285n.txt")
    assert not display_names.is_jxtz("jxtzabc.txt")
    assert not display_names.is_jxtz("培养方案.pdf")
