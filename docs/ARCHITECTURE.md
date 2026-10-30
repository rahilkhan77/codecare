# CodeCare architecture

Status: implemented as an MVP platform around the existing static marketing site. Capabilities that depend on external credentials stay disabled until those credentials exist. The product never reports a successful GitHub, AI, Stripe, or sandbox integration unless that system is actually configured and the operation succeeded.

## Existing frontend (unchanged product)

The GitHub repository was a static website:

| Path | Role |
| --- | --- |
| `index.html` | Marketing home, including an illustrative dashboard preview |
| `how-it-works/`, `pricing/`, `about/`, `contact/` | Product pages |
| Legal pages | Draft policies with placeholders |
| `styles.css`, `theme.css`, `pages.css`, `app.js` | Visual identity, theme toggle, contact validation |

There was no application framework, no authentication, no database, and no API. Dashboard numbers on the homepage remain illustrative marketing visuals.

## Runtime shape

```
Browser
  ├── Marketing site (existing HTML/CSS/JS)
  └── /workspace  (authenticated product UI, same visual language)
        │
        ▼
FastAPI  (/api/v1)
  ├── Auth (local sessions; optional Supabase JWT)
  ├── PostgreSQL (SQLAlchemy + Alembic)
  ├── GitHub App / OAuth (server-side tokens only)
  ├── Scan / issue / fix / PR pipeline
  ├── AI provider abstraction
  ├── Docker sandbox (never host execution of customer code)
  ├── Redis job queue when configured; in-process worker otherwise
  └── Stripe (disabled until keys are present)
```

PostgreSQL is the system of record. Redis is optional. Docker is required for verification of untrusted repository code; if Docker is unavailable, verification is marked `inconclusive` and no pull request is opened automatically.

## Modules

| Module | Responsibility |
| --- | --- |
| Authentication | Register/login, httpOnly JWT cookie, optional Supabase JWT exchange |
| Users and organizations | Personal organization on signup, membership, tenant isolation |
| GitHub | App install, installation tokens, repo selection, webhooks |
| Scanning | Tree, languages, manifests, OSV vulns, outdated packages, workflow failures, static findings |
| Issues | Detected findings with status and ownership |
| AI engine | Provider interface: Groq, OpenAI-compatible, Anthropic, heuristic fallback |
| Fix generation | Structured patch proposals; never assumed correct |
| Sandbox | Isolated `docker run` with CPU/memory/time/network limits |
| Verification | `verified` / `failed` / `inconclusive` only from real check results |
| Pull requests | Created only after successful verification (or explicit unsafe override is not offered) |
| Monitoring | Scan history, issue states, GitHub workflow failures, dependency health |
| Billing | Stripe Checkout + webhooks; entitlements from server-side subscription state |
| Notifications | In-app records for scan/fix/PR/billing events |
| Audit | Security-relevant actions |

## Trust boundaries

- Access tokens, GitHub App private keys, and Stripe secrets never leave the API process.
- Tenant queries are always scoped by organization membership.
- Untrusted repository code runs only in the sandbox image, with `--network none`.
- AI output is a proposal. Verification is a separate, non-AI step.
- Billing access is derived from `subscriptions` rows written by Stripe webhooks, not from the frontend.

## What this MVP does not claim

- Production/runtime APM or live HTTP monitoring of customer apps
- Automatic merge or deploy
- Purchasable plans until Stripe keys and price IDs are configured
- That an AI fix is correct because a model said so
