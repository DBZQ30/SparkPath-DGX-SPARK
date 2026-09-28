#!/usr/bin/env python
"""把既有 `plan_source_doc` 与学业预警公共文件导入「文件中心」`data/doc_center.db`（幂等）。

用法（仓库 agent4som 目录下）：
    venv/bin/python scripts/migrate_to_doc_center.py [--process]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from doccenter import service


def main() -> int:
    ap = argparse.ArgumentParser(description="迁移到文件中心")
    ap.add_argument("--training-plan-db", default=service.TRAINING_PLAN_DB)
    ap.add_argument("--warning-db", default="data/warning.db")
    ap.add_argument("--process", action="store_true", help="迁移后顺带消费 pending 解析任务")
    args = ap.parse_args()

    r = service.migrate_from_legacy(training_plan_db=args.training_plan_db,
                                    warning_db=args.warning_db)
    print(f"文件中心迁移：新增 {r['registered']} · 跳过(已存在/路径缺失) {r['skipped']} · 错误 {len(r['errors'])}")
    for e in r["errors"][:10]:
        print("  ⚠️", e)
    if args.process:
        p = service.process_pending(limit=1000)
        print(f"解析编排：plan/text {p['plan_or_text']} · text {p['text']} · 失败 {p['failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
