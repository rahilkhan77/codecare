from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = _BACKEND_DIR.parent
FRONTEND_ROOT = REPO_ROOT / "frontend"


def normalize_database_url(value: str) -> str:
    text = (value or "").strip()
    if text.startswith("postgres://"):
        return "postgresql+psycopg2://" + text[len("postgres://") :]
    if text.startswith("postgresql://"):
        return "postgresql+psycopg2://" + text[len("postgresql://") :]
    return text


def normalize_github_app_slug(value: str) -> str:
    """Accept a slug or a GitHub App URL and return the slug. Default is codecare-ai."""
    text = (value or "").strip()
    if not text:
        return "codecare-ai"
    text = text.split("?", 1)[0].split("#", 1)[0].strip().rstrip("/")
    lowered = text.lower()
    if "://" in text or lowered.startswith("github.com/") or "/apps/" in lowered:
        text = text.rstrip("/").split("/")[-1]
    return text.strip() or "codecare-ai"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), str(_BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_secret: str = "dev-only-change-me"
    fernet_key: str = ""
    fernet_key_previous: str = ""
    public_base_url: str = "http://127.0.0.1:8000"
    api_cors_origins: str = "http://127.0.0.1:8000,http://localhost:8000"
    database_url: str = "sqlite:///./codecare-dev.db"
    redis_url: str = ""

    supabase_url: str = ""
    supabase_jwt_secret: str = ""
    supabase_anon_key: str = ""

    github_app_id: str = ""
    github_app_slug: str = "codecare-ai"
    github_app_client_id: str = ""
    github_app_client_secret: str = ""
    github_app_private_key: str = ""
    github_app_private_key_path: str = ""
    github_webhook_secret: str = ""
    github_oauth_scopes: str = "read:user,user:email"

    ai_provider: str = "auto"
    groq_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    sandbox_image: str = "codecare-sandbox:local"
    sandbox_timeout_seconds: int = 120
    sandbox_install_timeout_seconds: int = 180
    sandbox_memory: str = "512m"
    sandbox_cpus: str = "1"
    sandbox_pids_limit: int = 128
    # auto uses Docker when the engine and image exist, otherwise static checks.
    sandbox_mode: str = "auto"
    sandbox_remote_url: str = ""

    dodo_payments_api_key: str = ""
    dodo_payments_webhook_secret: str = ""
    dodo_payments_environment: str = "test_mode"
    dodo_product_starter: str = ""
    dodo_product_indie: str = ""
    dodo_product_studio: str = ""

    cookie_name: str = "codecare_session"
    cookie_domain: str = ""
    csrf_cookie_name: str = "codecare_csrf"
    session_hours: int = 12
    email_verification_hours: int = 24
    password_reset_hours: int = 1

    resend_api_key: str = ""
    email_from: str = "CodeCare <noreply@codecare.si>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "CodeCare <noreply@codecare.si>"
    smtp_tls: bool = True
    webhook_replay_seconds: int = 300
    disable_worker: bool = False
    demo_mode: bool = False
    scan_interval_hours: int = 24
    max_job_attempts: int = 3

    @field_validator("database_url", mode="before")
    @classmethod
    def _database_url(cls, value: str) -> str:
        return normalize_database_url(str(value or ""))

    @field_validator("github_app_slug", mode="before")
    @classmethod
    def _github_app_slug(cls, value: str) -> str:
        return normalize_github_app_slug(str(value or ""))

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.api_cors_origins.split(",") if item.strip()]

    @property
    def github_configured(self) -> bool:
        return bool(self.github_app_id and self.github_private_key_pem and self.github_app_client_id)

    @property
    def github_oauth_configured(self) -> bool:
        return bool(self.github_app_client_id and self.github_app_client_secret)

    @property
    def email_configured(self) -> bool:
        return bool(self.resend_api_key.strip() or self.smtp_host.strip())

    @property
    def dodo_configured(self) -> bool:
        return bool(
            self.dodo_payments_api_key
            and self.dodo_payments_environment in {"test_mode", "live_mode"}
            and self.dodo_product_starter
        )

    @property
    def billing_state(self) -> str:
        if self.demo_mode:
            return "demo"
        if self.dodo_configured:
            return "ready"
        return "coming_soon"

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_jwt_secret)

    @property
    def github_private_key_pem(self) -> str:
        if self.github_app_private_key_path:
            return Path(self.github_app_private_key_path).read_text(encoding="utf-8")
        return self.github_app_private_key.replace("\\n", "\n")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
