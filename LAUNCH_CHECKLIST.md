# Launch checklist

CodeCare maintains the apps a vibe coder already shipped. They pay one monthly fee: Starter $29 (1 app), Indie $79 (5 apps), or Studio $199 (unlimited). Nothing below is optional if you want that loop to run against real repositories.

Do these on the server, in `.env`, then restart the API and worker.

1. `APP_SECRET` — at least 32 random characters. Signs the session cookie.
2. `CREDENTIALS_ENCRYPTION_KEY` — a Fernet key. Encrypts GitHub tokens.
3. `POSTGRES_PASSWORD` and `DATABASE_URL` — PostgreSQL. Compose builds the URL from the password.
4. `PUBLIC_BASE_URL=https://codecare.si`
5. `API_CORS_ORIGINS=https://codecare.si,https://www.codecare.si`
6. `COOKIE_DOMAIN=.codecare.si`
7. `APP_ENV=production` and `DEMO_MODE=false`
8. GitHub App: `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, private key (`GITHUB_APP_PRIVATE_KEY` or `GITHUB_APP_PRIVATE_KEY_PATH`), `GITHUB_WEBHOOK_SECRET`.
   - Callback: `https://codecare.si/api/v1/github/oauth/callback`
   - Setup: `https://codecare.si/api/v1/github/setup`
   - Webhook: `https://codecare.si/api/v1/webhooks/github`
9. Razorpay, in USD: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_PLAN_STARTER`, `RAZORPAY_PLAN_INDIE`, `RAZORPAY_PLAN_STUDIO`.
   - Enable international payments so the plans can be charged in USD.
   - Create three subscription plans: Starter $29 / month (1 app), Indie $79 / month (5 apps), Studio $199 / month (unlimited).
   - Webhook: `https://codecare.si/api/v1/webhooks/razorpay`
   - Events: `subscription.activated`, `subscription.charged`, `subscription.pending`, `subscription.halted`, `subscription.cancelled`, `subscription.completed`, `subscription.updated`, `payment.failed`.
10. One model key: `GROQ_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`. Without it, CodeCare will not invent a patch.
11. SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`. Without it, password reset and the monthly report are not emailed.
12. Sandbox image on the worker host:

```bash
docker compose -f docker-compose.prod.yml --profile sandbox build sandbox
```

Without that image, verification stays inconclusive and no pull request is opened.

Counsel still needs to replace the highlighted TODOs for the company legal name and jurisdiction on the legal pages. Everything else on those pages already uses CodeCare and https://codecare.si.

`DEMO_MODE=true` is only for local review (`demo@codecare.si` / `demo-review-pass`). Production startup refuses it.
