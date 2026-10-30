from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.models import AuthToken, new_id, utcnow


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_auth_token(db: Session, profile_id: str, purpose: str, hours: int) -> str:
    now = utcnow()
    existing = db.scalars(
        select(AuthToken).where(
            AuthToken.profile_id == profile_id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
        )
    ).all()
    for row in existing:
        row.used_at = now
    raw = secrets.token_urlsafe(32)
    db.add(
        AuthToken(
            id=new_id(),
            profile_id=profile_id,
            purpose=purpose,
            token_hash=hash_token(raw),
            expires_at=now + timedelta(hours=hours),
        )
    )
    return raw


def consume_auth_token(db: Session, raw: str, purpose: str) -> AuthToken | None:
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(raw), AuthToken.purpose == purpose))
    if not row or row.used_at is not None:
        return None
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=utcnow().tzinfo)
    if expires < utcnow():
        return None
    row.used_at = utcnow()
    return row


def public_link(path: str, token: str) -> str:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}{path}?token={token}"
