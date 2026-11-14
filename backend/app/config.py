from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://fred:fred@localhost:5432/fred"
    # Single-tenant until auth lands (Increment 3). Every query is still tenant-scoped.
    default_tenant_slug: str = "acme"
    cors_origins: list[str] = ["http://localhost:3000"]
    log_level: str = "info"
    model_cache: str | None = None  # where pre-trained model weights live (baked into the image at /models)
    cookie_secure: bool = False  # set true behind HTTPS
    session_days: int = 30
    allow_signup: bool = True
    secret_key: str | None = Field(None, validation_alias="FRED_SECRET_KEY")  # encrypts connector credentials
    stripe_api_base: str = "https://api.stripe.com/v1"
    hubspot_api_base: str = "https://api.hubapi.com"
    # Notifications. Links in Slack/email point here.
    public_url: str = "http://localhost:8080"
    smtp_host: str | None = None  # email is off until this is set
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "Fred <fred@localhost>"
    smtp_tls: str = "starttls"  # starttls | ssl | none
    # Slack webhooks may only point at Slack (stops the webhook field being used to call internal hosts).
    slack_webhook_hosts: list[str] = ["hooks.slack.com"]
    worker_interval_s: int = 60
    # OAuth apps for direct connections. A source shows "Connect" only when its app is configured.
    quickbooks_client_id: str | None = None
    quickbooks_client_secret: str | None = None
    quickbooks_sandbox: bool = False
    xero_client_id: str | None = None
    xero_client_secret: str | None = None
    salesforce_client_id: str | None = None
    salesforce_client_secret: str | None = None
    salesforce_login_url: str = "https://login.salesforce.com"  # https://test.salesforce.com for sandboxes
    gusto_client_id: str | None = None
    gusto_client_secret: str | None = None
    gusto_demo: bool = False
    google_ads_client_id: str | None = None
    google_ads_client_secret: str | None = None
    google_ads_developer_token: str | None = None
    google_ads_api_version: str = "v21"
    meta_graph_version: str = "v21.0"
    # Run the scheduler inside the API process (one service instead of two, e.g. on Render). Safe with
    # several API instances: each daily job is claimed in the database exactly once.
    embedded_worker: bool = False
    # Memory: analyses kept in memory (each holds trained models), and whether to pre-train at startup.
    analysis_cache: int = 8
    warm_on_start: bool = True

    @field_validator("database_url")
    @classmethod
    def _psycopg(cls, v: str) -> str:
        """Hosted Postgres hands out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver named."""
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @field_validator("public_url")
    @classmethod
    def _https(cls, v: str) -> str:
        """Accept a bare host (Render's fromService gives one) and assume https for it."""
        v = v.strip().rstrip("/")
        return v if v.startswith(("http://", "https://")) else f"https://{v}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
