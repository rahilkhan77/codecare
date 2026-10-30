from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from codecare.models import Repository, Subscription, UsageRecord

PLAN_LIMITS = {
    "none": {"repos": 1, "scans_per_day": 5, "ai_per_day": 3},
    "starter": {"repos": 1, "scans_per_day": 20, "ai_per_day": 20},
    "pro": {"repos": 5, "scans_per_day": 200, "ai_per_day": 200},
    "team": {"repos": 15, "scans_per_day": 1000, "ai_per_day": 1000},
    "enterprise": {"repos": 10_000, "scans_per_day": 10_000, "ai_per_day": 10_000},
}


def current_plan(db: Session, organization_id: str) -> str:
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == organization_id))
    if sub and sub.status in {"active", "trialing"}:
        return sub.plan
    return "none"


def _today_usage(db: Session, organization_id: str, metric: str) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.scalar(
            select(func.coalesce(func.sum(UsageRecord.quantity), 0)).where(
                UsageRecord.organization_id == organization_id,
                UsageRecord.metric == metric,
                UsageRecord.created_at >= start,
            )
        )
        or 0
    )


def assert_repo_quota(db: Session, organization_id: str, additional: int = 1) -> None:
    plan = current_plan(db, organization_id)
    limit = PLAN_LIMITS[plan]["repos"]
    connected = db.scalar(
        select(func.count()).select_from(Repository).where(
            Repository.organization_id == organization_id,
            Repository.connected.is_(True),
        )
    ) or 0
    if connected + additional > limit:
        raise HTTPException(
            status_code=402,
            detail=f"Plan '{plan}' allows {limit} connected repositor{'y' if limit == 1 else 'ies'}.",
        )


def assert_metric_quota(db: Session, organization_id: str, metric: str, limit_key: str) -> None:
    plan = current_plan(db, organization_id)
    limit = PLAN_LIMITS[plan][limit_key]
    used = _today_usage(db, organization_id, metric)
    if used >= limit:
        raise HTTPException(status_code=402, detail=f"Daily {metric} allowance for plan '{plan}' has been reached.")
