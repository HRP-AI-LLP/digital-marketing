from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]  # .../Digital Marketing


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "HRP Digital Marketing Studio"
    environment: str = "local"

    # App connects as a NON-superuser role so PostgreSQL row-level security is enforced.
    database_url: str = "postgresql+asyncpg://dm_app:dm_app@localhost:5433/digital_marketing"
    # Migrations run as the schema owner.
    migration_database_url: str = (
        "postgresql+asyncpg://dm_owner:dm_owner@localhost:5433/digital_marketing"
    )

    db_null_pool: bool = False  # tests: no pooled connections across event loops

    secret_key: str = "change-me"
    encryption_key: str = ""  # optional Fernet key; derived from SECRET_KEY when empty
    access_token_minutes: int = 30
    refresh_token_days: int = 30

    platform_admin_email: str = "admin@hrpai.in"
    platform_admin_name: str = "Prashant"
    platform_admin_password: str = ""  # used only on first boot

    default_org_name: str = "HRP AI LLP"
    default_org_slug: str = "hrp-ai"
    default_timezone: str = "Asia/Kolkata"

    cors_origins: str = "http://localhost:5180,http://127.0.0.1:5180"

    api_port: int = 8600
    web_port: int = 5180
    # Public HTTPS address that external services can reach (a Cloudflare Tunnel, e.g. https://studio.hrpai.in).
    # Only OAuth callbacks and signed media links are served through it; everything else is refused.
    public_base_url: str = ""
    # Sign-in callbacks that must use the public HTTPS address (Meta refuses http://localhost once an app is Live).
    # Google and LinkedIn accept http://localhost, so they keep using it. Use "*" for all.
    oauth_https_providers: str = "meta,threads,pinterest"
    storage_dir: str = str(ROOT_DIR / "storage")
    crawl_max_pages: int = 150
    worker_in_process: bool = False  # run the job worker inside the API process
    playwright_chromium_path: str = ""  # optional explicit Chromium path for media rendering

    @property
    def api_base_url(self) -> str:
        return f"http://localhost:{self.api_port}"

    @property
    def web_base_url(self) -> str:
        return f"http://localhost:{self.web_port}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
