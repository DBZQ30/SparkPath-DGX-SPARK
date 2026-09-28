"""Management-side workflows for the teaching-affairs assistant (本科新生学业规划智能助手).

企业微信入口已弃用；本模块现仅服务小程序侧的教师/管理员认证审核。
"""

from .service import (
    ManagementCommand,
    ManagementResult,
    ManagementService,
)

__all__ = [
    "ManagementCommand",
    "ManagementResult",
    "ManagementService",
]
