# Implementation roadmap

## Implemented and covered by automated tests

- Marketing site preserved; workspace uses live APIs
- Auth, organizations, tenant isolation
- GitHub App/OAuth/webhooks (signature + duplicate delivery); pending/revoked installs are not “connected”
- OAuth access/refresh expiry, refresh, revoke, and invalid-token handling (GitHub HTTP mocked)
- Scan pipeline, quotas, job deduplication, scheduled scans
- AI provider layer with retries; heuristic fallback does not emit patches
- Sandbox: no host execution, image required, allowlisted install/runtime argv, patch applied check, pytest-or-inconclusive
- PRs only after verified sandbox status
- Stripe checkout/portal/cancel/change-plan; webhooks map price IDs; disabled without keys
- Rate limits, production secret and Fernet checks, readiness probe, structured logging in production
- Encryption key rotation via `CREDENTIALS_ENCRYPTION_KEY_PREVIOUS`

## Blocked on operator credentials / infrastructure

- GitHub App install against a real repository
- Groq/OpenAI/Anthropic model-backed diagnosis
- Docker engine + image `codecare-sandbox:local` on the worker host
- Stripe live checkout
- TLS certificates in front of Caddy/uvicorn

## Not implemented (and not claimed)

- Production/runtime APM of customer applications
- Automatic merge or deploy of fixes
- Redis-backed queue (jobs are PostgreSQL/SQLite polled)
- Hostname-restricted registry proxy for sandbox installs
- Execution of customer `npm test` / `next` scripts
