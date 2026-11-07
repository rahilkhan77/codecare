# API specification

Base path: `/api/v1`  
Auth: httpOnly cookie `codecare_session` (server-backed session JWT) unless noted. State-changing requests also send `X-CSRF-Token` matching the `codecare_csrf` cookie. Webhooks are exempt and use their own signatures.

All error bodies: `{ "detail": "..." }`.

## Public

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | Liveness and integration flags (booleans only, no secrets) |
| POST | `/api/v1/contact` | Stores a contact message; rate limited |
| GET | `/api/v1/auth/csrf` | Sets the readable `codecare_csrf` cookie and returns the token |
| GET | `/api/v1/auth/providers` | Whether password, GitHub OAuth, and email delivery are configured |
| POST | `/api/v1/auth/register` | Email + password. Sends a verification link when delivery is available |
| POST | `/api/v1/auth/login` | Sets the httpOnly `codecare_session` cookie |
| POST | `/api/v1/auth/logout` | Revokes the server session and clears the cookie |
| POST | `/api/v1/auth/refresh` | Rotates the current session |
| POST | `/api/v1/auth/forgot-password` | Always the same shape. Does not claim mail was sent when SMTP is unavailable |
| POST | `/api/v1/auth/reset-password` | Consumes a reset token and revokes sessions |
| POST | `/api/v1/auth/verify-email` | Consumes an email verification token |
| POST | `/api/v1/auth/resend-verification` | Signed-in resend |
| POST | `/api/v1/auth/change-password` | Current password required when one is set |
| POST | `/api/v1/auth/delete-account` | Requires confirmation `DELETE` and the password when one exists |
| POST | `/api/v1/auth/switch-organization` | Session for another membership |
| POST | `/api/v1/auth/accept-invite` | Accept an invite for the signed-in email |
| GET | `/api/v1/auth/github/start` | 503 until GitHub OAuth client credentials exist |
| POST | `/api/v1/auth/supabase` | Exchange a Supabase access token when configured |
| GET | `/api/v1/auth/me` | Profile, role, email verification, and organizations |

State-changing `/api/v1` requests, except signed webhooks, require header `X-CSRF-Token` equal to the `codecare_csrf` cookie.

Roles: `owner` and `admin` can connect repositories, run scans, propose and verify fixes, manage billing, and invite members. `member` is read-only for those actions. Only an `owner` can change roles. Deleting an account that still owns a workspace with other members returns 409.

## Organization

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/org/members` | Members of the current workspace |
| POST | `/api/v1/org/members` | Add an existing account or email an invite |
| PATCH | `/api/v1/org/members/{id}` | Owner changes a role |
| DELETE | `/api/v1/org/members/{id}` | Owner or admin removes a member |

## Billing catalog

| Method | Path | Notes |
| --- | --- | --- |
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
| POST | `/api/v1/billing/checkout` | Creates a Dodo hosted checkout session and returns `url`. 503 if keys are missing. Demo mode records a local plan and says Dodo Payments was not charged |
| POST | `/api/v1/billing/portal` | Returns a Dodo customer portal link. 503 if keys are missing. 400 in demo mode |
| POST | `/api/v1/billing/change-plan` | Dodo change-plan API. Upgrades apply immediately. Downgrades apply on the next billing date |
| POST | `/api/v1/billing/cancel` | Sets cancel at the next billing date. Access stays on until that date |
| POST | `/api/v1/webhooks/dodo` | Verifies Standard Webhooks headers `webhook-id`, `webhook-signature`, and `webhook-timestamp`. Idempotent on `webhook-id` |

Plan catalog, in USD: Starter $29 (1 app), Indie $79 (5 apps), Studio $199 (unlimited). Dodo Payments is the merchant of record. Checkout stays disabled until `DODO_PAYMENTS_API_KEY` and `DODO_PRODUCT_STARTER` are set. Return URL: `https://codecare.si/workspace`.
