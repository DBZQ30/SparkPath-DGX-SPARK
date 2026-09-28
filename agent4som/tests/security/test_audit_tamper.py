"""审计完整性（Audit Trail Tamper-proofing）测试：SHA-256 链式哈希。

设计 P1-5：审计行任何列被篡改、行被删除、行被伪造都会被
``verify_chain()`` 检出。功能面用例见
tests/knowledge_base/core/test_audit_chain.py；本文件聚焦安全声明：
- 不可抵赖：正序写入 → 链完整
- 篡改可见：改 detail / 删行 / 伪造行 → 校验失败数下降
- 无法离链伪造：库外 INSERT 的 row 过不了哈希链
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_base.core.audit_logger import AuditLogger


def _chain(tmp_path: Path, n: int = 3) -> AuditLogger:
    audit = AuditLogger(str(tmp_path / "audit.db"))
    for i in range(n):
        audit.log_event("file_ingested", user_id=f"u{i}", role="admin",
                        filename=f"f{i}.pdf", scope="global", node_count=i)
    return audit


@pytest.mark.security
def test_honest_chain_verifies(tmp_path):
    audit = _chain(tmp_path)
    total, ok = audit.verify_chain()
    assert (total, ok) == (3, 3)


@pytest.mark.security
def test_db_admin_cannot_silently_rewrite_history(tmp_path):
    """DBA 改 uid（越权核心诉求）：该行起全部失配。"""
    audit = _chain(tmp_path)
    audit._conn.execute("UPDATE audit_events SET user_id='victim' WHERE id=2")
    audit._conn.commit()
    total, ok = audit.verify_chain()
    assert ok < total
    assert total - ok == 1


@pytest.mark.security
def test_row_deletion_breaks_chain(tmp_path):
    audit = _chain(tmp_path)
    audit._conn.execute("DELETE FROM audit_events WHERE id=2")
    audit._conn.commit()
    total, ok = audit.verify_chain()
    assert ok < total


@pytest.mark.security
def test_out_of_band_inserted_row_fails_verification(tmp_path):
    """绕过 log_event 的伪造行（伪造者不知链上 prev_hash）→ 校验失配。"""
    audit = _chain(tmp_path, n=2)
    audit._conn.execute(
        """INSERT INTO audit_events
           (event_type, timestamp, user_id, role, filename, scope, detail, row_hash)
           VALUES ('file_ingested', '2026-01-01T00:00:00', 'attacker', 'admin',
                   'fake.pdf', 'global', '{}', '0000dead')"""
    )
    audit._conn.commit()
    total, ok = audit.verify_chain()
    assert total == 3 and ok == 2


@pytest.mark.security
def test_replayed_row_hash_rejected(tmp_path):
    """抄别的行的 row_hash 也过不了（prev_hash 参与哈希）。"""
    audit = _chain(tmp_path, n=2)
    row = audit._conn.execute("SELECT row_hash FROM audit_events WHERE id=1").fetchone()
    audit._conn.execute(
        """INSERT INTO audit_events
           (event_type, timestamp, user_id, role, filename, scope, detail, row_hash)
           VALUES ('file_ingested', '2026-01-02T00:00:00', 'attacker', 'admin',
                   'fake.pdf', 'global', '{}', ?)""",
        (row["row_hash"] if not isinstance(row, tuple) else row[0],),
    )
    audit._conn.commit()
    total, ok = audit.verify_chain()
    assert ok < total


@pytest.mark.security
def test_verify_chain_counts_not_excuses(tmp_path):
    """校验结果是计数而非布尔：失败行数可量化（供 admin_cli audit --stats 报表）。"""
    audit = _chain(tmp_path, n=5)
    audit._conn.execute("UPDATE audit_events SET filename='x' WHERE id IN (2, 4)")
    audit._conn.commit()
    total, ok = audit.verify_chain()
    assert total == 5
    assert total - ok >= 2
