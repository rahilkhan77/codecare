#!/bin/sh
set -eu
if [ "${CODECARE_RUN_MIGRATIONS:-0}" = "1" ]; then
  alembic upgrade head
fi
exec "$@"
