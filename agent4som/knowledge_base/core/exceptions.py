class KnowledgeBaseError(Exception):
    """Base exception for all Knowledge Base errors."""
    pass

class QuotaExceededError(KnowledgeBaseError):
    """Raised when a quota is exceeded."""
    pass

class FileTooLargeError(QuotaExceededError):
    pass

class DailyLimitExceededError(QuotaExceededError):
    pass

class MaxFilesExceededError(QuotaExceededError):
    pass

class RateLimitExceededError(QuotaExceededError):
    """Raised when per-minute upload rate limit is exceeded."""
    pass

class SchemaValidationError(KnowledgeBaseError):
    pass
