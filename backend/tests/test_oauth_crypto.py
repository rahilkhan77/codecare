from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from codecare.config import Settings
from codecare.production import validate_settings
from codecare.security import (
    EncryptionConfigError,
    decrypt_secret,
    encrypt_secret,
    parse_fernet_key,
    reencrypt_secret,
)
from codecare.services.oauth import CredentialError, apply_oauth_payload, resolve_user_token, token_expired

VALID_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
PREVIOUS_KEY = Fernet.generate_key().decode()
CURRENT_KEY = Fernet.generate_key().decode()


def _settings(**kwargs) -> Settings:
    values = {
        "app_env": "development",
        "app_secret": "a" * 40,
        "credentials_encryption_key": VALID_KEY,
        "credentials_encryption_key_previous": "",
        "database_url": "postgresql+psycopg2://codecare:codecare@localhost/codecare",
        "api_cors_origins": "http://127.0.0.1:8000",
    }
    values.update(kwargs)
    return Settings.model_construct(**values)


def test_expired_oauth_without_refresh_is_rejected():
    installation = SimpleNamespace(
        token_status="valid",
        encrypted_user_token="ciphertext",
        encrypted_refresh_token=None,
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        refresh_expires_at=None,
    )
    assert token_expired(installation.token_expires_at) is True


@pytest.mark.asyncio
async def test_expired_token_refreshes(monkeypatch):
    settings = _settings(credentials_encryption_key=VALID_KEY)

    async def fake_refresh(token):
        assert token == "refresh-old"
        return {"access_token": "access-new", "refresh_token": "refresh-new", "expires_in": 28800}

    monkeypatch.setattr("codecare.services.oauth.gh.refresh_oauth_token", fake_refresh)
    monkeypatch.setattr("codecare.services.oauth.encrypt_secret", lambda value, s=None: encrypt_secret(value, settings))
    monkeypatch.setattr("codecare.services.oauth.decrypt_secret", lambda value, s=None: decrypt_secret(value, settings))
    installation = SimpleNamespace(
        token_status="valid",
        encrypted_user_token=encrypt_secret("access-old", settings),
        encrypted_refresh_token=encrypt_secret("refresh-old", settings),
        token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        refresh_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    token = await resolve_user_token(SimpleNamespace(commit=lambda: None), installation)
    assert token == "access-new"
    assert installation.token_status == "valid"


@pytest.mark.asyncio
async def test_expired_token_without_refresh_raises():
    settings = _settings()
    installation = SimpleNamespace(
        token_status="valid",
        encrypted_user_token=encrypt_secret("access-old", settings),
        encrypted_refresh_token=None,
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        refresh_expires_at=None,
    )
    db = SimpleNamespace(commit=lambda: None)
    with pytest.raises(CredentialError) as exc:
        await resolve_user_token(db, installation)
    assert exc.value.status == "expired"
    assert installation.token_status == "expired"


@pytest.mark.asyncio
async def test_revoked_credentials_never_returned():
    installation = SimpleNamespace(
        token_status="revoked",
        encrypted_user_token="x",
        encrypted_refresh_token="y",
        token_expires_at=None,
        refresh_expires_at=None,
    )
    with pytest.raises(CredentialError) as exc:
        await resolve_user_token(SimpleNamespace(), installation)
    assert exc.value.status == "revoked"


def test_invalid_encryption_key_rejected():
    with pytest.raises(EncryptionConfigError):
        parse_fernet_key("not-a-fernet-key")
    with pytest.raises(RuntimeError, match="Fernet"):
        validate_settings(_settings(app_env="production", credentials_encryption_key="g" * 44))


def test_production_requires_dedicated_encryption_key():
    with pytest.raises(RuntimeError, match="CREDENTIALS_ENCRYPTION_KEY"):
        validate_settings(_settings(app_env="production", credentials_encryption_key=""))


def test_encryption_key_rotation():
    previous_settings = _settings(credentials_encryption_key=PREVIOUS_KEY)
    ciphertext = encrypt_secret("github-token", previous_settings)
    rotated = _settings(credentials_encryption_key=CURRENT_KEY, credentials_encryption_key_previous=PREVIOUS_KEY)
    assert decrypt_secret(ciphertext, rotated) == "github-token"
    rewritten = reencrypt_secret(ciphertext, rotated)
    current_only = _settings(credentials_encryption_key=CURRENT_KEY, credentials_encryption_key_previous="")
    assert decrypt_secret(rewritten, current_only) == "github-token"
    with pytest.raises(ValueError):
        decrypt_secret(ciphertext, current_only)


def test_oauth_payload_stores_expiry():
    installation = SimpleNamespace(
        encrypted_user_token=None,
        encrypted_refresh_token=None,
        token_expires_at=None,
        refresh_expires_at=None,
        token_status="missing",
    )
    apply_oauth_payload(
        installation,
        {"access_token": "a", "refresh_token": "r", "expires_in": 8, "refresh_token_expires_in": 1000},
    )
    assert installation.token_status == "valid"
    assert installation.token_expires_at is not None
    assert installation.encrypted_refresh_token
