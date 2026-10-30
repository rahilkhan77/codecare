from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore")

    app_env: str = "development"
    app_secret: str = "dev-only-change-me"
    credentials_encryption_key: str = ""
    credentials_encryption_key_previous: str = ""
    public_base_url: str = "http://127.0.0.1:8000"
    api_cors_origins: str = "http://127.0.0.1:8000,http://localhost:8000"
    database_url: str = "sqlite:///./codecare-dev.db"
    redis_url: str = ""

    supabase_url: str = ""
    supabase_jwt_secret: str = ""
    supabase_anon_key: str = ""

    github_app_id: str = ""
    github_app_slug: str = ""
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
    groq_model: str = "llama-3.1-8b-instant"

    sandbox_image: str = "codecare-sandbox:local"
    sandbox_timeout_seconds: int = 120
    sandbox_install_timeout_seconds: int = 180
    sandbox_memory: str = "512m"
    sandbox_cpus: str = "1"
    sandbox_pids_limit: int = 128

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_starter: str = ""
    stripe_price_pro: str = ""
    stripe_price_team: str = ""

    cookie_name: str = "codecare_session"
    session_hours: int = 12
    webhook_replay_seconds: int = 300
    disable_worker: bool = False
    scan_interval_hours: int = 24
    max_job_attempts: int = 3

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.api_cors_origins.split(",") if item.strip()]

    @property
    def github_configured(self) -> bool:
        return bool(self.github_app_id and self.github_private_key_pem and self.github_app_client_id)

    @property
    def stripe_configured(self) -> bool:
        return bool(self.stripe_secret_key and self.stripe_price_starter)

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
