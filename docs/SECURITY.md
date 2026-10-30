# Security plan

## Authentication and sessions

- Passwords are hashed with bcrypt. They are never stored in plaintext.
- Sessions are server-backed JWTs in an httpOnly, `SameSite=Lax` cookie. Logout revokes the session row. Tokens are not returned to JavaScript.
- State-changing API calls require a double-submit CSRF token (`codecare_csrf` plus `X-CSRF-Token`). Signed webhooks are exempt.
- Optional Supabase JWTs are verified with the configured JWT secret before a local profile is linked.

## Authorization and tenancy

- Every repository, scan, issue, fix, job, and pull request is owned by an organization.
- API handlers load membership before returning a row. Cross-tenant IDs return 404, not 403, to avoid existence leaks where practical.
- GitHub repo connect checks that the installation actually includes that repository.

## Encryption at rest

- `CREDENTIALS_ENCRYPTION_KEY` must be a Fernet key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
- Production **does not** derive this key from `APP_SECRET`. Missing or invalid keys fail startup.
- Invalid keys fail startup in every environment if the variable is set.
- Rotation: set `CREDENTIALS_ENCRYPTION_KEY` to the new key and `CREDENTIALS_ENCRYPTION_KEY_PREVIOUS` to the old key. Decrypt tries current then previous. Re-encrypt stored tokens (`reencrypt_secret`) before removing the previous key.

## GitHub credentials

- User OAuth access and refresh tokens are encrypted at rest. Expiry is stored and checked with a two-minute skew. Expired tokens are refreshed when GitHub issued a refresh token; otherwise they are marked `expired` and never used.
- Revoked or invalid credentials are cleared. `POST /api/v1/github/oauth/revoke` revokes the token at GitHub and disconnects identity.
- Installation tokens are minted per request (about one hour) and are not persisted.
- Webhooks require `X-Hub-Signature-256`, unique `X-GitHub-Delivery`, and reject duplicate deliveries. Uninstall/suspend disconnects repositories and clears OAuth material.

## Execution

- Customer repository code is never imported, `exec`'d, or subprocessed on the API host.
- Dependency install (when a lockfile / pinned requirements exist) runs in Docker with a bridge network, `--ignore-scripts` / `--only-binary=:all:`, and resource limits. That phase is not unrestricted host execution.
- Test runtime uses `docker run --network none` with memory, CPU, PID, nofile, capability drop, and time limits.
- `verified` requires the patch bytes on disk and at least one pytest that actually passed. compile-only, tsc-only, empty suites, and failed installs are `inconclusive` or `failed`. They are never treated as verified.

## AI

- Prompts may include source excerpts. Logs redact tokens, PEM blocks, and common secret patterns.
- Model output cannot mark a fix `verified`.

## Billing

- Checkout requires Razorpay keys. The key secret never goes to the browser.
- Payment signatures and `X-Razorpay-Signature` are checked with HMAC-SHA256.
- Entitlements come from `subscriptions` rows written by verified webhooks, not from the frontend.

## Other controls

- Path-based rate limits on register, login, password reset, contact, GitHub webhooks, and scan POSTs (disabled when `APP_ENV=test`).
- Responses send a Content-Security-Policy (inline theme script is hash-pinned), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, COOP, and CORP. Production also sends HSTS.
- Static CSS, JS, and SVG are cached for a day. HTML is `no-cache`. Responses are gzip-compressed.
- Production startup refuses weak secrets, SQLite, wildcard CORS, a non-HTTPS public URL, and `DEMO_MODE`.
- `DEMO_MODE` seeds clearly labeled `[Sample]` rows for UI review and is off by default.
- Audit log for login, GitHub connect/disconnect, scans, fixes, PRs, billing.
- Input validation via Pydantic.
- Static file serving does not expose `backend/`, `.env`, or key files.

## OWASP Top 10 notes

| Risk | How CodeCare handles it |
| --- | --- |
| Broken access control | Organization membership is checked on every tenant query. Owner/admin/member gates billing, GitHub writes, and scans. |
| Cryptographic failures | bcrypt passwords, Fernet for GitHub tokens, HTTPS and Secure cookies in production. |
| Injection | Parameterized SQLAlchemy queries. Repository paths reject `..`. Shell is not given customer filenames unsafely in the sandbox policy. |
| Insecure design | Pull requests require a verified sandbox result. AI output is never treated as verified. |
| Security misconfiguration | Production startup checks. CSP, HSTS, no wildcard CORS. Demo mode cannot run in production. |
| Vulnerable components | `pip-audit` is part of review. Pin runtime dependencies in `backend/requirements.txt`. |
| Identification and authentication failures | Rate limits, session revocation, CSRF, email verification, reset tokens stored as SHA-256. |
| Software and data integrity | Razorpay and GitHub webhooks verify signatures. Sandbox does not run install scripts from customer packages. |
| Security logging failures | Audit log for auth, GitHub, scans, fixes, pull requests, and billing. Logs redact secrets. |
| SSRF | GitHub calls go to `api.github.com` with server credentials. AI calls go only to the configured provider host. |

## Remaining risks

- Install-phase Docker still uses a bridge network (needed for PyPI/npm). It is not a hostname allowlist.
- Customer `npm test` / `next build` scripts are not executed (arbitrary code).
- Live GitHub, Groq, and Docker-daemon verification have not been proven on this workstation.
