# CodeCare integration status

This document records what was actually exercised. It is not a production-ready claim.

## Credentials on this machine

| Secret / runtime | Present |
| --- | --- |
| GitHub App (`GITHUB_APP_ID` + private key) | No |
| Groq / OpenAI / Anthropic | No |
| Dodo Payments | No |
| Docker CLI | Yes |
| Docker engine | Not running when last checked |

## Automated tests

The backend pytest suite is the verified surface. Live Docker image builds, live GitHub OAuth, and live Groq calls are **not** claimed here.

## What works in tests / local code paths

- Auth, tenant isolation, unauthorized repo connect rejected
- Webhook HMAC, duplicate delivery, uninstall disconnect + OAuth wipe
- OAuth expiry tracking, refresh handling, revoked/invalid rejection (mocked GitHub)
- Fernet required in production; no `APP_SECRET` derivation; previous-key rotation
- Local scan of `backend/samples/maintenance-fixture`
- Heuristic AI fallback that does **not** invent patches
- Sandbox policy: patch-must-apply, pytest must collect and pass, secrets redacted in logs, timeouts not verified, `npm ci --ignore-scripts`, pip wheels-only
- Workspace views read live API data

## Implemented but not live-proven

- GitHub App install, listing, tarball, PR
- Real refresh against GitHub
- Groq diagnosis and patches
- Docker sandbox against a real engine/image
- npm ci / pip install inside Docker

## Remaining limitations

- Install-phase network is Docker bridge, not a registry allowlist
- `npm test` / `next build` are not executed
- End-to-end GitHub → AI → sandbox → PR has not succeeded on a real remote
