# Deploying CodeCare

Production origin: `https://codecare.si`. `www.codecare.si` redirects to the apex. Cookies use `COOKIE_DOMAIN=.codecare.si`.

Fill secrets in `.env` before the first boot. `DEMO_MODE` must stay `false` in production. The API refuses to start if it is true.

## VPS with Docker Compose

A fresh Ubuntu VM (2 vCPU, 4 GB RAM) with a public IPv4 is enough. Point DNS `A` records for `codecare.si` and `www.codecare.si` at that address before Caddy requests certificates. Open TCP 80 and 443.

One command, as root, installs Docker, clones the repo to `/opt/codecare`, builds the sandbox image, and starts Compose:

```bash
sudo bash scripts/setup-vps.sh
```

The first run copies `.env.example` to `backend/.env` and stops so you can fill it. Set `DATABASE_URL` to the Neon connection string, plus `APP_SECRET`, `FERNET_KEY`, the GitHub App values, `GROQ_API_KEY`, and `RESEND_API_KEY`. Compose does not replace `DATABASE_URL`, so the API and worker use Neon. Redis stays in Compose. A bundled Postgres service exists only with `--profile bundled-db` and is not part of the default boot.

Re-run `sudo bash scripts/setup-vps.sh` after the file is filled. It builds `codecare-sandbox:local` from `sandbox/Dockerfile`, then:

```bash
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

The API container runs `alembic upgrade head` before uvicorn (`CODECARE_RUN_MIGRATIONS=1`). The worker starts only after `/readyz` is healthy. Caddy obtains certificates for `codecare.si` and redirects `www` to the apex.

The worker mounts `/var/run/docker.sock` and runs as root so it can start sandbox containers. The API process runs as an unprivileged user and does not mount the socket. Container logs use the json-file driver with a 10 MB cap and five files.

### Health check

```bash
bash scripts/healthcheck.sh
```

That asks the API container for `/health` and `/readyz`. From the public internet, check `https://codecare.si/readyz`.

### Backups

Backups read Neon through `DATABASE_URL` in `backend/.env`. The script does not print the URL.

```bash
bash scripts/backup-db.sh
```

The dump lands in `backups/`, which is gitignored. Keep `FERNET_KEY` with the backup. Database rows cannot be decrypted without it. A daily cron entry:

```bash
0 3 * * * cd /opt/codecare && bash scripts/backup-db.sh
```

## Render, Railway, or Fly

Use the same image (`backend/Dockerfile`). Run two processes from it:

- Web: `uvicorn codecare.main:app --host 0.0.0.0 --port 8000` with `CODECARE_RUN_MIGRATIONS=1` and `CODECARE_DISABLE_WORKER=1`.
- Worker: `python -m codecare.worker` with `CODECARE_RUN_MIGRATIONS=0`. Give this process a Docker socket only if that host supports sandbox builds. Managed platforms often do not, so verification stays `inconclusive` and pull requests are not opened.

Attach managed PostgreSQL and Redis. Set `DATABASE_URL` and `REDIS_URL` from the provider. Terminate TLS at the platform and set `PUBLIC_BASE_URL=https://codecare.si`. Custom domain docs:

- Render: add `codecare.si` and `www.codecare.si`, then redirect www to the apex in the dashboard.
- Railway: attach the custom domain and use its proxy for HTTPS.
- Fly: `fly certs add codecare.si` and `fly certs add www.codecare.si`. Redirect www in the Fly proxy or a small edge config.

Health check path: `/readyz`.

## What stays off until credentials exist

GitHub App, Dodo Payments, SMTP, and hosted AI keys are listed in `.env.example` and `docs/ENVIRONMENT.md`. The app reports those integrations as not configured and does not invent a successful connection.
