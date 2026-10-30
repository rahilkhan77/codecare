# API specification

Base path: `/api/v1`  
Auth: httpOnly cookie `codecare_session` (JWT) unless noted. CSRF is mitigated with `SameSite=Lax` cookies on same-origin workspace pages.

All error bodies: `{ "detail": "..." }`.

## Public

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | Liveness and integration flags (booleans only, no secrets) |
| POST | `/api/v1/contact` | Stores a contact message; rate limited |
| POST | `/api/v1/auth/register` | Email + password |
| POST | `/api/v1/auth/login` | Sets session cookie |
| POST | `/api/v1/auth/logout` | Clears cookie |
| POST | `/api/v1/auth/supabase` | Exchange a Supabase access token when configured |
| GET | `/api/v1/auth/me` | Current profile + org |
| GET | `/api/v1/billing/plans` | Proposed plan catalog and whether checkout is enabled |

## Workspace

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/workspace/overview` | Health summary from real repository/scan data |
| GET | `/api/v1/notifications` | In-app notifications |
| POST | `/api/v1/notifications/{id}/read` | Mark read |
| GET | `/api/v1/settings` | Account + integration status |
| PATCH | `/api/v1/settings` | Display name |

## GitHub

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/github/status` | Connected / not configured (never invents a connection) |
| GET | `/api/v1/github/install-url` | GitHub App installation URL |
| GET | `/api/v1/github/oauth/start` | User-to-server OAuth (identity) |
| GET | `/api/v1/github/oauth/callback` | OAuth return |
| GET | `/api/v1/github/setup` | App setup callback (`installation_id`) |
| GET | `/api/v1/github/repos/available` | Repos visible to the installation |
| POST | `/api/v1/github/repos/connect` | Connect selected repos |
| POST | `/api/v1/github/repos/{id}/disconnect` | Disconnect |
| POST | `/api/v1/webhooks/github` | Signature + delivery-id replay protection |

## Repositories, scans, issues, fixes

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/repos` | Connected repositories |
| GET | `/api/v1/repos/{id}` | Health + latest scan |
| POST | `/api/v1/repos/{id}/scans` | Enqueue a scan |
| GET | `/api/v1/repos/{id}/scans` | Scan history |
| GET | `/api/v1/scans/{id}` | Scan detail |
| GET | `/api/v1/issues` | Filter by repo/status |
| GET | `/api/v1/issues/{id}` | Investigation payload |
| POST | `/api/v1/issues/{id}/diagnose` | AI/heuristic diagnosis |
| POST | `/api/v1/issues/{id}/propose-fix` | Generate a patch proposal |
| GET | `/api/v1/fixes/{id}` | Proposal + verification |
| POST | `/api/v1/fixes/{id}/verify` | Sandbox verification |
| POST | `/api/v1/fixes/{id}/pull-request` | Open PR only if verified |
| GET | `/api/v1/pull-requests` | PR history |
| GET | `/api/v1/jobs/{id}` | Background job status |

## Billing

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/billing/status` | Server-side subscription |
| POST | `/api/v1/billing/checkout` | 503 if Stripe is not configured |
| POST | `/api/v1/webhooks/stripe` | Signature verification |

Plan catalog (proposed): Starter $19, Pro $49, Team $149, Enterprise custom.
