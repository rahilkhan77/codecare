from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from codecare.config import Settings, get_settings

SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|authorization|bearer)\s*[:=]\s*['\"]?[^\s'\"]{8,}"
)
PEM_PATTERN = re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----")


class EncryptionConfigError(RuntimeError):
    pass


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_session_token(profile_id: str, organization_id: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": profile_id,
        "org": organization_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.session_hours)).timestamp()),
    }
    return jwt.encode(payload, settings.app_secret, algorithm="HS256")


def decode_session_token(token: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    return jwt.decode(token, settings.app_secret, algorithms=["HS256"])


def decode_supabase_token(token: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    if not settings.supabase_jwt_secret:
        raise jwt.InvalidTokenError("Supabase is not configured")
    return jwt.decode(token, settings.supabase_jwt_secret, algorithms=["HS256"], audience="authenticated")


def parse_fernet_key(raw: str, name: str = "CREDENTIALS_ENCRYPTION_KEY") -> bytes:
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
    fernets = [Fernet(parse_fernet_key(settings.credentials_encryption_key, "CREDENTIALS_ENCRYPTION_KEY"))]
    previous = (settings.credentials_encryption_key_previous or "").strip()
    if previous:
        fernets.append(Fernet(parse_fernet_key(previous, "CREDENTIALS_ENCRYPTION_KEY_PREVIOUS")))
    return fernets


def assert_encryption_configured(settings: Settings, *, required: bool) -> None:
    if required and not (settings.credentials_encryption_key or "").strip():
        raise EncryptionConfigError("CREDENTIALS_ENCRYPTION_KEY is required in production.")
    if (settings.credentials_encryption_key or "").strip():
        parse_fernet_key(settings.credentials_encryption_key, "CREDENTIALS_ENCRYPTION_KEY")
    if (settings.credentials_encryption_key_previous or "").strip():
        parse_fernet_key(settings.credentials_encryption_key_previous, "CREDENTIALS_ENCRYPTION_KEY_PREVIOUS")


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


def verify_stripe_signature(secret: str, body: bytes, header: str) -> bool:
    if not secret or not header:
        return False
    try:
        import stripe

        stripe.Webhook.construct_event(body, header, secret)
        return True
    except Exception:
        return False


def redact(text: str) -> str:
    redacted = SECRET_PATTERN.sub("[redacted]", text or "")
    return PEM_PATTERN.sub("[redacted-private-key]", redacted)
