"""audit_logger 链式哈希完整性测试（327 行此前仅 51 行基础查询测试）。

覆盖 P1-5 设计意图：任何列被篡改/删行都应被 verify_chain 检出。
（test_audit_logger.py 覆盖基础读写；此处聚焦防篡改链。）
"""

from __future__ import annotations

from pathlib import Path

from knowledge_base.core.audit_logger import AuditLogger


def _make_logger_with_events(tmp_path: Path, n: int = 4) -> AuditLogger:
    db = AuditLogger(str(tmp_path / "audit-chain.db"))
    for i in range(n):
        db.log_event(
            "file_ingested", user_id=f"u{i}", role="admin",
            filename=f"f{i}.pdf", scope="global", node_count=i,
        )
    return db


def test_chain_verifies_when_intact(tmp_path: Path):
    db = _make_logger_with_events(tmp_path)
    total, ok = db.verify_chain()
    assert total == 4
    assert ok == 4


def test_chain_detects_modified_detail(tmp_path: Path):
    db = _make_logger_with_events(tmp_path)
    # 直接改第 2 行的 filename（越过应用层，模拟 DBA 篡改）
    db._conn.execute("UPDATE audit_events SET filename='tampered.pdf' WHERE id=2")
    db._conn.commit()
    total, ok = db.verify_chain()
    assert total == 4
    assert ok == 3          # 第 2 行起失配
    assert ok < total


def test_chain_detects_deleted_row(tmp_path: Path):
    db = _make_logger_with_events(tmp_path)
    db._conn.execute("DELETE FROM audit_events WHERE id=2")
    db._conn.commit()
    total, ok = db.verify_chain()
    # 删行 → 第 3 行的 prev_hash 断链
    assert total == 3
    assert ok == 2


def test_chain_detects_injected_row(tmp_path: Path):
    """库外插入的伪造行（row_hash 对不上链）会被识别。"""
    db = _make_logger_with_events(tmp_path, n=2)
    db._conn.execute(
        """INSERT INTO audit_events
           (event_type, timestamp, user_id, role, filename, scope, detail, row_hash)
           VALUES ('file_ingested', '2026-01-01T00:00:00+00:00', 'hacker', 'admin',
                   'fake.pdf', 'global', '{}', 'deadbeef')"""
    )
    db._conn.commit()
    total, ok = db.verify_chain()
    assert total == 3
    assert ok == 2          # 伪造行失配


def test_chain_skips_legacy_null_hash_rows(tmp_path: Path):
    """链功能启用前的 NULL row_hash 行跳过，不参与校验。"""
    db = _make_logger_with_events(tmp_path, n=1)
    db._conn.execute(
        """INSERT INTO audit_events
           (event_type, timestamp, user_id, role, detail, row_hash)
           VALUES ('legacy_event', '2025-01-01T00:00:00+00:00', 'u0', 'admin',
                   '{}', NULL)"""
    )
    db._conn.commit()
    db.log_event("search", user_id="u9", role="student", query="课程")
    total, _ok = db.verify_chain()
    # NULL 行不计入；链上 2 行（原 1 行 + 新 1 行）……
    # 注意：log_event 取 prev 为 NULL 行 → prev_hash 为空，与首行哈希无关，
    # 但 NULL 行夹在中间使 verify 只统计 hash 行。
    assert total == 2


def test_retention_cleanup_removes_old_rows(tmp_path: Path):
    db = _make_logger_with_events(tmp_path, n=2)
    # 人为把第一行 retention_date 挪到 100 天前
    db._conn.execute(
        "UPDATE audit_events SET retention_date='2020-01-01' WHERE id=1")
    db._conn.commit()
    deleted = db.cleanup_retention(days=90)
    assert deleted == 1
    remaining = db.query(limit=10)
    assert len(remaining) == 1
