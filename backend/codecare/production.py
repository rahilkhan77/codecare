from __future__ import annotations

from codecare.config import Settings
from codecare.security import EncryptionConfigError, assert_encryption_configured

WEAK_SECRETS = {
    "",
    "dev-only-change-me",
    "test-secret-key-please-change",
    "test-secret-key-please-change-now",
    "change-me-to-a-long-random-string",
}


def validate_settings(settings: Settings) -> None:
    if (settings.fernet_key or "").strip() or (settings.fernet_key_previous or "").strip():
        try:
            assert_encryption_configured(settings, required=False)
        except EncryptionConfigError as exc:
            raise RuntimeError(str(exc)) from exc
    if settings.app_env != "production":
        return
    if settings.app_secret in WEAK_SECRETS or len(settings.app_secret) < 32:
        raise RuntimeError("APP_SECRET must be a strong, unique value in production.")
    try:
        assert_encryption_configured(settings, required=True)
    except EncryptionConfigError as exc:
        raise RuntimeError(str(exc)) from exc
    if settings.is_sqlite:
        raise RuntimeError("SQLite is not supported in production. Set DATABASE_URL to PostgreSQL.")
    if not (settings.public_base_url or "").startswith("https://"):
        raise RuntimeError("PUBLIC_BASE_URL must use https in production.")
    if settings.cors_origins != ["https://codecare.si"]:
        raise RuntimeError("CORS must be limited to https://codecare.si in production.")
    if settings.demo_mode:
        raise RuntimeError("DEMO_MODE cannot be enabled in production.")
