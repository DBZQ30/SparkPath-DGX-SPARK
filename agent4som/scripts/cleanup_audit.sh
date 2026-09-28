#!/bin/bash
# Daily audit log retention cleanup — removes events older than 90 days.
set -euo pipefail
cd "$(dirname "$0")/.."
source venv/bin/activate
python3 -c "
from knowledge_base.bootstrap import load_dotenv
load_dotenv()
from knowledge_base.core.audit_logger import AuditLogger
alog = AuditLogger('data/audit.db')
deleted = alog.cleanup_retention(days=90)
print(f'Cleaned {deleted} audit events older than 90 days')
alog.close()
"
