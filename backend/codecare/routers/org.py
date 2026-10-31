from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.db import get_db
from codecare.deps import AuthContext, current_auth, require_roles
from codecare.models import Organization, OrganizationInvite, OrganizationMember, Profile, new_id, utcnow
from codecare.schemas import InviteMemberRequest, UpdateMemberRequest
from codecare.services.audit import audit
from codecare.services.email import OutboundEmail, deliver, delivery_message
from codecare.services.tokens import hash_token, public_link

router = APIRouter(tags=["organization"])


def _owner_count(db: Session, organization_id: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.organization_id == organization_id, OrganizationMember.role == "owner")
        )
        or 0
    )


@router.get("/api/v1/org/members")
def list_members(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    rows = db.execute(
        select(OrganizationMember, Profile)
        .join(Profile, Profile.id == OrganizationMember.profile_id)
        .where(OrganizationMember.organization_id == auth.organization_id)
        .order_by(OrganizationMember.created_at)
    ).all()
    return [
        {
            "id": member.id,
            "profile_id": profile.id,
            "email": profile.email,
            "full_name": profile.full_name,
            "role": member.role,
        }
        for member, profile in rows
    ]


@router.post("/api/v1/org/members")
def invite_member(
    payload: InviteMemberRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    email = payload.email.lower()
    org = db.get(Organization, auth.organization_id)
    profile = db.scalar(select(Profile).where(Profile.email == email))
    if profile:
        existing = db.scalar(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == auth.organization_id,
                OrganizationMember.profile_id == profile.id,
            )
        )
        if existing:
            raise HTTPException(status_code=409, detail="That account is already a member of this workspace")
        db.add(
            OrganizationMember(
                id=new_id(),
                organization_id=auth.organization_id,
                profile_id=profile.id,
                role=payload.role,
            )
        )
        audit(
            db,
            action="org.member_added",
            organization_id=auth.organization_id,
            profile_id=auth.profile.id,
            target_type="profile",
            target_id=profile.id,
            metadata={"role": payload.role},
        )
        delivery = deliver(
            OutboundEmail(
                to=email,
                subject=f"You joined {org.name if org else 'a CodeCare workspace'}",
                text=f"{auth.profile.full_name or auth.profile.email} added you to {org.name if org else 'a workspace'} as {payload.role}. Sign in at {get_settings().public_base_url.rstrip('/')}/workspace/login.html",
            )
        )
        db.commit()
        return {"ok": True, "joined": True, "delivery": delivery, "message": delivery_message(delivery)}

    raw = secrets.token_urlsafe(32)
    db.add(
        OrganizationInvite(
            id=new_id(),
            organization_id=auth.organization_id,
            email=email,
            role=payload.role,
            token_hash=hash_token(raw),
            invited_by=auth.profile.id,
            expires_at=utcnow() + timedelta(days=7),
        )
    )
    link = public_link("/workspace/accept-invite.html", raw)
    delivery = deliver(
        OutboundEmail(
            to=email,
            subject=f"Join {org.name if org else 'a CodeCare workspace'}",
            text=(
                f"{auth.profile.full_name or auth.profile.email} invited you to {org.name if org else 'a workspace'} as {payload.role}.\n\n"
                f"Create an account with {email}, then open:\n{link}"
            ),
        )
    )
    audit(db, action="org.invite_created", organization_id=auth.organization_id, profile_id=auth.profile.id, metadata={"email": email, "role": payload.role})
    db.commit()
    body = {"ok": True, "joined": False, "delivery": delivery, "message": delivery_message(delivery)}
    if delivery != "smtp":
        body["invite_url"] = link
    return body


@router.patch("/api/v1/org/members/{member_id}")
def update_member(
    member_id: str,
    payload: UpdateMemberRequest,
    auth: AuthContext = Depends(require_roles("owner")),
    db: Session = Depends(get_db),
):
    member = db.get(OrganizationMember, member_id)
    if not member or member.organization_id != auth.organization_id:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.role == "owner" and payload.role != "owner" and _owner_count(db, auth.organization_id) <= 1:
        raise HTTPException(status_code=409, detail="The workspace needs at least one owner")
    member.role = payload.role
    if payload.role == "owner":
        org = db.get(Organization, auth.organization_id)
        if org:
            org.owner_id = member.profile_id
    audit(
        db,
        action="org.role_changed",
        organization_id=auth.organization_id,
        profile_id=auth.profile.id,
        target_type="organization_member",
        target_id=member.id,
        metadata={"role": payload.role},
    )
    db.commit()
    return {"ok": True, "role": member.role}


@router.delete("/api/v1/org/members/{member_id}")
def remove_member(
    member_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    member = db.get(OrganizationMember, member_id)
    if not member or member.organization_id != auth.organization_id:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.role == "owner":
        if auth.role != "owner":
            raise HTTPException(status_code=403, detail="Only an owner can remove an owner")
        if _owner_count(db, auth.organization_id) <= 1:
            raise HTTPException(status_code=409, detail="The workspace needs at least one owner")
    db.delete(member)
    audit(
        db,
        action="org.member_removed",
        organization_id=auth.organization_id,
        profile_id=auth.profile.id,
        target_type="organization_member",
        target_id=member_id,
    )
    db.commit()
    return {"ok": True}
