from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import time
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from codecare.config import Settings, get_settings

SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|authorization|bearer)\s*[:=]\s*['\"]?[^\s'\"]{8,}"
)
BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}")
URL_USERINFO_PATTERN = re.compile(r"://[^/\s:@]+:[^@\s/]+@")
PEM_PATTERN = re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----")


class EncryptionConfigError(RuntimeError):
    pass


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_session_token(
    profile_id: str,
    organization_id: str,
    settings: Settings | None = None,
    *,
    session_id: str,
    expires_at: datetime | None = None,
) -> str:
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    exp = expires_at or (now + timedelta(hours=settings.session_hours))
    payload = {
        "sub": profile_id,
        "org": organization_id,
        "sid": session_id,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, settings.app_secret, algorithm="HS256")


def cookie_security(settings: Settings) -> dict:
    options = {
        "secure": settings.app_env == "production",
        "samesite": "lax",
        "path": "/",
    }
    domain = (settings.cookie_domain or "").strip()
    if domain:
        options["domain"] = domain
    return options


def apply_session_cookie(response, token: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    response.set_cookie(
        settings.cookie_name,
        token,
        httponly=True,
        max_age=settings.session_hours * 3600,
        **cookie_security(settings),
    )


def clear_session_cookie(response, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    response.delete_cookie(settings.cookie_name, **cookie_security(settings))


def apply_csrf_cookie(response, token: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        httponly=False,
        max_age=settings.session_hours * 3600,
        **cookie_security(settings),
    )


def decode_session_token(token: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    return jwt.decode(token, settings.app_secret, algorithms=["HS256"])


def decode_supabase_token(token: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    if not settings.supabase_jwt_secret:
        raise jwt.InvalidTokenError("Supabase is not configured")
    return jwt.decode(token, settings.supabase_jwt_secret, algorithms=["HS256"], audience="authenticated")


def parse_fernet_key(raw: str, name: str = "FERNET_KEY") -> bytes:
    value = (raw or "").strip()
    if not value:
        raise EncryptionConfigError(f"{name} is not set. Generate one with cryptography.fernet.Fernet.generate_key().")
    key = value.encode()
    try:
        Fernet(key)
    except (ValueError, TypeError) as exc:
        raise EncryptionConfigError(
            f"{name} is not a valid Fernet key. It must be 32 url-safe base64-encoded bytes."
        ) from exc
    return key


def encryption_fernets(settings: Settings | None = None) -> list[Fernet]:
    settings = settings or get_settings()
    fernets = [Fernet(parse_fernet_key(settings.fernet_key, "FERNET_KEY"))]
    previous = (settings.fernet_key_previous or "").strip()
    if previous:
        fernets.append(Fernet(parse_fernet_key(previous, "FERNET_KEY_PREVIOUS")))
    return fernets


def assert_encryption_configured(settings: Settings, *, required: bool) -> None:
    if required and not (settings.fernet_key or "").strip():
        raise EncryptionConfigError("FERNET_KEY is required in production.")
    if (settings.fernet_key or "").strip():
        parse_fernet_key(settings.fernet_key, "FERNET_KEY")
    if (settings.fernet_key_previous or "").strip():
        parse_fernet_key(settings.fernet_key_previous, "FERNET_KEY_PREVIOUS")


def encrypt_secret(value: str, settings: Settings | None = None) -> str:
    return encryption_fernets(settings)[0].encrypt(value.encode()).decode()


def decrypt_secret(value: str, settings: Settings | None = None) -> str:
    last: Exception | None = None
    for fernet in encryption_fernets(settings):
        try:
            return fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            last = exc
    raise ValueError("Unable to decrypt stored credential") from last


def reencrypt_secret(value: str, settings: Settings | None = None) -> str:
    """Decrypt with current or previous key, encrypt with the current key."""
    return encrypt_secret(decrypt_secret(value, settings), settings)


def verify_github_signature(secret: str, body: bytes, header: str) -> bool:
    if not secret or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def _standard_webhook_key(secret: str) -> bytes | None:
    raw = secret.strip()
    if raw.startswith("whsec_"):
        raw = raw[len("whsec_") :]
    if not raw:
        return None
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None
    return key or None


def verify_standard_webhook(
    secret: str,
    body: bytes,
    webhook_id: str,
    webhook_timestamp: str,
    webhook_signature: str,
    *,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> bool:
    """Verify a Standard Webhooks signature (webhook-id, webhook-timestamp, webhook-signature)."""
    if not secret or not webhook_id or not webhook_timestamp or not webhook_signature:
        return False
    key = _standard_webhook_key(secret)
    if key is None:
        return False
    try:
        timestamp = int(webhook_timestamp)
    except ValueError:
        return False
    current = int(time.time()) if now is None else now
    if tolerance_seconds >= 0 and abs(current - timestamp) > tolerance_seconds:
        return False
    signed = f"{webhook_id}.{webhook_timestamp}.".encode() + body
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    for part in webhook_signature.split():
        version, separator, signature = part.partition(",")
        if version != "v1" or not separator or not signature:
            continue
        try:
            candidate = base64.b64decode(signature, validate=True)
        except (binascii.Error, ValueError):
            continue
        if hmac.compare_digest(digest, candidate):
            return True
    return False


def redact(text: str) -> str:
    redacted = SECRET_PATTERN.sub("[redacted]", text or "")
    redacted = BEARER_PATTERN.sub("Bearer [redacted]", redacted)
    redacted = URL_USERINFO_PATTERN.sub("://[redacted]@", redacted)
    return PEM_PATTERN.sub("[redacted-private-key]", redacted)
