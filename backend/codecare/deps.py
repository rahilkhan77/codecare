from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Cookie, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.db import get_db
from codecare.models import AuthSession, OrganizationMember, Profile
from codecare.security import apply_session_cookie, decode_session_token
from codecare.services.sessions import as_utc, maybe_extend_session


class AuthContext:
    def __init__(self, profile: Profile, organization_id: str, role: str, session_id: str):
        self.profile = profile
        self.organization_id = organization_id
        self.role = role
        self.session_id = session_id


def current_auth(
    response: Response,
    db: Session = Depends(get_db),
    codecare_session: str | None = Cookie(default=None, alias=get_settings().cookie_name),
) -> AuthContext:
    if not codecare_session:
        raise HTTPException(status_code=401, detail="Sign in required")
    try:
        payload = decode_session_token(codecare_session)
    except Exception:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    session_id = payload.get("sid")
    session_row = db.get(AuthSession, session_id) if session_id else None
    if not session_row or session_row.revoked_at is not None or as_utc(session_row.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    if session_row.profile_id != payload.get("sub") or session_row.organization_id != payload.get("org"):
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    profile = db.get(Profile, payload.get("sub"))
    if not profile:
        raise HTTPException(status_code=401, detail="Account not found")
    org_id = payload.get("org")
    member = db.scalar(
        select(OrganizationMember).where(
            OrganizationMember.profile_id == profile.id,
            OrganizationMember.organization_id == org_id,
        )
    )
    if not member:
        raise HTTPException(status_code=403, detail="No organization membership")
    refreshed = maybe_extend_session(db, session_row)
    if refreshed:
        db.commit()
        apply_session_cookie(response, refreshed)
    return AuthContext(profile, org_id, member.role, session_row.id)


def require_roles(*roles: str):
    def dependency(auth: AuthContext = Depends(current_auth)) -> AuthContext:
        if auth.role not in roles:
            raise HTTPException(status_code=403, detail="You do not have permission for this action")
        return auth

    return dependency


def org_scoped(model, item_id: str, auth: AuthContext, db: Session):
    row = db.get(model, item_id)
    if not row or getattr(row, "organization_id", None) != auth.organization_id:
        raise HTTPException(status_code=404, detail="Not found")
    return row
