from __future__ import annotations

import secrets

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.db import get_db
from codecare.deps import AuthContext, current_auth
from codecare.models import OauthState, Organization, OrganizationInvite, OrganizationMember, Profile, new_id, utcnow
from codecare.schemas import (
    AcceptInviteRequest,
    ChangePasswordRequest,
    DeleteAccountRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SupabaseAuthRequest,
    SwitchOrganizationRequest,
    VerifyEmailRequest,
)
from codecare.security import (
    apply_csrf_cookie,
    apply_session_cookie,
    clear_session_cookie,
    decode_session_token,
    decode_supabase_token,
    hash_password,
    verify_password,
)
from codecare.services.accounts import AccountGuardError, delete_account
from codecare.services.audit import audit
from codecare.services.email import OutboundEmail, deliver, delivery_message, delivery_mode
from codecare.services.orgs import bootstrap_org
from codecare.services.sessions import issue_session, revoke_all_sessions, revoke_session
from codecare.services.tokens import consume_auth_token, hash_token, issue_auth_token, public_link

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _dummy_hash() -> str:
    return hash_password("codecare-timing-pad")


_DUMMY_PASSWORD_HASH = None


def _timing_hash() -> str:
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = _dummy_hash()
    return _DUMMY_PASSWORD_HASH


def _establish_session(response: Response, db: Session, profile: Profile, organization_id: str) -> None:
    token = issue_session(db, profile.id, organization_id)
    db.commit()
    apply_session_cookie(response, token)


def _memberships(db: Session, profile_id: str) -> list[OrganizationMember]:
    return list(
        db.scalars(
            select(OrganizationMember).where(OrganizationMember.profile_id == profile_id).order_by(OrganizationMember.created_at)
        ).all()
    )


def _preferred_membership(members: list[OrganizationMember]) -> OrganizationMember | None:
    for member in members:
        if member.role == "owner":
            return member
    return members[0] if members else None


def _send_verification(db: Session, profile: Profile) -> str:
    settings = get_settings()
    token = issue_auth_token(db, profile.id, "verify_email", settings.email_verification_hours)
    link = public_link("/workspace/verify-email.html", token)
    return deliver(
        OutboundEmail(
            to=profile.email,
            subject="Verify your CodeCare email",
            text=(
                f"Confirm {profile.email} for CodeCare.\n\n{link}\n\n"
                "If you did not create this account, you can ignore this message."
            ),
        )
    )


def _public_profile(profile: Profile, org: Organization | None, role: str, members: list[OrganizationMember], db: Session) -> dict:
    organizations = []
    for member in members:
        item = db.get(Organization, member.organization_id)
        if item:
            organizations.append({"id": item.id, "name": item.name, "slug": item.slug, "role": member.role})
    return {
        "id": profile.id,
        "email": profile.email,
        "full_name": profile.full_name,
        "email_verified": profile.email_verified_at is not None,
        "role": role,
        "organization": {"id": org.id, "name": org.name, "slug": org.slug} if org else None,
        "organizations": organizations,
    }


@router.get("/csrf")
def csrf(response: Response):
    token = secrets.token_urlsafe(32)
    apply_csrf_cookie(response, token)
    return {"csrf_token": token}


@router.get("/providers")
def providers():
    settings = get_settings()
    return {
        "password": True,
        "github": settings.github_oauth_configured,
        "email": delivery_mode(settings),
    }


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
    org = bootstrap_org(db, profile)
    audit(db, action="auth.register", organization_id=org.id, profile_id=profile.id)
    email_delivery = _send_verification(db, profile)
    _establish_session(response, db, profile, org.id)
    return {
        "id": profile.id,
        "email": profile.email,
        "organization_id": org.id,
        "email_verified": False,
        "email_delivery": email_delivery,
        "message": delivery_message(email_delivery),
    }


