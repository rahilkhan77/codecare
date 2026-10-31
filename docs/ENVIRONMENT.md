# Environment and deployment

## Local development

1. Copy `.env.example` to `backend/.env`. Do not commit that file.
2. Set a strong `APP_SECRET` and a dedicated Fernet `FERNET_KEY`:

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

Production origin is `https://codecare.si`. Set `PUBLIC_BASE_URL=https://codecare.si`, `API_CORS_ORIGINS=https://codecare.si`, and `COOKIE_DOMAIN=.codecare.si`. Email links and OAuth redirects use `PUBLIC_BASE_URL`. Production startup refuses any other CORS origin.

SQLite is the default if `DATABASE_URL` is unset or points at sqlite. Use PostgreSQL for anything beyond local trials.

## Encryption key rotation

1. Generate a new Fernet key.
2. Set `FERNET_KEY` to the new key.
3. Set `FERNET_KEY_PREVIOUS` to the previous key.
4. Restart the API. Existing ciphertext still decrypts.
5. Re-encrypt stored GitHub tokens (operator task: decrypt with both keys, write with the current key) before clearing `FERNET_KEY_PREVIOUS`.
6. Never derive the Fernet key from `APP_SECRET`.

## Tests

```powershell
cd backend
$env:PYTHONPATH = (Get-Location).Path
pytest -q
```

## Docker Compose

```powershell
docker compose -f deploy/docker-compose.yml up --build
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
- missing or invalid `FERNET_KEY`
- a CORS list other than `https://codecare.si`
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

Password reset, email verification, and workspace invites send through the Resend API when `RESEND_API_KEY` is set. The from address is `CodeCare <noreply@codecare.si>`.

SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS`) is the fallback and is used only when `RESEND_API_KEY` is empty.

In development and test, with both empty, the API prints the message to the log and records it for tests. In production, with both empty, no mail is sent and the API says delivery is unavailable.

## Demo mode

`DEMO_MODE=false` by default. Set `DEMO_MODE=true` only in development. Startup creates `demo@codecare.si` / `demo-review-pass` and a workspace whose scan, issue, fix, verification, and pull request are prefixed `[Sample]`. Production refuses to boot when demo mode is on. See [DEPLOYMENT.md](DEPLOYMENT.md) for the VPS and hosted-platform steps.

## Dodo Payments

Subscriptions are billed in **USD**. Dodo Payments is the merchant of record. Create three monthly subscription products: Starter $29, Indie $79, and Studio $199. Set each product's subscription period to 20 years so it renews until the customer cancels. Put the product ids in `DODO_PRODUCT_STARTER`, `DODO_PRODUCT_INDIE`, and `DODO_PRODUCT_STUDIO`.

`DODO_PAYMENTS_API_KEY` is the API key. `DODO_PAYMENTS_ENVIRONMENT` is `test_mode` or `live_mode`. `DODO_PAYMENTS_WEBHOOK_SECRET` is the Standard Webhooks signing secret for `https://codecare.si/api/v1/webhooks/dodo`.

Until those values exist, billing stays dormant: the workspace says coming soon and checkout returns HTTP 503. Nothing else in the app calls Dodo. After launch, checkout stays HTTP 503 until the API key and `DODO_PRODUCT_STARTER` are set. `POST /api/v1/billing/checkout` creates a Dodo hosted checkout session and the browser redirects to `checkout_url`, with `return_url` `https://codecare.si/workspace`. Entitlements follow the webhook, which checks `webhook-id`, `webhook-timestamp`, and `webhook-signature`. Upgrades use the change-plan API immediately. Downgrades take effect on the next billing date. Cancel sets `cancel_at_next_billing_date`. `POST /api/v1/billing/portal` returns a 24-hour customer portal link. Demo mode never calls Dodo and says "Dodo Payments was not charged."

## Supabase

Optional. Local email/password accounts always work. When `SUPABASE_JWT_SECRET` is set, `POST /api/v1/auth/supabase` links a verified Supabase user to a CodeCare profile.
