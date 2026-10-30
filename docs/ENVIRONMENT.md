# Environment and deployment

## Local development

1. Copy `.env.example` to `.env`.
2. Set a strong `APP_SECRET` and a dedicated Fernet `CREDENTIALS_ENCRYPTION_KEY`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

3. Create a virtualenv and install backend dependencies:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
uvicorn codecare.main:app --reload --port 8000
```

4. Open http://127.0.0.1:8000/ for the marketing site and http://127.0.0.1:8000/workspace/login.html to sign in.

Production origin is `https://codecare.si`. Set `PUBLIC_BASE_URL=https://codecare.si`, `API_CORS_ORIGINS=https://codecare.si,https://www.codecare.si`, and `COOKIE_DOMAIN=.codecare.si`. Email links, OAuth redirects, and Stripe return URLs use `PUBLIC_BASE_URL`.

SQLite is the default if `DATABASE_URL` is unset or points at sqlite. Use PostgreSQL for anything beyond local trials.

## Encryption key rotation

1. Generate a new Fernet key.
2. Set `CREDENTIALS_ENCRYPTION_KEY` to the new key.
3. Set `CREDENTIALS_ENCRYPTION_KEY_PREVIOUS` to the previous key.
4. Restart the API. Existing ciphertext still decrypts.
5. Re-encrypt stored GitHub tokens (operator task: decrypt with both keys, write with the current key) before clearing `CREDENTIALS_ENCRYPTION_KEY_PREVIOUS`.
6. Never derive the Fernet key from `APP_SECRET`.

## Tests

```powershell
cd backend
$env:PYTHONPATH = (Get-Location).Path
pytest -q
```

## Docker Compose

```powershell
docker compose up --build
```

Build the sandbox image before expecting verified fixes. The **Docker engine must be running**:

```powershell
docker build -t codecare-sandbox:local sandbox
```

Without that image, or if the engine is down, verification is `inconclusive` and pull requests are not opened.

Install phase (pinned pip / `npm ci --ignore-scripts`) may use a bridge network inside Docker. Runtime tests always use `--network none`.

## GitHub App

See `docs/GITHUB_APP.md`. Enable **Expire user authorization tokens** so GitHub issues refresh tokens; CodeCare stores expiry and refreshes before use.

Until App variables exist, the workspace reports GitHub as not configured. It does not pretend the connection succeeded.

## Production

Set `APP_ENV=production`. Startup refuses:

- weak `APP_SECRET`
- missing or invalid `CREDENTIALS_ENCRYPTION_KEY`
- SQLite
- wildcard CORS

- Terminate TLS in front of uvicorn (see `deploy/Caddyfile`).
- Run `alembic upgrade head` against PostgreSQL instead of relying on `create_all`.
- Run API and worker as separate processes (`CODECARE_DISABLE_WORKER=1` on the API).
- Build `codecare-sandbox:local` on the worker host and grant that process Docker socket access only.
- Cookies are `Secure` when `APP_ENV=production`.
- Database backups: `scripts/backup-db.sh`.
- Liveness: `GET /health`. Readiness (includes database): `GET /readyz`.

## Email

Password reset, email verification, and workspace invites use SMTP when `SMTP_HOST` is set (`SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS`).

In development and test, an empty `SMTP_HOST` prints the message to the API log and records it for tests. In production, an empty `SMTP_HOST` does not send mail and the API says delivery is unavailable.

Suggested production from-address: `CodeCare <noreply@codecare.si>`.

## Demo mode

`DEMO_MODE=false` by default. Set `DEMO_MODE=true` only in development. Startup creates `demo@codecare.si` / `demo-review-pass` and a workspace whose scan, issue, fix, verification, and pull request are prefixed `[Sample]`. Production refuses to boot when demo mode is on. See [DEPLOYMENT.md](DEPLOYMENT.md) for the VPS and hosted-platform steps.

## Stripe

Checkout remains HTTP 503 until `STRIPE_SECRET_KEY` and at least `STRIPE_PRICE_STARTER` are set. Entitlements are taken from `subscriptions` rows written by signed webhooks.

## Supabase

Optional. Local email/password accounts always work. When `SUPABASE_JWT_SECRET` is set, `POST /api/v1/auth/supabase` links a verified Supabase user to a CodeCare profile.
