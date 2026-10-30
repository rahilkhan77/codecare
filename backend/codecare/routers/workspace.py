from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.db import get_db
from codecare.deps import AuthContext, current_auth
from codecare.models import (
    DetectedIssue,
    GithubInstallation,
    Notification,
    Organization,
    Profile,
    PullRequestRecord,
    Repository,
    Subscription,
)
from codecare.schemas import SettingsUpdate
from codecare.services.entitlements import current_plan

router = APIRouter(tags=["workspace"])


@router.get("/api/v1/workspace/overview")
def overview(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    org_id = auth.organization_id
    repos = db.scalars(select(Repository).where(Repository.organization_id == org_id, Repository.connected.is_(True))).all()
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    resolved = db.scalar(
        select(func.count()).select_from(DetectedIssue).where(
            DetectedIssue.organization_id == org_id,
            DetectedIssue.status.in_(["resolved", "pr_opened"]),
            DetectedIssue.updated_at >= week_ago,
        )
    ) or 0
    open_prs = db.scalar(
        select(func.count()).select_from(PullRequestRecord).where(
            PullRequestRecord.organization_id == org_id,
            PullRequestRecord.status == "open",
        )
    ) or 0
    attention = sum(1 for repo in repos if repo.health_status == "attention")
    health = "healthy" if repos and attention == 0 else "attention" if repos else "unknown"
    activities = db.scalars(
        select(Notification).where(Notification.organization_id == org_id).order_by(Notification.created_at.desc()).limit(8)
    ).all()
    return {
        "greeting_name": auth.profile.full_name.split(" ")[0] if auth.profile.full_name else "there",
        "health": health,
        "repo_count": len(repos),
        "issues_resolved_7d": resolved,
        "prs_need_review": open_prs,
        "plan": current_plan(db, org_id),
        "repositories": [
            {
                "id": repo.id,
                "full_name": repo.full_name,
                "health": repo.health_status,
                "language": repo.language,
                "last_scanned_at": repo.last_scanned_at.isoformat() if repo.last_scanned_at else None,
            }
            for repo in repos
        ],
        "activity": [
            {"id": item.id, "title": item.title, "body": item.body, "created_at": item.created_at.isoformat()}
            for item in activities
        ],
    }


@router.get("/api/v1/notifications")
def notifications(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Notification).where(Notification.organization_id == auth.organization_id).order_by(Notification.created_at.desc()).limit(50)
    ).all()
    return [
        {"id": row.id, "title": row.title, "body": row.body, "read": row.read, "created_at": row.created_at.isoformat()}
        for row in rows
    ]


@router.post("/api/v1/notifications/{notification_id}/read")
def read_notification(notification_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    row = db.get(Notification, notification_id)
    if row and row.organization_id == auth.organization_id:
        row.read = True
        db.commit()
    return {"ok": True}


@router.get("/api/v1/settings")
def settings_get(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    settings = get_settings()
    org = db.get(Organization, auth.organization_id)
    installation = db.scalar(select(GithubInstallation).where(GithubInstallation.organization_id == auth.organization_id))
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    return {
        "profile": {
            "email": auth.profile.email,
            "full_name": auth.profile.full_name,
            "email_verified": auth.profile.email_verified_at is not None,
            "role": auth.role,
        },
        "organization": {"name": org.name if org else "", "slug": org.slug if org else ""},
        "github": {
            "configured": settings.github_configured,
            "connected": bool(installation and not str(installation.installation_id).startswith("pending-")),
            "account_login": installation.account_login if installation else None,
        },
        "ai": {"provider": "configured" if (settings.groq_api_key or settings.openai_api_key or settings.anthropic_api_key) else "heuristic"},
        "billing": {
            "stripe_configured": settings.stripe_configured,
            "plan": sub.plan if sub else "none",
            "status": sub.status if sub else "inactive",
        },
        "sandbox": {"requires_docker": True},
    }


@router.patch("/api/v1/settings")
def settings_patch(payload: SettingsUpdate, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    profile = db.get(Profile, auth.profile.id)
    profile.full_name = payload.full_name
    db.commit()
    return {"ok": True, "full_name": profile.full_name}
