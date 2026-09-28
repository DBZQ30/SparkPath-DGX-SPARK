#!/bin/bash
# SOM KB daily backup: ChromaDB + SQLite metadata + roles
# Uses SQLite .backup API for consistent snapshots.
set -euo pipefail

BACKUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data/backups"
DATA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

# 1. ChromaDB vectors (SQLite WAL checkpoint before tar for consistency)
echo "[$(date)] Backing up ChromaDB..."
sqlite3 "$DATA_DIR/chroma/chroma.sqlite3" \
    "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
if ! tar -czf "$BACKUP_DIR/chroma_${TIMESTAMP}.tar.gz" \
    -C "$DATA_DIR" chroma/ \
    2>/dev/null; then
    echo "ERROR: chroma backup failed — tar returned non-zero"
    exit 1
fi

# 2. SQLite databases (consistent via .backup API only — no cp fallback)
echo "[$(date)] Backing up SQLite databases..."
for db in quota.db audit.db; do
    if [ -f "$DATA_DIR/$db" ]; then
        if ! sqlite3 "$DATA_DIR/$db" \
            ".backup '$BACKUP_DIR/${db%.db}_${TIMESTAMP}.db'" 2>/dev/null; then
            echo "ERROR: .backup failed for $db"
            exit 1
        fi
    fi
done

# 3. Roles
if [ -f "$HOME/.hermes/roles.json" ]; then
    cp "$HOME/.hermes/roles.json" "$BACKUP_DIR/roles_${TIMESTAMP}.json"
fi

# 4. Retention cleanup
find "$BACKUP_DIR" -type f -mtime +$RETENTION_DAYS -delete

echo "[$(date)] Backup complete: $BACKUP_DIR"
ls -la "$BACKUP_DIR" | tail -5
