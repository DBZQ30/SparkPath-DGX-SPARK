"""文件中心（公共教学文件统一上传与适用性管理）——设计 007。

- **原件层 + 适用性**归中心；**解析产物**归各功能（`training_plan.db` / `warning.db`）。
- 一份文件按内容 hash 去重；管理员勾选 **功能 × 年级 × 专业**；各功能按
  `(feature, grade, major)` **自动引用**适用文件及其解析产物。
- **只纳公共教学文件**；学生数据文件（成绩单/名单/选课结果/课表）不进中心。
"""
from .db import DocCenterDB, DB_PATH
from . import service

__all__ = ["DB_PATH", "DocCenterDB", "service"]
