# GitHub App setup for CodeCare

Live repository access does not work until this App exists and the environment variables below are set. The workspace will show GitHub as **not configured** until then. That is intentional.

## 1. Create the App

1. GitHub → Settings → Developer settings → GitHub Apps → **New GitHub App**.
2. GitHub App name: something unique, for example `codecare-<yourname>`.
3. Homepage URL: `https://codecare.si` (local trials can use `http://127.0.0.1:8000`).
4. Callback URL: `https://codecare.si/api/v1/github/oauth/callback`  
   Sign-in and "Connect GitHub identity" share this callback. The `state` record distinguishes them. Local equivalent: `http://127.0.0.1:8000/api/v1/github/oauth/callback`.
5. Setup URL: `https://codecare.si/api/v1/github/setup`
6. Webhook URL: `https://codecare.si/api/v1/webhooks/github`
7. Webhook secret: a long random string (store as `GITHUB_WEBHOOK_SECRET`).
8. Leave **Expire user authorization tokens** enabled so GitHub issues a refresh token. CodeCare stores expiry, refreshes before use, and will not use an expired access token. Installation tokens remain short-lived and minted per request.

For local development, webhook delivery needs a public HTTPS URL (ngrok, Cloudflare Tunnel, or similar) pointing at port 8000. Installation and OAuth still work on localhost.

## 2. Permissions (minimum)

Repository permissions:

| Permission | Access | Why |
| --- | --- | --- |
| Metadata | Read | List installations |
| Contents | Read and write | Read files / open fix branches |
| Pull requests | Read and write | Open PRs after verification |
| Actions | Read | Workflow failure monitoring |
| Commit statuses | Read | Optional CI signal |

Unsubscribe from unused events. Subscribe to:

- `push`
- `workflow_run`
- `check_suite` (optional)
- `installation`
- `installation_repositories`

Where can this App be installed: **Only on this account** until you are ready for others.

## 3. Credentials

After creating the App:

- Note **App ID** → `GITHUB_APP_ID`
- Note **Slug** (the URL name) → `GITHUB_APP_SLUG`
- Generate a **private key** (PEM). Save it outside git. Set `GITHUB_APP_PRIVATE_KEY_PATH` to that file, or paste the PEM into `GITHUB_APP_PRIVATE_KEY` with `\n` for newlines.
- Create a client secret → `GITHUB_APP_CLIENT_ID` and `GITHUB_APP_CLIENT_SECRET`

Never commit the PEM or client secret.

## 4. Install

1. Start CodeCare with those variables set.
2. Sign in at `/workspace/`.
3. Integrations → **Install GitHub App**.
4. Select only the repositories CodeCare should maintain.
5. GitHub redirects to `/api/v1/github/setup?installation_id=…&state=…`.
6. Connect selected repositories from the workspace. Listing uses the **installation token**, not the user OAuth token.

User OAuth (`Connect GitHub identity`) stores encrypted access and refresh tokens plus expiry. Expired access tokens are refreshed; failed refresh marks credentials revoked. Scans, archive fetch, and PRs still use a fresh **installation token** (about 1 hour, minted per request, never persisted). `POST /api/v1/github/oauth/revoke` revokes the user token at GitHub.

Uninstall/suspend webhooks mark the installation as revoked and disconnect every repository that used it.

## 5. What still will not work without this

- Repository listing
- Scans of GitHub remotes
- Archive retrieval for sandbox verification
- Pull requests

Those paths return HTTP 503 or a workspace banner until the App is configured.
