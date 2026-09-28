import threading
from typing import Any
from knowledge_base.core.exceptions import (
    FileTooLargeError,
    DailyLimitExceededError,
    MaxFilesExceededError,
    RateLimitExceededError,
)

class QuotaManager:
    def __init__(self, repo: Any, max_file_size_mb: int = 100, max_user_files: int = 50,
                 max_daily_uploads: int = 30, max_uploads_per_minute: int = 3):
        self.repo = repo
        self.max_file_size_bytes = max_file_size_mb * 1024 * 1024
        self.max_user_files = max_user_files
        self.max_daily_uploads = max_daily_uploads
        self.max_uploads_per_minute = max_uploads_per_minute
        self._lock = threading.Lock()

    def check_quota(self, user_id: str, file_size_bytes: int, is_admin_or_owner: bool = False):
        if file_size_bytes > self.max_file_size_bytes:
            raise FileTooLargeError(
                f"文件大小超过上限（{self.max_file_size_bytes // (1024 * 1024)}MB）。"
            )

        # Admin and owner bypass all rate and count limits
        if is_admin_or_owner:
            return

        # Serialize quota checks to prevent concurrent requests from both
        # passing the limit check before either increments the counter.
        with self._lock:
            # Per-minute rate limit (new since 2026-07-16)
            recent = self.repo.get_user_recent_upload_count(user_id, window_seconds=60)
            if recent >= self.max_uploads_per_minute:
                raise RateLimitExceededError(
                    f"上传过于频繁，每分钟最多 {self.max_uploads_per_minute} 次。请稍后再试。"
                )

            if self.repo.get_user_daily_upload_count(user_id) >= self.max_daily_uploads:
                raise DailyLimitExceededError(
                    f"今日上传次数已达上限（{self.max_daily_uploads} 次），请明天再试。"
                )

            if self.repo.get_user_file_count(user_id) >= self.max_user_files:
                raise MaxFilesExceededError(
                    f"知识库文件数量已达上限（{self.max_user_files} 个）。"
                )

            # Record upload for rate limiting (before returning → counted even on success)
            self.repo.record_upload(user_id)
