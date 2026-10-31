# Deploying CodeCare

The intended host is Vercel for the static site and Render for the API and worker. A VPS with Docker Compose remains available below. Production origin is `https://codecare.si`. `www.codecare.si` redirects to the apex. The browser calls `https://codecare.si/api/...`, and Vercel proxies that to the API, so session cookies stay first-party. `COOKIE_DOMAIN=.codecare.si`. `API_CORS_ORIGINS` is only `https://codecare.si`. `DEMO_MODE` must stay `false`. The API refuses to start if it is true.

## DNS

At the registrar for `codecare.si`:

| Host | Type | Value |
| --- | --- | --- |
| `@` | A | `76.76.21.21` (Vercel). The Vercel domain screen shows this record when you add the apex. |
| `www` | CNAME | `cname.vercel-dns.com` |
| `api` | CNAME | the Render web service hostname, such as `codecare-api.onrender.com` |

Wait until the `api` CNAME answers before expecting the Vercel rewrite to reach Render. Until then, set the rewrite in `frontend/vercel.json` to the `onrender.com` URL.

## Vercel

1. Sign in at [vercel.com](https://vercel.com) and choose **Add New… → Project**.
2. Import the GitHub repository `rahilkhan77/codecare`.
3. Set **Root Directory** to `frontend`. Vercel reads `frontend/vercel.json`.
4. Framework preset: **Other**. There is no build command and no install command. Output is the `frontend` directory itself.
5. Add the domain `codecare.si`, then add `www.codecare.si`. Vercel shows the A and CNAME records above. `vercel.json` redirects `www` to the apex.
6. The rewrite sends `/api/:path*` to `https://api.codecare.si/api/:path*`. To use the Render URL before DNS exists, change that destination to `https://<your-service>.onrender.com/api/:path*` and redeploy. Change it back to `https://api.codecare.si` after the CNAME is live.
7. Deploy. Open `https://codecare.si/` and `https://codecare.si/api/v1/billing/plans` (the second request is proxied). The response `Set-Cookie` is for `.codecare.si`, so the browser stores it as a first-party cookie.

Headers on the static site match the API: the theme script hash, CSP, HSTS, `X-Frame-Options: DENY`, and `nosniff`.

## Render

1. Sign in at [dashboard.render.com](https://dashboard.render.com) and choose **New → Blueprint**.
2. Connect the same GitHub repository. Render reads `render.yaml` at the repo root.
3. Apply the blueprint. It creates two Python 3.12 services in Oregon and does **not** create a Render database:
   - Web `codecare-api`: `alembic upgrade head && uvicorn codecare.main:app --host 0.0.0.0 --port $PORT`, health check `/readyz`, root directory `backend`.
   - Worker `codecare-worker`: `python -m codecare.worker`, same root directory. The web service sets `CODECARE_DISABLE_WORKER=1` so jobs run only in the worker.
4. When Render prompts for `sync: false` values, paste them from your local `backend/.env`. Do not commit them.
   - `APP_SECRET`
   - `FERNET_KEY`
   - `DATABASE_URL` (the existing Neon URL, including `sslmode=require`)
   - `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_WEBHOOK_SECRET`
   - `GITHUB_APP_PRIVATE_KEY` as one line with `\n` between PEM lines
   - `GROQ_API_KEY`, `RESEND_API_KEY`
5. The blueprint already sets `APP_ENV=production`, `PUBLIC_BASE_URL=https://codecare.si`, `API_CORS_ORIGINS=https://codecare.si`, `COOKIE_DOMAIN=.codecare.si`, `DEMO_MODE=false`, `SANDBOX_MODE=static`, `GITHUB_APP_SLUG=codecare-ai`, and `GROQ_MODEL=openai/gpt-oss-120b`.
6. On the web service, open **Settings → Custom Domains**, add `api.codecare.si`, and create the CNAME Render displays.
7. After the first deploy, open `https://api.codecare.si/readyz` and `https://api.codecare.si/health`.
8. GitHub App callback URL: `https://codecare.si/api/v1/github/oauth/callback`. Webhook URL: `https://api.codecare.si/api/v1/webhooks/github`. Dodo webhook, when you turn billing on: `https://api.codecare.si/api/v1/webhooks/dodo`.

### Verification on Render

Render cannot run Docker inside the service. `SANDBOX_MODE=static` parses Python files with `ast.parse` and does not import, exec, or subprocess customer code. The result is `limited`. A `limited` run does not open a pull request. JavaScript and test suites are not executed. `SANDBOX_MODE=remote` is reserved for a future off-host sandbox; if `SANDBOX_REMOTE_URL` is set, the client still sends nothing. The VPS path below is the one that can mark a patch `verified`, because it runs pytest inside the sandbox image.

## VPS with Docker Compose

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

## What stays off until credentials exist

GitHub App, Dodo Payments, SMTP, and hosted AI keys are listed in `.env.example` and `docs/ENVIRONMENT.md`. The app reports those integrations as not configured and does not invent a successful connection.
