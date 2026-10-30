import pytest

from codecare.config import Settings
from codecare.production import validate_settings
from codecare.services.github import GitHubError, safe_repo_path

VALID_FERNET = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _settings(**kwargs) -> Settings:
    values = {
        "app_env": "development",
        "app_secret": "dev-only-change-me",
        "credentials_encryption_key": VALID_FERNET,
        "database_url": "sqlite:///./x.db",
        "api_cors_origins": "http://127.0.0.1:8000",
    }
    values.update(kwargs)
    return Settings.model_construct(**values)


def test_production_rejects_weak_secret():
    with pytest.raises(RuntimeError):
        validate_settings(
            _settings(
                app_env="production",
                app_secret="dev-only-change-me",
                credentials_encryption_key=VALID_FERNET,
                database_url="postgresql+psycopg2://codecare:codecare@localhost/codecare",
            )
        )


def test_production_rejects_sqlite():
    with pytest.raises(RuntimeError, match="SQLite"):
        validate_settings(
            _settings(
                app_env="production",
                app_secret="a" * 40,
                credentials_encryption_key=VALID_FERNET,
                database_url="sqlite:///./x.db",
            )
        )


def test_dev_settings_allowed():
    validate_settings(_settings())


def test_production_requires_https_public_url():
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        validate_settings(
            _settings(
                app_env="production",
                app_secret="a" * 40,
                credentials_encryption_key=VALID_FERNET,
                database_url="postgresql+psycopg2://codecare:codecare@localhost/codecare",
                public_base_url="http://codecare.si",
                api_cors_origins="https://codecare.si",
            )
        )
    validate_settings(
        _settings(
            app_env="production",
            app_secret="a" * 40,
            credentials_encryption_key=VALID_FERNET,
            database_url="postgresql+psycopg2://codecare:codecare@localhost/codecare",
            public_base_url="https://codecare.si",
            api_cors_origins="https://codecare.si,https://www.codecare.si",
            cookie_domain=".codecare.si",
        )
    )


def test_safe_repo_path():
    assert safe_repo_path("src/app.py") == "src/app.py"
    with pytest.raises(GitHubError):
        safe_repo_path("../etc/passwd")
