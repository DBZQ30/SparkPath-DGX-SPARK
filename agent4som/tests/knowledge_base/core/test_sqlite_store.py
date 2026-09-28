"""Tests for SqliteStore — persistent quota/version metadata."""

from knowledge_base.core.sqlite_store import SqliteStore


def test_file_count_starts_zero(tmp_path):
    store = SqliteStore(str(tmp_path / "test.db"))
    assert store.get_user_file_count("user_a") == 0
    assert store.get_user_file_count("user_b") == 0


def test_set_file_metadata_increments_count(tmp_path):
    store = SqliteStore(str(tmp_path / "test.db"))
    store.set_file_metadata("user_a", "f1.pdf", {"content_hash": "abc"})
    assert store.get_user_file_count("user_a") == 1
    store.set_file_metadata("user_a", "f2.pdf", {"content_hash": "def"})
    assert store.get_user_file_count("user_a") == 2


def test_file_counts_per_user_isolated(tmp_path):
    store = SqliteStore(str(tmp_path / "test.db"))
    store.set_file_metadata("user_a", "f1.pdf", {"content_hash": "a"})
    store.set_file_metadata("user_b", "f2.pdf", {"content_hash": "b"})
    assert store.get_user_file_count("user_a") == 1
    assert store.get_user_file_count("user_b") == 1


def test_daily_upload_tracking(tmp_path):
    store = SqliteStore(str(tmp_path / "test.db"))
    assert store.get_user_daily_upload_count("user_a") == 0
    store.set_file_metadata("user_a", "f1.pdf", {"content_hash": "a"})
    assert store.get_user_daily_upload_count("user_a") == 1
    store.set_file_metadata("user_a", "f2.pdf", {"content_hash": "b"})
    assert store.get_user_daily_upload_count("user_a") == 2


def test_get_file_metadata_returns_content_hash(tmp_path):
    store = SqliteStore(str(tmp_path / "test.db"))
    store.set_file_metadata("user_a", "f1.pdf", {"content_hash": "abc123"})
    meta = store.get_file_metadata("user_a", "f1.pdf")
    assert meta == {"content_hash": "abc123"}


def test_get_file_metadata_nonexistent(tmp_path):
    store = SqliteStore(str(tmp_path / "test.db"))
    assert store.get_file_metadata("user_a", "nonexistent.pdf") is None


def test_delete_user_data_removes_all_traces(tmp_path):
    store = SqliteStore(str(tmp_path / "test.db"))
    store.set_file_metadata("user_a", "f1.pdf", {"content_hash": "a"})
    store.set_file_metadata("user_a", "f2.pdf", {"content_hash": "b"})
    store.set_file_metadata("user_b", "f3.pdf", {"content_hash": "c"})

    store.delete_user_data("user_a")

    assert store.get_user_file_count("user_a") == 0
    assert store.get_user_daily_upload_count("user_a") == 0
    assert store.get_file_metadata("user_a", "f1.pdf") is None
    # user_b data should survive
    assert store.get_user_file_count("user_b") == 1


def test_replace_existing_metadata(tmp_path):
    store = SqliteStore(str(tmp_path / "test.db"))
    store.set_file_metadata("user_a", "f1.pdf", {"content_hash": "v1"})
    store.set_file_metadata("user_a", "f1.pdf", {"content_hash": "v2"})
    meta = store.get_file_metadata("user_a", "f1.pdf")
    assert meta == {"content_hash": "v2"}


def test_list_file_metadata_by_scope(tmp_path):
    store = SqliteStore(str(tmp_path / "test.db"))
    store.set_file_metadata("user_a", "b.pdf", {"scope": "global", "content_hash": "b"})
    store.set_file_metadata("user_b", "a.pdf", {"scope": "global", "content_hash": "a"})
    store.set_file_metadata("user_a", "c.pdf", {"scope": "teachers", "content_hash": "c"})

    rows = store.list_file_metadata_by_scope("global")
    assert [r["filename"] for r in rows] == ["a.pdf", "b.pdf"]   # 按 filename 排序
    assert {r["user_id"] for r in rows} == {"user_a", "user_b"}
    assert all(r["scope"] == "global" and "ingested_at" in r for r in rows)

    assert [r["filename"] for r in store.list_file_metadata_by_scope("teachers")] == ["c.pdf"]
    assert store.list_file_metadata_by_scope("users/nobody") == []


def test_list_file_metadata_by_scope_order(tmp_path):
    store = SqliteStore(str(tmp_path / "test.db"))
    store.set_file_metadata("user_a", "a.pdf", {"scope": "global", "content_hash": "a"})
    store.set_file_metadata("user_b", "b.pdf", {"scope": "global", "content_hash": "b"})
    store.set_file_metadata("user_a", "c.pdf", {"scope": "global", "content_hash": "c"})

    # 默认正序（向后兼容既有 4 个调用点）
    assert [r["filename"] for r in store.list_file_metadata_by_scope("global")] \
        == ["a.pdf", "b.pdf", "c.pdf"]
    assert [r["filename"] for r in store.list_file_metadata_by_scope("global", "desc")] \
        == ["c.pdf", "b.pdf", "a.pdf"]
    # 非法值走白名单回退正序，不接受任意 SQL
    assert [r["filename"] for r in store.list_file_metadata_by_scope("global", "drop")] \
        == ["a.pdf", "b.pdf", "c.pdf"]
