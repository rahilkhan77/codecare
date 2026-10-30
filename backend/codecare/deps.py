from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.db import get_db
from codecare.models import OrganizationMember, Profile
from codecare.security import decode_session_token


class AuthContext:
    def __init__(self, profile: Profile, organization_id: str, role: str):
        self.profile = profile
        self.organization_id = organization_id
        self.role = role


def current_auth(
    db: Session = Depends(get_db),
    codecare_session: str | None = Cookie(default=None, alias=get_settings().cookie_name),
) -> AuthContext:
    if not codecare_session:
        raise HTTPException(status_code=401, detail="Sign in required")
    try:
        payload = decode_session_token(codecare_session)
    except Exception:
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
    return AuthContext(profile, org_id, member.role)


def org_scoped(model, item_id: str, auth: AuthContext, db: Session):
    row = db.get(model, item_id)
    if not row or getattr(row, "organization_id", None) != auth.organization_id:
        raise HTTPException(status_code=404, detail="Not found")
    return row
