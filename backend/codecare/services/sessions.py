from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from codecare.config import Settings, get_settings
from codecare.models import AuthSession, new_id, utcnow
from codecare.security import create_session_token


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def issue_session(db: Session, profile_id: str, organization_id: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    expires_at = utcnow() + timedelta(hours=settings.session_hours)
    row = AuthSession(id=new_id(), profile_id=profile_id, organization_id=organization_id, expires_at=expires_at)
    db.add(row)
    db.flush()
    return create_session_token(profile_id, organization_id, settings, session_id=row.id, expires_at=expires_at)


def revoke_session(db: Session, session_id: str | None) -> None:
    if not session_id:
        return
    row = db.get(AuthSession, session_id)
    if row and row.revoked_at is None:
        row.revoked_at = utcnow()


def revoke_all_sessions(db: Session, profile_id: str, *, except_id: str | None = None) -> None:
    rows = db.scalars(select(AuthSession).where(AuthSession.profile_id == profile_id, AuthSession.revoked_at.is_(None))).all()
    now = utcnow()
    for row in rows:
        if row.id != except_id:
            row.revoked_at = now


def maybe_extend_session(db: Session, row: AuthSession, settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    remaining = as_utc(row.expires_at) - now
    if remaining > timedelta(hours=settings.session_hours) / 4:
        return None
    row.expires_at = now + timedelta(hours=settings.session_hours)
    return create_session_token(
        row.profile_id,
        row.organization_id,
        settings,
        session_id=row.id,
        expires_at=row.expires_at,
    )
