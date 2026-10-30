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
    utcnow,
)
from codecare.services.email import OutboundEmail, deliver, delivery_message
from codecare.schemas import SettingsUpdate
from codecare.services.entitlements import current_plan

router = APIRouter(tags=["workspace"])


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _app_card(db: Session, repo: Repository) -> dict:
    open_issues = db.scalar(
        select(func.count()).select_from(DetectedIssue).where(
            DetectedIssue.repository_id == repo.id,
            DetectedIssue.status.notin_(["resolved", "pr_opened"]),
        )
    ) or 0
    shipped = db.scalar(
        select(func.count()).select_from(PullRequestRecord).where(
            PullRequestRecord.repository_id == repo.id,
            PullRequestRecord.created_at >= _month_start(),
        )
    ) or 0
    return {
        "id": repo.id,
        "full_name": repo.full_name,
        "health": repo.health_status,
        "language": repo.language,
        "last_scanned_at": repo.last_scanned_at.isoformat() if repo.last_scanned_at else None,
        "open_issues": open_issues,
        "prs_shipped_this_month": shipped,
        "scan_interval_hours": repo.scan_interval_hours,
        "auto_merge": bool(repo.auto_merge),
    }


def _monthly_report(db: Session, organization_id: str) -> dict:
    start = _month_start()
    repos = db.scalars(select(Repository).where(Repository.organization_id == organization_id, Repository.connected.is_(True))).all()
    prs = db.scalars(
        select(PullRequestRecord).where(
            PullRequestRecord.organization_id == organization_id,
            PullRequestRecord.created_at >= start,
        )
    ).all()
    fixed = db.scalars(
        select(DetectedIssue).where(
            DetectedIssue.organization_id == organization_id,
            DetectedIssue.status.in_(["resolved", "pr_opened"]),
            DetectedIssue.updated_at >= start,
        )
    ).all()
    return {
        "month": start.strftime("%Y-%m"),
        "apps": [_app_card(db, repo) for repo in repos],
        "pull_requests": [{"title": row.title, "url": row.url, "status": row.status} for row in prs],
        "fixes": [{"title": row.title, "status": row.status} for row in fixed],
        "summary": (
            f"{len(prs)} pull request{'s' if len(prs) != 1 else ''} opened and "
            f"{len(fixed)} issue{'s' if len(fixed) != 1 else ''} moved forward across {len(repos)} app{'s' if len(repos) != 1 else ''}."
        ),
    }


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
        "repositories": [_app_card(db, repo) for repo in repos],
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
            "connected": bool(
                installation and not str(installation.installation_id).startswith(("pending-", "sample-", "revoked-"))
            ),
            "account_login": installation.account_login if installation else None,
        },
        "ai": {"provider": "configured" if (settings.groq_api_key or settings.openai_api_key or settings.anthropic_api_key) else "heuristic"},
        "billing": {
            "stripe_configured": settings.stripe_configured,
            "plan": sub.plan if sub else "none",
            "status": sub.status if sub else "inactive",
        },
        "sandbox": {"requires_docker": True},
        "demo_mode": settings.demo_mode,
    }


@router.patch("/api/v1/settings")
def settings_patch(payload: SettingsUpdate, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    profile = db.get(Profile, auth.profile.id)
    profile.full_name = payload.full_name
    db.commit()
    return {"ok": True, "full_name": profile.full_name}


@router.get("/api/v1/reports/monthly")
def monthly_report(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    return _monthly_report(db, auth.organization_id)


@router.post("/api/v1/reports/monthly/email")
def email_monthly_report(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    report = _monthly_report(db, auth.organization_id)
    lines = [report["summary"], ""]
    for item in report["pull_requests"]:
        lines.append(f"- {item['title']} ({item['status']}) {item['url']}")
    if not report["pull_requests"]:
        lines.append("No pull requests were opened this month.")
    sent = deliver(
        OutboundEmail(
            to=auth.profile.email,
            subject=f"CodeCare maintenance report {report['month']}",
            text="\n".join(lines),
        )
    )
    return {"ok": True, "delivery": sent, "message": delivery_message(sent), "month": report["month"]}


@router.post("/api/v1/onboarding/complete")
def complete_onboarding(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    profile = db.get(Profile, auth.profile.id)
    profile.onboarding_completed_at = profile.onboarding_completed_at or utcnow()
    db.commit()
    return {"ok": True}
