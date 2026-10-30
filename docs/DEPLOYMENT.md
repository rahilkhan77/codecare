# Deploying CodeCare

Production origin: `https://codecare.si`. `www.codecare.si` redirects to the apex. Cookies use `COOKIE_DOMAIN=.codecare.si`.

Fill secrets in `.env` before the first boot. `DEMO_MODE` must stay `false` in production. The API refuses to start if it is true.

## VPS with Docker Compose

A small VM (2 vCPU, 4 GB RAM) with Docker and a public IPv4 is enough to start. Point DNS `A` records for `codecare.si` and `www.codecare.si` at that address before Caddy requests certificates.

1. Install Docker Engine and the Compose plugin.
2. Clone the repository and check out the release you intend to run.
3. Copy `.env.example` to `.env`.
4. Set at least:
   - `APP_SECRET` (32+ random characters)
   - `CREDENTIALS_ENCRYPTION_KEY` (Fernet key)
   - `POSTGRES_PASSWORD` (used by Compose for the database URL)
   - `APP_ENV=production`
   - `PUBLIC_BASE_URL=https://codecare.si`
   - `API_CORS_ORIGINS=https://codecare.si,https://www.codecare.si`
   - `COOKIE_DOMAIN=.codecare.si`
   - `DEMO_MODE=false`
5. Open TCP 80 and 443. Do not publish Postgres or Redis to the internet. The Compose file does not map those ports.
6. Build the sandbox image on the worker host. Verification refuses to run without it:

```bash
docker compose -f docker-compose.prod.yml --profile sandbox build sandbox
```

That image is `codecare-sandbox:local`, built from `sandbox/Dockerfile`.

7. Start the stack:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

The API container runs `alembic upgrade head` before uvicorn (`CODECARE_RUN_MIGRATIONS=1`). The worker starts only after `/readyz` is healthy. Caddy obtains certificates for `codecare.si` and redirects `www` to the apex.

8. Check `https://codecare.si/readyz` and `https://codecare.si/health`.

The worker mounts `/var/run/docker.sock` and runs as root so it can start sandbox containers. The API process runs as an unprivileged user and does not mount the socket.

### Backups

```bash
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U codecare codecare > codecare.sql
```

Keep `CREDENTIALS_ENCRYPTION_KEY` with the backup. Database rows cannot be decrypted without it.

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

GitHub App, Stripe, SMTP, and hosted AI keys are listed in `.env.example` and `docs/ENVIRONMENT.md`. The app reports those integrations as not configured and does not invent a successful connection.
