"""培养方案智能解读（training-plan-interpretation）—— 后端包。

独立于 academic-warning：自建 `data/training_plan.db`，只放培养方案公开数据。
入口：`python -m trainingplan.cli ...`；HTTP：`trainingplan.api:app`。
"""
from __future__ import annotations

__all__ = ["__version__"]
__version__ = "0.1.0"
