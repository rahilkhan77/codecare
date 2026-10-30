from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.db import get_db
from codecare.deps import AuthContext, current_auth
from codecare.models import Organization, OrganizationMember, Profile, Subscription, new_id
from codecare.schemas import LoginRequest, RegisterRequest, SupabaseAuthRequest
from codecare.security import create_session_token, decode_supabase_token, hash_password, verify_password
from codecare.services.audit import audit

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    slug = SLUG_RE.sub("-", value.lower()).strip("-")
    return (slug or "workspace")[:60]


def _set_session(response: Response, profile_id: str, organization_id: str) -> None:
    settings = get_settings()
    token = create_session_token(profile_id, organization_id, settings)
    response.set_cookie(
        settings.cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
        max_age=settings.session_hours * 3600,
        path="/",
    )


def _bootstrap_org(db: Session, profile: Profile) -> Organization:
    org = Organization(id=new_id(), name=f"{profile.full_name or profile.email}'s workspace", slug=_slug(profile.email.split("@")[0]) + "-" + profile.id[:8], owner_id=profile.id)
    db.add(org)
    db.add(OrganizationMember(id=new_id(), organization_id=org.id, profile_id=profile.id, role="owner"))
    db.add(Subscription(id=new_id(), organization_id=org.id, plan="none", status="inactive"))
    return org


@router.post("/register")
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    existing = db.scalar(select(Profile).where(Profile.email == payload.email.lower()))
    if existing:
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    profile = Profile(
        id=new_id(),
        email=payload.email.lower(),
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
    )
    db.add(profile)
    org = _bootstrap_org(db, profile)
    audit(db, action="auth.register", organization_id=org.id, profile_id=profile.id)
    db.commit()
    _set_session(response, profile.id, org.id)
    return {"id": profile.id, "email": profile.email, "organization_id": org.id}


@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    profile = db.scalar(select(Profile).where(Profile.email == payload.email.lower()))
    if not profile or not profile.password_hash or not verify_password(payload.password, profile.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    member = db.scalar(select(OrganizationMember).where(OrganizationMember.profile_id == profile.id))
    if not member:
        raise HTTPException(status_code=403, detail="No workspace membership")
    audit(db, action="auth.login", organization_id=member.organization_id, profile_id=profile.id)
    db.commit()
    _set_session(response, profile.id, member.organization_id)
    return {"id": profile.id, "email": profile.email, "organization_id": member.organization_id}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(get_settings().cookie_name, path="/")
    return {"ok": True}


@router.post("/supabase")
def supabase_exchange(payload: SupabaseAuthRequest, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.supabase_configured:
        raise HTTPException(status_code=503, detail="Supabase authentication is not configured")
    try:
        claims = decode_supabase_token(payload.access_token, settings)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Supabase token")
    sub = str(claims.get("sub") or "")
    email = str(claims.get("email") or "").lower()
    if not sub or not email:
        raise HTTPException(status_code=401, detail="Supabase token missing email")
    profile = db.scalar(select(Profile).where(Profile.supabase_user_id == sub))
    if not profile:
        profile = db.scalar(select(Profile).where(Profile.email == email))
    if not profile:
        profile = Profile(id=new_id(), email=email, full_name=email.split("@")[0], supabase_user_id=sub)
        db.add(profile)
        org = _bootstrap_org(db, profile)
    else:
        profile.supabase_user_id = sub
        member = db.scalar(select(OrganizationMember).where(OrganizationMember.profile_id == profile.id))
        org = db.get(Organization, member.organization_id) if member else _bootstrap_org(db, profile)
    db.commit()
    _set_session(response, profile.id, org.id)
    return {"id": profile.id, "email": profile.email, "organization_id": org.id}


@router.get("/me")
def me(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    org = db.get(Organization, auth.organization_id)
    return {
        "id": auth.profile.id,
        "email": auth.profile.email,
        "full_name": auth.profile.full_name,
        "organization": {"id": org.id, "name": org.name, "slug": org.slug} if org else None,
        "role": auth.role,
    }