@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    profile = db.scalar(select(Profile).where(Profile.email == payload.email.lower()))
    stored = profile.password_hash if profile and profile.password_hash else _timing_hash()
    password_ok = verify_password(payload.password, stored)
    if not profile or not profile.password_hash or not password_ok:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    member = _preferred_membership(_memberships(db, profile.id))
    if not member:
        raise HTTPException(status_code=403, detail="No workspace membership")
    audit(db, action="auth.login", organization_id=member.organization_id, profile_id=profile.id)
    _establish_session(response, db, profile, member.organization_id)
    return {"id": profile.id, "email": profile.email, "organization_id": member.organization_id}


@router.post("/logout")
def logout(
    response: Response,
    db: Session = Depends(get_db),
    codecare_session: str | None = Cookie(default=None, alias=get_settings().cookie_name),
):
    if codecare_session:
        try:
            payload = decode_session_token(codecare_session)
            revoke_session(db, payload.get("sid"))
            db.commit()
        except Exception:
            db.rollback()
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/refresh")
def refresh(response: Response, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    revoke_session(db, auth.session_id)
    audit(db, action="auth.refresh", organization_id=auth.organization_id, profile_id=auth.profile.id)
    _establish_session(response, db, auth.profile, auth.organization_id)
    return {"ok": True}


@router.post("/switch-organization")
def switch_organization(
    payload: SwitchOrganizationRequest,
    response: Response,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
):
    member = db.scalar(
        select(OrganizationMember).where(
            OrganizationMember.profile_id == auth.profile.id,
            OrganizationMember.organization_id == payload.organization_id,
        )
    )
    if not member:
        raise HTTPException(status_code=404, detail="You are not a member of that workspace")
    revoke_session(db, auth.session_id)
    audit(db, action="auth.switch_organization", organization_id=member.organization_id, profile_id=auth.profile.id)
    _establish_session(response, db, auth.profile, member.organization_id)
    return {"ok": True, "organization_id": member.organization_id, "role": member.role}


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    settings = get_settings()
    mode = delivery_mode(settings)
    profile = db.scalar(select(Profile).where(Profile.email == payload.email.lower()))
    if profile and mode != "unavailable":
        token = issue_auth_token(db, profile.id, "reset_password", settings.password_reset_hours)
        link = public_link("/workspace/reset-password.html", token)
        sent = deliver(
            OutboundEmail(
                to=profile.email,
                subject="Reset your CodeCare password",
                text=f"Reset the password for {profile.email}.\n\n{link}\n\nThis link expires in {settings.password_reset_hours} hour(s).",
            )
        )
        db.commit()
        mode = sent
    return {"ok": True, "delivery": mode, "message": delivery_message(mode)}


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, response: Response, db: Session = Depends(get_db)):
    row = consume_auth_token(db, payload.token, "reset_password")
    if not row:
        raise HTTPException(status_code=400, detail="Reset link is invalid or expired")
    profile = db.get(Profile, row.profile_id)
    if not profile:
        raise HTTPException(status_code=400, detail="Reset link is invalid or expired")
    profile.password_hash = hash_password(payload.password)
    profile.email_verified_at = profile.email_verified_at or utcnow()
    revoke_all_sessions(db, profile.id)
    audit(db, action="auth.password_reset", profile_id=profile.id)
    db.commit()
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/verify-email")
def verify_email(payload: VerifyEmailRequest, db: Session = Depends(get_db)):
    row = consume_auth_token(db, payload.token, "verify_email")
    if not row:
        raise HTTPException(status_code=400, detail="Verification link is invalid or expired")
    profile = db.get(Profile, row.profile_id)
    if not profile:
        raise HTTPException(status_code=400, detail="Verification link is invalid or expired")
    profile.email_verified_at = utcnow()
    audit(db, action="auth.email_verified", profile_id=profile.id)
    db.commit()
    return {"ok": True, "email": profile.email}


