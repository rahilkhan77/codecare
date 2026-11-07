#!/usr/bin/env bash
# Ask the API container for /health and /readyz. Prints status only.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
compose=(docker compose -f deploy/docker-compose.prod.yml exec -T api python -c)
check() {
  local path="$1"
  "${compose[@]}" "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000${path}').read()" >/dev/null
  echo "${path} ok"
}
check /health
check /readyz
