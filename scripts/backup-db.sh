#!/usr/bin/env sh
# Dump the database in DATABASE_URL. On the VPS that URL is Neon.
# Does not print the URL.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
if [ -z "${DATABASE_URL:-}" ] && [ -f "$ROOT/backend/.env" ]; then
  DATABASE_URL=$(ROOT="$ROOT" python3 -c '
import os
from pathlib import Path
env = Path(os.environ["ROOT"]) / "backend" / ".env"
for line in env.read_text(encoding="utf-8").splitlines():
    if line.startswith("DATABASE_URL="):
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'\''", "\""):
            value = value[1:-1]
        print(value)
        break
')
fi
if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is not set" >&2
  exit 1
fi
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=${BACKUP_DIR:-$ROOT/backups}/codecare-$STAMP.sql
mkdir -p "$(dirname "$OUT")"
pg_dump --no-owner --format=plain "$DATABASE_URL" > "$OUT"
echo "Wrote backup to $OUT"
