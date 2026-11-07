# Launch checklist

CodeCare maintains the apps a vibe coder already shipped. They pay one monthly fee: Starter $29 (1 app), Indie $79 (5 apps), or Studio $199 (unlimited). Nothing below is optional if you want that loop to run against real repositories.

Do these on the server, in `.env`, then restart the API and worker.

1. `APP_SECRET` — at least 32 random characters. Signs the session cookie.
2. `FERNET_KEY` — a Fernet key in `backend/.env`. Encrypts GitHub tokens.
3. `POSTGRES_PASSWORD` and `DATABASE_URL` — PostgreSQL. Compose builds the URL from the password.
4. `PUBLIC_BASE_URL=https://codecare.si`
5. `API_CORS_ORIGINS=https://codecare.si`
6. `COOKIE_DOMAIN=.codecare.si`
7. `APP_ENV=production` and `DEMO_MODE=false`
8. GitHub App: `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, private key (`GITHUB_APP_PRIVATE_KEY` or `GITHUB_APP_PRIVATE_KEY_PATH`), `GITHUB_WEBHOOK_SECRET`.
   - Callback: `https://codecare.si/api/v1/github/oauth/callback`
   - Setup: `https://codecare.si/api/v1/github/setup`
   - Webhook: `https://codecare.si/api/v1/webhooks/github`
9. Dodo Payments, in USD. Dodo is the merchant of record.
   - `DODO_PAYMENTS_API_KEY`
   - `DODO_PAYMENTS_WEBHOOK_SECRET` (the `whsec_...` signing secret)
   - `DODO_PAYMENTS_ENVIRONMENT=live_mode` in production, `test_mode` otherwise
   - `DODO_PRODUCT_STARTER`, `DODO_PRODUCT_INDIE`, `DODO_PRODUCT_STUDIO`
   - Create three monthly subscription products: Starter $29 (1 app), Indie $79 (5 apps), Studio $199 (unlimited). Set the subscription period to 20 years so they renew until cancel.
   - Put the three products in one product collection and turn on Settings → Subscriptions → Allow Subscription Updates so the customer portal can switch plans.
   - Webhook URL: `https://codecare.si/api/v1/webhooks/dodo`
   - Events: `subscription.active`, `subscription.renewed`, `subscription.on_hold`, `subscription.cancelled`, `subscription.failed`, `subscription.expired`, `subscription.plan_changed`, `payment.succeeded`, `payment.failed`.
   - Return URL used by checkout and the portal: `https://codecare.si/workspace`
10. One model key: `GROQ_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`. Without it, CodeCare will not invent a patch.
11. SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`. Without it, password reset and the monthly report are not emailed.
12. Sandbox image on the worker host:

```bash
docker compose -f deploy/docker-compose.prod.yml --profile sandbox build sandbox
```

Without that image, verification stays inconclusive and no pull request is opened.

Counsel still needs to replace the highlighted TODOs for the company legal name and jurisdiction on the legal pages. Everything else on those pages already uses CodeCare and https://codecare.si.

`DEMO_MODE=true` is only for local review (`demo@codecare.si` / `demo-review-pass`). Production startup refuses it.
