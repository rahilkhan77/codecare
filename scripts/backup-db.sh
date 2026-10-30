#!/usr/bin/env sh
set -eu
# Usage: DATABASE_URL=postgresql://user:pass@host:5432/codecare ./scripts/backup-db.sh
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=${BACKUP_DIR:-./backups}/codecare-$STAMP.sql
mkdir -p "$(dirname "$OUT")"
pg_dump --no-owner --format=plain "$DATABASE_URL" > "$OUT"
echo "Wrote backup to $OUT"
