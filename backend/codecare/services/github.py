from __future__ import annotations

import tarfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import jwt

from codecare.config import Settings, get_settings

GITHUB_API = "https://api.github.com"
GITHUB_ACCEPT = "application/vnd.github+json"


class GitHubError(RuntimeError):
    pass


def github_app_jwt(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if not settings.github_configured:
        raise GitHubError("GitHub App is not configured")
    now = int(time.time())
    payload = {"iat": now - 30, "exp": now + 540, "iss": settings.github_app_id}
    return jwt.encode(payload, settings.github_private_key_pem, algorithm="RS256")


async def _request(method: str, url: str, token: str, **kwargs) -> Any:
    headers = kwargs.pop("headers", {})
    headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": GITHUB_ACCEPT,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CodeCare-Maintenance",
        }
    )
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(method, url, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise GitHubError(f"GitHub API {response.status_code}: {response.text[:500]}")
        if response.status_code == 204:
            return None
        return response.json()


async def installation_token(installation_id: str, settings: Settings | None = None) -> str:
    app_token = github_app_jwt(settings)
    data = await _request(
        "POST",
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        app_token,
    )
    return data["token"]


async def get_installation(installation_id: str) -> dict:
    return await _request("GET", f"{GITHUB_API}/app/installations/{installation_id}", github_app_jwt())


async def list_installation_repos(token: str) -> list[dict]:
    repos: list[dict] = []
    page = 1
    while page <= 10:
        data = await _request("GET", f"{GITHUB_API}/installation/repositories", token, params={"per_page": 100, "page": page})
        batch = data.get("repositories") or []
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


async def exchange_oauth_code(code: str, settings: Settings | None = None, redirect_uri: str | None = None) -> dict:
    settings = settings or get_settings()
    body = {
        "client_id": settings.github_app_client_id,
        "client_secret": settings.github_app_client_secret,
        "code": code,
    }
    if redirect_uri:
        body["redirect_uri"] = redirect_uri
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            json=body,
            headers={"Accept": "application/json"},
        )
        data = response.json()
        if "access_token" not in data:
            raise GitHubError(data.get("error_description") or data.get("error") or "GitHub OAuth exchange failed")
        return data


async def refresh_oauth_token(refresh_token: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": settings.github_app_client_id,
                "client_secret": settings.github_app_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Accept": "application/json"},
        )
        data = response.json()
        if "access_token" not in data:
            raise GitHubError(data.get("error_description") or data.get("error") or "GitHub OAuth refresh failed")
        return data


async def revoke_oauth_token(access_token: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.github_app_client_id or not settings.github_app_client_secret:
        raise GitHubError("GitHub OAuth client is not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.delete(
            f"{GITHUB_API}/applications/{settings.github_app_client_id}/token",
            auth=(settings.github_app_client_id, settings.github_app_client_secret),
            json={"access_token": access_token},
            headers={"Accept": GITHUB_ACCEPT, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "CodeCare-Maintenance"},
        )
        if response.status_code not in {204, 404}:
            raise GitHubError(f"GitHub token revoke {response.status_code}")


async def github_user(token: str) -> dict:
    return await _request("GET", f"{GITHUB_API}/user", token)


async def github_emails(token: str) -> list:
    return await _request("GET", f"{GITHUB_API}/user/emails", token)


async def get_repo(token: str, full_name: str) -> dict:
    return await _request("GET", f"{GITHUB_API}/repos/{full_name}", token)


async def get_tree(token: str, full_name: str, sha: str) -> dict:
    return await _request("GET", f"{GITHUB_API}/repos/{full_name}/git/trees/{sha}", token, params={"recursive": "1"})


def safe_repo_path(path: str) -> str:
    cleaned = (path or "").replace("\\", "/").lstrip("/")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise GitHubError("Invalid repository path")
    return "/".join(parts)


async def get_file(token: str, full_name: str, path: str, ref: str) -> str | None:
    try:
        safe = safe_repo_path(path)
        data = await _request("GET", f"{GITHUB_API}/repos/{full_name}/contents/{safe}", token, params={"ref": ref})
    except GitHubError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("encoding") == "base64" and data.get("content"):
        import base64

        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return None


async def download_tarball(token: str, full_name: str, ref: str, dest: Path) -> int:
    """Fetch a GitHub archive. This copies bytes only; it does not execute repository code."""
    url = f"{GITHUB_API}/repos/{full_name}/tarball/{ref}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "CodeCare-Maintenance",
    }
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise GitHubError(f"GitHub tarball {response.status_code}")
        data = response.content
    dest.mkdir(parents=True, exist_ok=True)
    extracted = 0
    with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            parts = Path(member.name).parts[1:]  # strip the GitHub top-level folder
            if not parts or ".." in parts:
                continue
            target = dest.joinpath(*parts)
            if dest.resolve() not in target.resolve().parents and target.resolve() != dest.resolve():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            fileobj = archive.extractfile(member)
            if fileobj is None:
                continue
            target.write_bytes(fileobj.read())
            extracted += 1
            if extracted >= 5000:
                break
    return extracted


async def list_workflow_runs(token: str, full_name: str) -> list[dict]:
    try:
        data = await _request("GET", f"{GITHUB_API}/repos/{full_name}/actions/runs", token, params={"per_page": 15})
        return data.get("workflow_runs") or []
    except GitHubError:
        return []


async def create_pull_request(token: str, full_name: str, *, title: str, body: str, head: str, base: str) -> dict:
    return await _request(
        "POST",
        f"{GITHUB_API}/repos/{full_name}/pulls",
        token,
        json={"title": title, "body": body, "head": head, "base": base},
    )


async def create_branch_and_commit(
    token: str,
    full_name: str,
    *,
    base_sha: str,
    branch: str,
    message: str,
    files: dict[str, str],
) -> None:
    ref = await _request(
        "POST",
        f"{GITHUB_API}/repos/{full_name}/git/refs",
        token,
        json={"ref": f"refs/heads/{branch}", "sha": base_sha},
    )
    base_commit = await _request("GET", f"{GITHUB_API}/repos/{full_name}/git/commits/{base_sha}", token)
    tree_items = []
    for path, content in files.items():
        blob = await _request(
            "POST",
            f"{GITHUB_API}/repos/{full_name}/git/blobs",
            token,
            json={"content": content, "encoding": "utf-8"},
        )
        tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = await _request(
        "POST",
        f"{GITHUB_API}/repos/{full_name}/git/trees",
        token,
        json={"base_tree": base_commit["tree"]["sha"], "tree": tree_items},
    )
    commit = await _request(
        "POST",
        f"{GITHUB_API}/repos/{full_name}/git/commits",
        token,
        json={"message": message, "tree": tree["sha"], "parents": [base_sha]},
    )
    await _request(
        "PATCH",
        f"{GITHUB_API}/repos/{full_name}/git/refs/heads/{branch}",
        token,
        json={"sha": commit["sha"]},
    )
    return ref
