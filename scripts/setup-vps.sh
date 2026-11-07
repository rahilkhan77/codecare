#!/usr/bin/env bash
# One-command setup for a fresh Ubuntu VPS.
# DNS A records for codecare.si and www.codecare.si must point at this machine first.
# Usage: sudo bash scripts/setup-vps.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/rahilkhan77/codecare.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/codecare}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash scripts/setup-vps.sh"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git postgresql-client
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

if [ ! -d "${APP_DIR}/.git" ]; then
  git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" fetch origin "${BRANCH}"
  git -C "${APP_DIR}" checkout "${BRANCH}"
  git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
fi
cd "${APP_DIR}"

if [ ! -f backend/.env ]; then
  cp .env.example backend/.env
  chmod 600 backend/.env
  echo "Created ${APP_DIR}/backend/.env from .env.example."
  echo "Fill DATABASE_URL (Neon), APP_SECRET, FERNET_KEY, GitHub, Groq, and Resend, then re-run this script."
  exit 1
fi
chmod 600 backend/.env

docker compose -f deploy/docker-compose.prod.yml --profile sandbox build sandbox
docker compose -f deploy/docker-compose.prod.yml up -d --build
bash scripts/healthcheck.sh
echo "CodeCare is up. Caddy requests certificates for codecare.si and redirects www to the apex."
echo "Backups: bash scripts/backup-db.sh"
