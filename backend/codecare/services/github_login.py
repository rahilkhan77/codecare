from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.models import Organization, OrganizationMember, Profile, new_id, utcnow
from codecare.security import apply_session_cookie
from codecare.services import github as gh
from codecare.services.audit import audit
from codecare.services.orgs import bootstrap_org
from codecare.services.sessions import issue_session


def _verified_email(emails: object) -> str:
    if not isinstance(emails, list):
        return ""
    verified = [item for item in emails if isinstance(item, dict) and item.get("verified") and item.get("email")]
    primary = next((item for item in verified if item.get("primary")), None)
    chosen = primary or (verified[0] if verified else None)
    return str(chosen["email"]).lower() if chosen else ""


async def finish_github_sign_in(db: Session, token_data: dict) -> RedirectResponse:
    settings = get_settings()
    access = token_data.get("access_token")
    if not access:
        raise HTTPException(status_code=401, detail="GitHub did not return an access token")
    try:
        user = await gh.github_user(access)
        emails = await gh.github_emails(access)
    except gh.GitHubError as exc:
        raise HTTPException(status_code=401, detail="GitHub rejected the sign-in token") from exc
    email = _verified_email(emails)
    github_id = str(user.get("id") or "")
    if not email or not github_id:
        raise HTTPException(
            status_code=400,
            detail="GitHub did not return a verified email. Authorize the user:email scope and try again.",
        )
    profile = db.scalar(select(Profile).where(Profile.github_user_id == github_id))
    if profile is None:
        profile = db.scalar(select(Profile).where(Profile.email == email))
        if profile and profile.github_user_id and profile.github_user_id != github_id:
            raise HTTPException(status_code=409, detail="That email is already linked to a different GitHub account")
        if profile:
            profile.github_user_id = github_id
            profile.email_verified_at = profile.email_verified_at or utcnow()
    if profile is None:
        profile = Profile(
            id=new_id(),
            email=email,
            full_name=(user.get("name") or user.get("login") or email.split("@")[0])[:200],
            github_user_id=github_id,
            email_verified_at=utcnow(),
            avatar_url=str(user.get("avatar_url") or "")[:500],
        )
        db.add(profile)
        org = bootstrap_org(db, profile)
    else:
        member = db.scalar(
            select(OrganizationMember)
            .where(OrganizationMember.profile_id == profile.id)
            .order_by(OrganizationMember.created_at)
        )
        if member:
            org = db.get(Organization, member.organization_id)
        else:
            org = bootstrap_org(db, profile)
    if org is None:
        raise HTTPException(status_code=500, detail="Workspace could not be created")
    token = issue_session(db, profile.id, org.id, settings)
    audit(db, action="auth.github_sign_in", organization_id=org.id, profile_id=profile.id)
    db.commit()
    response = RedirectResponse(settings.public_base_url.rstrip("/") + "/workspace/", status_code=303)
    apply_session_cookie(response, token, settings)
    return response
