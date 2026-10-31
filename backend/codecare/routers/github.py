from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.db import get_db
from codecare.deps import AuthContext, current_auth, org_scoped, require_roles
from codecare.models import GithubInstallation, GithubWebhookEvent, OauthState, Repository, RepositoryScan, new_id, utcnow
from codecare.schemas import ConnectReposRequest
from codecare.security import verify_github_signature
from codecare.services import github as gh
from codecare.services.audit import audit
from codecare.services.entitlements import assert_repo_quota
from codecare.services.jobs import enqueue, has_active_job
from codecare.services.github_login import finish_github_sign_in
from codecare.services.oauth import apply_oauth_payload, clear_oauth_credentials, oauth_snapshot, revoke_and_clear

router = APIRouter(tags=["github"])


@router.get("/api/v1/github/status")
def github_status(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    settings = get_settings()
    installation = db.scalar(select(GithubInstallation).where(GithubInstallation.organization_id == auth.organization_id))
    iid = str(installation.installation_id) if installation else ""
    sample = bool(installation and (installation.token_status == "sample" or iid.startswith("sample-")))
    connected = bool(installation and not sample and not iid.startswith("pending-") and not iid.startswith("revoked-"))
    return {
        "configured": settings.github_configured,
        "demo_mode": settings.demo_mode,
        "sample": sample,
        "connected": connected,
        "account_login": installation.account_login if installation else None,
        "github_login": installation.github_login if installation else None,
        "oauth": oauth_snapshot(installation),
    }


@router.post("/api/v1/github/repos/connect-sample")
def connect_sample(auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Sample repositories are only available in demo mode")
    repo = db.scalar(
        select(Repository).where(Repository.organization_id == auth.organization_id, Repository.github_repo_id == "sample-demo")
    )
    if repo:
        repo.connected = True
        db.commit()
        return {"id": repo.id, "full_name": repo.full_name, "sample": True}
    assert_repo_quota(db, auth.organization_id, 1)
    installation = GithubInstallation(
        id=new_id(),
        organization_id=auth.organization_id,
        installation_id=f"sample-{auth.organization_id[:8]}",
        account_login="sample",
        account_type="Organization",
        token_status="sample",
    )
    db.add(installation)
    db.flush()
    repo = Repository(
        id=new_id(),
        organization_id=auth.organization_id,
        installation_id=installation.id,
        github_repo_id="sample-demo",
        full_name="sample/maintenance-fixture",
        default_branch="main",
        private=False,
        language="Python",
        connected=True,
        health_status="unknown",
    )
    db.add(repo)
    db.commit()
    return {"id": repo.id, "full_name": repo.full_name, "sample": True}


@router.get("/api/v1/github/install-url")
def install_url(auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.github_configured:
        raise HTTPException(status_code=503, detail="GitHub App is not configured on the server")
    state = secrets.token_urlsafe(24)
    db.add(OauthState(id=new_id(), state=state, profile_id=auth.profile.id, organization_id=auth.organization_id))
    db.commit()
    slug = settings.github_app_slug or "codecare-ai"
    url = f"https://github.com/apps/{slug}/installations/new?state={state}"
    return {"url": url}


@router.get("/api/v1/github/oauth/start")
def oauth_start(auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.github_app_client_id:
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
    state = secrets.token_urlsafe(24)
    db.add(OauthState(id=new_id(), state=state, profile_id=auth.profile.id, organization_id=auth.organization_id))
    db.commit()
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_app_client_id}"
        f"&scope={settings.github_oauth_scopes}"
        f"&state={state}"
    )
    return {"url": url}


def _consume_state(db: Session, state: str) -> OauthState:
    row = db.scalar(select(OauthState).where(OauthState.state == state))
    if not row:
        raise HTTPException(status_code=400, detail="Unknown OAuth state")
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
    if created < datetime.now(timezone.utc) - timedelta(minutes=15):
        raise HTTPException(status_code=400, detail="OAuth state expired")
    db.delete(row)
    return row


@router.get("/api/v1/github/oauth/callback")
async def oauth_callback(code: str, state: str, db: Session = Depends(get_db)):
    row = _consume_state(db, state)
    settings = get_settings()
    redirect_uri = None
    if row.purpose == "login":
        redirect_uri = settings.public_base_url.rstrip("/") + "/api/v1/github/oauth/callback"
    token_data = await gh.exchange_oauth_code(code, redirect_uri=redirect_uri)
    if row.purpose == "login":
        return await finish_github_sign_in(db, token_data)
    try:
        user = await gh.github_user(token_data["access_token"])
    except gh.GitHubError as exc:
        raise HTTPException(status_code=401, detail="GitHub rejected the OAuth token") from exc
    installation = db.scalar(select(GithubInstallation).where(GithubInstallation.organization_id == row.organization_id))
    if not installation:
        installation = GithubInstallation(id=new_id(), organization_id=row.organization_id, installation_id=f"pending-{row.organization_id[:8]}")
        db.add(installation)
    apply_oauth_payload(installation, token_data)
    installation.github_user_id = str(user.get("id"))
    installation.github_login = user.get("login")
    audit(db, action="github.oauth", organization_id=row.organization_id, profile_id=row.profile_id)
    db.commit()
    return RedirectResponse(get_settings().public_base_url + "/workspace/?github=oauth")


@router.get("/api/v1/github/setup")
async def github_setup(installation_id: str, state: str | None = None, setup_action: str | None = None, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.github_configured:
        raise HTTPException(status_code=503, detail="GitHub App is not configured")
    if not state:
        raise HTTPException(status_code=400, detail="Missing state")
    row = _consume_state(db, state)
    info = await gh.get_installation(installation_id)
    account = info.get("account") or {}
    existing = db.scalar(select(GithubInstallation).where(GithubInstallation.organization_id == row.organization_id))
    if existing:
        existing.installation_id = str(installation_id)
        existing.account_login = account.get("login") or ""
        existing.account_type = account.get("type") or "User"
        installation = existing
    else:
        installation = GithubInstallation(
            id=new_id(),
            organization_id=row.organization_id,
            installation_id=str(installation_id),
            account_login=account.get("login") or "",
            account_type=account.get("type") or "User",
        )
        db.add(installation)
    audit(
        db,
        action="github.install",
        organization_id=row.organization_id,
        profile_id=row.profile_id,
        metadata={"setup_action": setup_action, "installation_id": installation_id},
    )
    db.commit()
    return RedirectResponse(settings.public_base_url + "/workspace/?github=installed")


@router.get("/api/v1/github/repos/available")
async def available_repos(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    installation = db.scalar(select(GithubInstallation).where(GithubInstallation.organization_id == auth.organization_id))
    if installation and (installation.token_status == "sample" or str(installation.installation_id).startswith("sample-")):
        return []
    if not installation or installation.installation_id.startswith("pending-"):
        raise HTTPException(status_code=400, detail="Install the GitHub App before listing repositories")
    token = await gh.installation_token(installation.installation_id)
    repos = await gh.list_installation_repos(token)
    return [
        {
            "github_repo_id": str(item["id"]),
            "full_name": item["full_name"],
            "private": item.get("private", True),
            "default_branch": item.get("default_branch") or "main",
            "language": item.get("language") or "",
        }
        for item in repos
    ]


@router.post("/api/v1/github/repos/connect")
async def connect_repos(payload: ConnectReposRequest, auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    installation = db.scalar(select(GithubInstallation).where(GithubInstallation.organization_id == auth.organization_id))
    if not installation or installation.installation_id.startswith("pending-"):
        raise HTTPException(status_code=400, detail="Install the GitHub App first")
    token = await gh.installation_token(installation.installation_id)
    visible = {str(item["id"]): item for item in await gh.list_installation_repos(token)}
    created = []
    for repo_id in payload.github_repo_ids:
        remote = visible.get(repo_id)
        if not remote:
            raise HTTPException(status_code=403, detail=f"Repository {repo_id} is not in this GitHub installation")
        existing = db.scalar(
            select(Repository).where(
                Repository.organization_id == auth.organization_id,
                Repository.github_repo_id == repo_id,
            )
        )
        if existing:
            if not existing.connected:
                assert_repo_quota(db, auth.organization_id, 1)
            existing.connected = True
            existing.full_name = remote["full_name"]
            existing.default_branch = remote.get("default_branch") or "main"
            existing.installation_id = installation.id
            created.append(existing)
            continue
        assert_repo_quota(db, auth.organization_id, 1)
        repo = Repository(
            id=new_id(),
            organization_id=auth.organization_id,
            installation_id=installation.id,
            github_repo_id=repo_id,
            full_name=remote["full_name"],
            default_branch=remote.get("default_branch") or "main",
            private=bool(remote.get("private", True)),
            language=remote.get("language") or "",
            connected=True,
            health_status="unknown",
        )
        db.add(repo)
        created.append(repo)
    audit(db, action="github.repos.connect", organization_id=auth.organization_id, profile_id=auth.profile.id)
    db.commit()
    return [{"id": repo.id, "full_name": repo.full_name} for repo in created]


@router.post("/api/v1/github/oauth/revoke")
async def oauth_revoke(auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    installation = db.scalar(select(GithubInstallation).where(GithubInstallation.organization_id == auth.organization_id))
    if not installation or not installation.encrypted_user_token:
        raise HTTPException(status_code=400, detail="No GitHub user token is stored")
    await revoke_and_clear(db, installation)
    audit(db, action="github.oauth.revoke", organization_id=auth.organization_id, profile_id=auth.profile.id)
    db.commit()
    return {"ok": True, "oauth": oauth_snapshot(installation)}


@router.post("/api/v1/github/repos/{repo_id}/disconnect")
def disconnect_repo(repo_id: str, auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    repo = org_scoped(Repository, repo_id, auth, db)
    repo.connected = False
    audit(db, action="github.repos.disconnect", organization_id=auth.organization_id, profile_id=auth.profile.id, target_id=repo.id)
    db.commit()
    return {"ok": True}


@router.post("/api/v1/webhooks/github")
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    delivery = request.headers.get("X-GitHub-Delivery", "")
    event = request.headers.get("X-GitHub-Event", "")
    if not settings.github_webhook_secret:
        raise HTTPException(status_code=503, detail="GitHub webhook secret is not configured")
    if not delivery:
        raise HTTPException(status_code=400, detail="Missing delivery id")
    if not verify_github_signature(settings.github_webhook_secret, body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    existing = db.scalar(select(GithubWebhookEvent).where(GithubWebhookEvent.delivery_id == delivery))
    if existing:
        return {"ok": True, "duplicate": True}
    db.add(GithubWebhookEvent(id=new_id(), delivery_id=delivery, event=event, processed=True))
    payload = json.loads(body.decode() or "{}")
    action = payload.get("action")
    if event == "installation" and action in {"deleted", "suspend"}:
        inst_id = str((payload.get("installation") or {}).get("id") or "")
        installation = db.scalar(select(GithubInstallation).where(GithubInstallation.installation_id == inst_id)) if inst_id else None
        if installation:
            for repo in db.scalars(select(Repository).where(Repository.installation_id == installation.id)).all():
                repo.connected = False
            installation.installation_id = f"revoked-{inst_id}"[:32]
            clear_oauth_credentials(installation, "revoked")
            audit(db, action="github.uninstall", organization_id=installation.organization_id, metadata={"installation_id": inst_id, "event_action": action})
    if event in {"push", "workflow_run", "check_suite", "installation_repositories"}:
        repo_full = ((payload.get("repository") or {}).get("full_name")) or ""
        repos = db.scalars(select(Repository).where(Repository.full_name == repo_full, Repository.connected.is_(True))).all()
        for repo in repos:
            if has_active_job(db, repository_id=repo.id, kind="scan"):
                continue
            row = RepositoryScan(
                id=new_id(),
                organization_id=repo.organization_id,
                repository_id=repo.id,
                status="queued",
                trigger=event,
            )
            db.add(row)
            db.flush()
            enqueue(db, organization_id=repo.organization_id, kind="scan", repository_id=repo.id, payload={"scan_id": row.id})
    db.commit()
    return {"ok": True, "event": event, "action": action}