@router.post("/resend-verification")
def resend_verification(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    if auth.profile.email_verified_at:
        return {"ok": True, "delivery": "skipped", "message": "This email is already verified."}
    email_delivery = _send_verification(db, auth.profile)
    db.commit()
    return {"ok": True, "delivery": email_delivery, "message": delivery_message(email_delivery)}


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
):
    profile = db.get(Profile, auth.profile.id)
    if profile.password_hash:
        if not payload.current_password or not verify_password(payload.current_password, profile.password_hash):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
    elif payload.current_password:
        raise HTTPException(status_code=400, detail="This account has no password yet. Leave the current password blank.")
    profile.password_hash = hash_password(payload.new_password)
    revoke_all_sessions(db, profile.id, except_id=auth.session_id)
    audit(db, action="auth.password_changed", organization_id=auth.organization_id, profile_id=profile.id)
    db.commit()
    return {"ok": True}


@router.post("/delete-account")
def remove_account(
    payload: DeleteAccountRequest,
    response: Response,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
):
    if payload.confirmation != "DELETE":
        raise HTTPException(status_code=400, detail="Type DELETE to confirm account deletion")
    profile = db.get(Profile, auth.profile.id)
    if profile.password_hash:
        if not payload.password or not verify_password(payload.password, profile.password_hash):
            raise HTTPException(status_code=400, detail="Password is incorrect")
    try:
        delete_account(db, profile)
    except AccountGuardError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    db.commit()
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/accept-invite")
def accept_invite(payload: AcceptInviteRequest, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    invite = db.scalar(select(OrganizationInvite).where(OrganizationInvite.token_hash == hash_token(payload.token)))
    if not invite or invite.accepted_at is not None:
        raise HTTPException(status_code=400, detail="Invitation is invalid or already used")
    expires = invite.expires_at if invite.expires_at.tzinfo else invite.expires_at.replace(tzinfo=utcnow().tzinfo)
    if expires < utcnow():
        raise HTTPException(status_code=400, detail="Invitation has expired")
    if invite.email.lower() != auth.profile.email.lower():
        raise HTTPException(status_code=403, detail="Sign in with the invited email address to accept")
    existing = db.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == invite.organization_id,
            OrganizationMember.profile_id == auth.profile.id,
        )
    )
    if not existing:
        db.add(
            OrganizationMember(
                id=new_id(),
                organization_id=invite.organization_id,
                profile_id=auth.profile.id,
                role=invite.role,
            )
        )
    invite.accepted_at = utcnow()
    audit(
        db,
        action="org.invite_accepted",
        organization_id=invite.organization_id,
        profile_id=auth.profile.id,
        target_type="organization_invite",
        target_id=invite.id,
    )
    db.commit()
    return {"ok": True, "organization_id": invite.organization_id, "role": invite.role}


@router.get("/github/start")
def github_sign_in_start(db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.github_oauth_configured:
        raise HTTPException(status_code=503, detail="GitHub sign-in is not configured")
    state = secrets.token_urlsafe(24)
    db.add(OauthState(id=new_id(), state=state, purpose="login"))
    db.commit()
    redirect_uri = settings.public_base_url.rstrip("/") + "/api/v1/github/oauth/callback"
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_app_client_id}"
        "&scope=read:user,user:email"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )
    return {"url": url, "redirect_uri": redirect_uri}


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
        profile = Profile(id=new_id(), email=email, full_name=email.split("@")[0], supabase_user_id=sub, email_verified_at=utcnow())
        db.add(profile)
        org = bootstrap_org(db, profile)
    else:
        profile.supabase_user_id = sub
        if claims.get("email_verified"):
            profile.email_verified_at = profile.email_verified_at or utcnow()
        member = _preferred_membership(_memberships(db, profile.id))
        org = db.get(Organization, member.organization_id) if member else bootstrap_org(db, profile)
    audit(db, action="auth.supabase", organization_id=org.id, profile_id=profile.id)
    _establish_session(response, db, profile, org.id)
    return {"id": profile.id, "email": profile.email, "organization_id": org.id}


@router.get("/me")
def me(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    org = db.get(Organization, auth.organization_id)
    return _public_profile(auth.profile, org, auth.role, _memberships(db, auth.profile.id), db)
