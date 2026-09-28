"""培养方案智能解读 —— SQLite 存储层（`data/training_plan.db`，WAL）门面。

独立于 `warning.db`：本库只放培养方案（公开数据），不含任何学生数据
（例外：plan_student_binding 仅存"openid ↔ 学号"绑定关系，设计 §6.7）。

拆分后的实现见同目录模块（沿用 knowledge_base/repository/sqlite_metadata.py
先例：按 DAO 关注点拆 mixin，本文件只做组装与 re-export，历史 import 路径不变）：
- `db_core.py`       连接/建表/旧库列迁移/applies_to 工具
- `db_documents.py`  文档与解析队列（plan_document 状态机）
- `db_plan_data.py`  方案数据（学分树/课程/推荐课表/先修/毕业要求/培养模式）
- `db_policy.py`     政策（转专业 / 专业选择）
- `db_students.py`   学生侧（解读留痕/身份绑定/原件登记）
"""
from __future__ import annotations

from .db_core import (  # noqa: F401
    ALL_GRADES,
    SCHEMA_VERSION,
    TrainingPlanDBCore,
)
from .db_documents import DocumentDaoMixin
from .db_plan_data import PlanDataDaoMixin
from .db_policy import PolicyDaoMixin
from .db_students import StudentDaoMixin


class TrainingPlanDB(TrainingPlanDBCore, DocumentDaoMixin, PlanDataDaoMixin,
                     PolicyDaoMixin, StudentDaoMixin):
    """培养方案存储层统一入口（各 mixin 见上方模块清单）。"""
