from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from codecare.models import GithubInstallation, utcnow
from codecare.security import decrypt_secret, encrypt_secret
from codecare.services import github as gh

REFRESH_SKEW = timedelta(minutes=2)


class CredentialError(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def token_expired(expires_at: datetime | None, *, now: datetime | None = None) -> bool:
    if expires_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return _aware(expires_at) <= current + REFRESH_SKEW


def apply_oauth_payload(installation: GithubInstallation, token_data: dict) -> None:
    access = token_data.get("access_token")
    if not access:
        raise CredentialError("invalid", "GitHub OAuth response did not include an access token")
    installation.encrypted_user_token = encrypt_secret(access)
    refresh = token_data.get("refresh_token")
    if refresh:
        installation.encrypted_refresh_token = encrypt_secret(refresh)
    expires_in = int(token_data.get("expires_in") or 0)
    installation.token_expires_at = utcnow() + timedelta(seconds=expires_in) if expires_in else None
    refresh_expires_in = int(token_data.get("refresh_token_expires_in") or 0)
    installation.refresh_expires_at = utcnow() + timedelta(seconds=refresh_expires_in) if refresh_expires_in else None
    installation.token_status = "valid"


def clear_oauth_credentials(installation: GithubInstallation, status: str = "revoked") -> None:
    installation.encrypted_user_token = None
    installation.encrypted_refresh_token = None
    installation.token_expires_at = None
    installation.refresh_expires_at = None
    installation.token_status = status


def oauth_snapshot(installation: GithubInstallation | None) -> dict:
    if not installation:
        return {"status": "missing", "expires_at": None, "has_refresh_token": False}
    return {
        "status": installation.token_status or "missing",
        "expires_at": installation.token_expires_at.isoformat() if installation.token_expires_at else None,
        "has_refresh_token": bool(installation.encrypted_refresh_token),
    }


async def resolve_user_token(db: Session, installation: GithubInstallation) -> str:
    if installation.token_status in {"revoked", "invalid"}:
        raise CredentialError(installation.token_status, "GitHub user credentials are not usable")
    if not installation.encrypted_user_token:
        installation.token_status = "missing"
        db.commit()
        raise CredentialError("missing", "No GitHub user token is stored")
    if token_expired(installation.token_expires_at):
        if not installation.encrypted_refresh_token:
            installation.token_status = "expired"
            db.commit()
            raise CredentialError("expired", "GitHub user token expired and no refresh token is stored")
        if token_expired(installation.refresh_expires_at, now=datetime.now(timezone.utc) - REFRESH_SKEW):
            installation.token_status = "expired"
            db.commit()
            raise CredentialError("expired", "GitHub refresh token has expired; reconnect GitHub identity")
        await _refresh(db, installation)
    return decrypt_secret(installation.encrypted_user_token)


async def _refresh(db: Session, installation: GithubInstallation) -> None:
    refresh = decrypt_secret(installation.encrypted_refresh_token or "")
    try:
        data = await gh.refresh_oauth_token(refresh)
    except gh.GitHubError as exc:
        message = str(exc).lower()
        if "401" in message or "bad_refresh_token" in message or "incorrect_client_credentials" in message:
            clear_oauth_credentials(installation, "revoked")
            db.commit()
            raise CredentialError("revoked", "GitHub refresh token was rejected") from exc
        raise
    apply_oauth_payload(installation, data)
    db.commit()


async def revoke_and_clear(db: Session, installation: GithubInstallation) -> None:
    token = None
    if installation.encrypted_user_token:
        try:
            token = decrypt_secret(installation.encrypted_user_token)
        except ValueError:
            token = None
    if token:
        try:
            await gh.revoke_oauth_token(token)
        except gh.GitHubError:
            pass
    clear_oauth_credentials(installation, "revoked")
    db.commit()
