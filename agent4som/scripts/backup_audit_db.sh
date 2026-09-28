#!/bin/bash
# Daily backup of audit.db with 7-day rotation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
AUDIT_DB="$BASE_DIR/data/audit.db"
BACKUP_DIR="$BASE_DIR/data/backups"
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

if [ ! -f "$AUDIT_DB" ]; then
    echo "$(date -Iseconds) audit.db not found, skipping backup"
    exit 0
fi

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/audit_${TIMESTAMP}.db"

# Use SQLite backup API for consistent snapshot
sqlite3 "$AUDIT_DB" ".backup '$BACKUP_FILE'"
echo "$(date -Iseconds) backup: $BACKUP_FILE ($(stat -c%s "$BACKUP_FILE") bytes)"

# Rotate old backups
find "$BACKUP_DIR" -name "audit_*.db" -mtime +$RETENTION_DAYS -delete
