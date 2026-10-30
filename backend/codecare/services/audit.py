from __future__ import annotations

import json

from sqlalchemy.orm import Session

from codecare.models import AuditLog, Notification, UsageRecord, new_id
from codecare.security import redact


def audit(
    db: Session,
    *,
    action: str,
    organization_id: str | None = None,
    profile_id: str | None = None,
    target_type: str = "",
    target_id: str = "",
    metadata: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            id=new_id(),
            organization_id=organization_id,
            profile_id=profile_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            metadata_json=redact(json.dumps(metadata or {})),
        )
    )


def notify(db: Session, organization_id: str, title: str, body: str, profile_id: str | None = None) -> None:
    db.add(
        Notification(
            id=new_id(),
            organization_id=organization_id,
            profile_id=profile_id,
            title=title,
            body=redact(body),
        )
    )


def usage(db: Session, organization_id: str, metric: str, quantity: int = 1) -> None:
    db.add(UsageRecord(id=new_id(), organization_id=organization_id, metric=metric, quantity=quantity))
