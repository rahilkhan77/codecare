"""Run owner live checks. Prints statuses only. Never prints secret values."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
ENV_FILE = BACKEND / ".env"
sys.path.insert(0, str(BACKEND))

from codecare.config import normalize_database_url, normalize_github_app_slug  # noqa: E402
from codecare.services.email import check_resend_key  # noqa: E402


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def redact(text: str, secrets: list[str]) -> str:
    cleaned = text or ""
    for secret in secrets:
        if secret and len(secret) >= 6:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


def status_line(name: str, outcome: str, detail: str) -> None:
    print(f"{name}: {outcome} — {detail}")


def check_github_app(secrets: list[str], has_private_key: bool) -> None:
    """Mint an App JWT and, when an installation exists, list repos. No writes."""
    if not has_private_key:
        status_line("github_app_jwt", "skipped", "private key is not set")
        status_line("github_installations", "skipped", "private key is not set")
        status_line("github_installation_token", "skipped", "private key is not set")
        status_line("github_repo_list", "skipped", "private key is not set")
        return
    from codecare.config import get_settings
    from codecare.services.github import github_app_jwt

    get_settings.cache_clear()
    settings = get_settings()
    try:
        app_jwt = github_app_jwt(settings)
    except Exception as exc:
        status_line("github_app_jwt", "failed", redact(f"{exc.__class__.__name__}", secrets))
        status_line("github_installations", "skipped", "JWT was not minted")
        status_line("github_installation_token", "skipped", "JWT was not minted")
        status_line("github_repo_list", "skipped", "JWT was not minted")
        return
    status_line("github_app_jwt", "passed", "App JWT minted")
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "CodeCare-Maintenance",
    }
    try:
        listed = httpx.get(
            "https://api.github.com/app/installations",
            headers=headers,
            params={"per_page": 100},
            timeout=30,
        )
    except Exception as exc:
        status_line("github_installations", "failed", exc.__class__.__name__)
        status_line("github_installation_token", "skipped", "installation list failed")
        status_line("github_repo_list", "skipped", "installation list failed")
        return
    if listed.status_code != 200:
        status_line("github_installations", "failed", f"HTTP {listed.status_code}")
        status_line("github_installation_token", "skipped", "installation list failed")
        status_line("github_repo_list", "skipped", "installation list failed")
        return
    installations = listed.json() if isinstance(listed.json(), list) else []
    status_line("github_installations", "passed", f"{len(installations)} installation(s)")
    if not installations:
        status_line("github_installation_token", "skipped", "no installations")
        status_line("github_repo_list", "skipped", "no installations")
        return
    installation_id = installations[0].get("id")
    try:
        minted = httpx.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers=headers,
            timeout=30,
        )
    except Exception as exc:
        status_line("github_installation_token", "failed", exc.__class__.__name__)
        status_line("github_repo_list", "skipped", "installation token was not minted")
        return
    if minted.status_code != 201:
        status_line("github_installation_token", "failed", f"HTTP {minted.status_code}")
        status_line("github_repo_list", "skipped", "installation token was not minted")
        return
    access_token = str((minted.json() or {}).get("token") or "")
    status_line("github_installation_token", "passed", "token minted; no repository writes")
    repo_headers = dict(headers)
    repo_headers["Authorization"] = f"Bearer {access_token}"
    try:
        repos = httpx.get(
            "https://api.github.com/installation/repositories",
            headers=repo_headers,
            params={"per_page": 100},
            timeout=30,
        )
    except Exception as exc:
        status_line("github_repo_list", "failed", exc.__class__.__name__)
        return
    if repos.status_code != 200:
        status_line("github_repo_list", "failed", f"HTTP {repos.status_code}")
        return
    total = (repos.json() or {}).get("total_count")
    status_line("github_repo_list", "passed", f"{total} repo(s) listed; no pulls or pushes")


def main() -> int:
    env = load_env(ENV_FILE)
    secrets = [value for value in env.values() if value]
    database_url = env.get("DATABASE_URL", "")
    groq_key = env.get("GROQ_API_KEY", "")
    resend_key = env.get("RESEND_API_KEY", "")
    app_id = env.get("GITHUB_APP_ID", "")
    client_id = env.get("GITHUB_APP_CLIENT_ID", "")
    client_secret = env.get("GITHUB_APP_CLIENT_SECRET", "")
    private_key = env.get("GITHUB_APP_PRIVATE_KEY", "")
    slug = normalize_github_app_slug(env.get("GITHUB_APP_SLUG", ""))

    if not database_url:
        status_line("alembic", "skipped", "DATABASE_URL is not set")
    else:
        child_env = os.environ.copy()
        child_env["DATABASE_URL"] = normalize_database_url(database_url)
        child_env["PYTHONPATH"] = str(BACKEND)
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND,
            env=child_env,
            capture_output=True,
            text=True,
        )
        detail = redact((completed.stderr or completed.stdout or "").strip().splitlines()[-1] if (completed.stderr or completed.stdout) else "", secrets)
        if completed.returncode == 0:
            status_line("alembic", "passed", "upgrade head")
        else:
            status_line("alembic", "failed", detail or f"exit {completed.returncode}")

    if not groq_key:
        status_line("groq", "skipped", "GROQ_API_KEY is not set")
    else:
        try:
            model = env.get("GROQ_MODEL") or "openai/gpt-oss-120b"
            response = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with the single word ok."}],
                    "max_tokens": 16,
                },
                timeout=60,
            )
            if response.status_code == 200:
                status_line("groq", "passed", f"chat completion with {model} returned HTTP 200")
            else:
                status_line("groq", "failed", f"{model} returned HTTP {response.status_code}")
        except Exception as exc:
            status_line("groq", "failed", redact(exc.__class__.__name__, secrets))

    if not resend_key:
        status_line("resend", "skipped", "RESEND_API_KEY is not set")
    else:
        try:
            code = check_resend_key(resend_key)
            if code == 200:
                status_line("resend", "passed", "key accepted by GET /domains; no email sent")
            else:
                status_line("resend", "failed", f"GET /domains returned HTTP {code}; no email sent")
        except Exception as exc:
            status_line("resend", "failed", redact(exc.__class__.__name__, secrets))

    check_github_app(secrets, bool(private_key.strip() or env.get("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()))

    if not app_id and not client_id:
        status_line("github_app", "skipped", "GitHub App id and client id are not set")
    else:
        try:
            page = httpx.get(f"https://github.com/apps/{slug}", timeout=20, follow_redirects=True)
            if page.status_code == 200:
                status_line("github_app_page", "passed", "public app page returned HTTP 200")
            else:
                status_line("github_app_page", "failed", f"HTTP {page.status_code}")
        except Exception as exc:
            status_line("github_app_page", "failed", exc.__class__.__name__)
        if client_id and client_secret:
            try:
                token = httpx.post(
                    "https://github.com/login/oauth/access_token",
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "code": "invalid-live-check",
                        "redirect_uri": "https://codecare.si/api/v1/github/oauth/callback",
                    },
                    timeout=20,
                )
                payload = token.json() if "json" in token.headers.get("content-type", "") else {}
                error = str(payload.get("error") or "")
                if error == "incorrect_client_credentials":
                    status_line("github_oauth_client", "failed", "GitHub rejected the client credentials")
                elif error in {"bad_verification_code", "redirect_uri_mismatch"}:
                    status_line("github_oauth_client", "passed", "client credentials accepted; no token issued")
                    if error == "redirect_uri_mismatch":
                        status_line(
                            "github_oauth_callback",
                            "failed",
                            "registered callback does not match https://codecare.si/api/v1/github/oauth/callback",
                        )
                    else:
                        status_line("github_oauth_callback", "passed", "callback URL was accepted")
                else:
                    status_line("github_oauth_client", "failed", f"HTTP {token.status_code}")
            except Exception as exc:
                status_line("github_oauth_client", "failed", exc.__class__.__name__)
        else:
            status_line("github_oauth_client", "skipped", "client id or client secret is not set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
