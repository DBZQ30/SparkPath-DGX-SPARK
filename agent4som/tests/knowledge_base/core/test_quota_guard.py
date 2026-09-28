import pytest
from knowledge_base.core.exceptions import (
    FileTooLargeError,
    DailyLimitExceededError,
    MaxFilesExceededError,
    RateLimitExceededError,
)
from knowledge_base.core.quota_manager import QuotaManager

class MockStorageRepo:
    def __init__(self, user_files_count=0, user_daily_uploads=0, recent_uploads=0):
        self.user_files_count = user_files_count
        self.user_daily_uploads = user_daily_uploads
        self.recent_uploads = recent_uploads
        self.recorded = []

    def get_user_file_count(self, user_id: str) -> int:
        return self.user_files_count

    def get_user_daily_upload_count(self, user_id: str) -> int:
        return self.user_daily_uploads

    def get_user_recent_upload_count(self, user_id: str, window_seconds: int = 60) -> int:
        return self.recent_uploads

    def record_upload(self, user_id: str) -> None:
        self.recorded.append(user_id)

def test_quota_guard_exceed_size_limit():
    repo = MockStorageRepo()
    manager = QuotaManager(repo, max_file_size_mb=10)

    with pytest.raises(FileTooLargeError) as exc:
        manager.check_quota("user_1", file_size_bytes=11 * 1024 * 1024)
    assert "文件大小超过上限" in str(exc.value)

def test_quota_guard_exceed_daily_limit():
    repo = MockStorageRepo(user_daily_uploads=30)
    manager = QuotaManager(repo, max_daily_uploads=30)

    with pytest.raises(DailyLimitExceededError):
        manager.check_quota("user_1", file_size_bytes=1024)

def test_quota_guard_exceed_max_files():
    repo = MockStorageRepo(user_files_count=50)
    manager = QuotaManager(repo, max_user_files=50)

    with pytest.raises(MaxFilesExceededError):
        manager.check_quota("user_1", file_size_bytes=1024)

def test_quota_guard_pass():
    repo = MockStorageRepo(user_files_count=10, user_daily_uploads=5)
    manager = QuotaManager(repo)
    # Should not raise any exception
    manager.check_quota("user_1", file_size_bytes=1024)
    assert "user_1" in repo.recorded

def test_rate_limit_exceeded():
    """Non-privileged users must not exceed 3 uploads per minute."""
    repo = MockStorageRepo(recent_uploads=3)
    manager = QuotaManager(repo, max_uploads_per_minute=3)
    with pytest.raises(RateLimitExceededError):
        manager.check_quota("user_1", file_size_bytes=1024)

def test_rate_limit_admin_bypass():
    """Admin/owner bypass rate limiting."""
    repo = MockStorageRepo(recent_uploads=10)
    manager = QuotaManager(repo, max_uploads_per_minute=3)
    # Should not raise — admin bypasses all limits
    manager.check_quota("admin", file_size_bytes=1024, is_admin_or_owner=True)

def test_rate_limit_under():
    """Under the limit should pass."""
    repo = MockStorageRepo(recent_uploads=2)
    manager = QuotaManager(repo, max_uploads_per_minute=3)
    manager.check_quota("user_1", file_size_bytes=1024)
    assert "user_1" in repo.recorded
