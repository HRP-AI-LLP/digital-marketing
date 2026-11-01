from functools import lru_cache
from pathlib import Path

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def asyncpg_url(url: str) -> str:
    """Accept a plain libpq URL (postgresql://…?sslmode=require&channel_binding=require) and turn it into
    the SQLAlchemy asyncpg form (postgresql+asyncpg://…?ssl=require)."""
    url = (url or "").strip()
    if not url:
        return url
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    q = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if k == "sslmode":
            q.append(("ssl", "require" if v in ("require", "verify-ca", "verify-full", "prefer") else v))
        elif k in ("channel_binding", "options", "application_name") and scheme == "postgresql+asyncpg":
            continue
        else:
            q.append((k, v))
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))

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
    db_pool_size: int = 5

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def _asyncpg(cls, v: str) -> str:
        return asyncpg_url(v)

    secret_key: str = "change-me"
    encryption_key: str = ""  # optional Fernet key; derived from SECRET_KEY when empty
    access_token_minutes: int = 30
    refresh_token_days: int = 30

    platform_admin_email: str = "admin@hrpai.in"
    platform_admin_name: str = "Prashant"
    platform_admin_password: str = ""  # used only on first boot

    bootstrap_org: bool = True  # empty database: also create the first tenant (off in production: admin only)
    default_org_name: str = "HRP AI LLP"
    default_org_slug: str = "hrp-ai"
    default_timezone: str = "Asia/Kolkata"

    cors_origins: str = "http://localhost:5180,http://127.0.0.1:5180"

    api_port: int = 8600
    web_port: int = 5180
    # Where people sign in to the studio (put in welcome emails). Defaults to http://localhost:<web_port>.
    app_url: str = ""
    # Public HTTPS address that external services can reach (a Cloudflare Tunnel, e.g. https://studio.hrpai.in).
    # Only OAuth callbacks and signed media links are served through it; everything else is refused.
    public_base_url: str = ""
    # Sign-in callbacks that must use the public HTTPS address (Meta refuses http://localhost once an app is Live).
    # Google, LinkedIn and Reddit accept http://localhost, so they keep using it. X and Canva refuse "localhost"
    # callbacks (TikTok needs HTTPS too: add "tiktok" here if you switch it back on). Use "*" for all.
    oauth_https_providers: str = "meta,threads,pinterest,x,canva"
    # Platforms switched off for this studio (comma separated). TikTok is banned in India, so it is off by default:
    # it isn't offered for new accounts, posts or media, but existing accounts keep working. Set to "" to turn it on.
    disabled_platforms: str = "tiktok"
    storage_dir: str = str(ROOT_DIR / "storage")
    crawl_max_pages: int = 150
    worker_in_process: bool = False  # run the job worker inside the API process
    playwright_chromium_path: str = ""  # optional explicit Chromium path for media rendering
    # Webhooks: allow http://localhost / 127.0.0.1 addresses (for testing a local n8n). Private and internal
    # addresses are always refused.
    webhooks_allow_localhost: bool = False
    # API tokens: let requests that carry an API token (Authorization: Bearer hrp_...) through the public tunnel,
    # so cloud tools (Zapier, Make) can drive the studio. Off by default: only the local network can use tokens.
    api_tokens_via_tunnel: bool = False

    # ---- cloud (UAT / production on Cloudflare + Cloud Run + Neon Postgres + R2). Locally all of these stay empty.
    # "cloud": no tunnel guard (requests come through the Cloudflare Worker, which adds X-Origin-Verify), jobs run on
    # the scheduler's /internal/tick calls instead of a long-running worker, and laptop-only features are off.
    deploy_mode: str = "local"  # local | cloud
    origin_verify_secret: str = ""  # shared with the Worker; requests without it are refused in the cloud
    cron_secret: str = ""  # header X-Cron-Secret on POST /api/v1/internal/tick
    tick_seconds: int = 50  # how long one tick keeps claiming new jobs
    tick_every_minutes: int = 20  # the regular Cloud Scheduler tick (TICK_SCHEDULE); wake-ups fill the gaps
    tasks_queue: str = ""  # Cloud Tasks queue for exact-time wake-ups (cloud); empty = regular ticks only
    tick_url: str = ""  # optional: this service's URL for wake-ups (default: worked out from Cloud Run)
    gcp_project: str = ""
    gcp_region: str = ""
    # S3-compatible storage for media (Cloudflare R2). When set, the local folder becomes a cache.
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_region: str = "auto"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    # Stops every automatic action (posting, replies, metrics, crawls) on this copy of the app. Set to false on the
    # laptop once the cloud environment has taken over, so the same accounts are never driven twice.
    automation_enabled: bool = True
    cloud_nightly_backup: bool = False  # cloud: nightly pg_dump to R2 (off for now; Back up now still works)

    @property
    def cloud(self) -> bool:
        return self.deploy_mode.strip().lower() == "cloud"

    @property
    def api_base_url(self) -> str:
        if self.cloud and self.public_base_url:
            return self.public_base_url.rstrip("/").removesuffix("/api/v1")
        return f"http://localhost:{self.api_port}"

    @property
    def web_base_url(self) -> str:
        if self.cloud and (self.app_url or self.public_base_url):
            return (self.app_url or self.public_base_url).rstrip("/")
        return f"http://localhost:{self.web_port}"

    @property
    def sign_in_url(self) -> str:
        return (self.app_url or self.web_base_url).rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
