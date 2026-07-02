import os
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.environ["DATABASE_URL"]
COMPANIES_HOUSE_API_KEY: str = os.environ["COMPANIES_HOUSE_API_KEY"]
APP_ENV: str = os.getenv("APP_ENV", "production")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

_secret = os.getenv("SECRET_KEY", "").strip()
if not _secret:
    if APP_ENV == "production":
        raise RuntimeError(
            "SECRET_KEY must be set when APP_ENV=production. "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    import logging
    import secrets as _secrets

    _secret = _secrets.token_urlsafe(48)
    logging.getLogger(__name__).warning(
        "secret_key_ephemeral: SECRET_KEY env var is not set; generated an "
        "ephemeral key for this process (allowed because APP_ENV != production). "
        "Signed cookies will not survive an app restart."
    )
SECRET_KEY: str = _secret
RM_NAMES: list[str] = [
    name.strip() for name in os.getenv("RM_NAMES", "").split(",") if name.strip()
]
ACTOR_NAMES: list[str] = [
    name.strip() for name in os.getenv("ACTOR_NAMES", "").split(",") if name.strip()
]
ADMIN_ACTOR_NAMES: list[str] = [
    name.strip()
    for name in os.getenv("ADMIN_ACTOR_NAMES", "").split(",")
    if name.strip()
]
LEI_BACKFILL_CHUNK_SIZE: int = int(os.getenv("LEI_BACKFILL_CHUNK_SIZE", "500"))
ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "").strip()
CH_ENRICHMENT_BATCH_SIZE: int = int(os.getenv("CH_ENRICHMENT_BATCH_SIZE", "200"))
CH_ENRICHMENT_SAFE_LIMIT: int = int(os.getenv("CH_ENRICHMENT_SAFE_LIMIT", "5"))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


SCORING_SHADOW_MODE: bool = _env_bool("SCORING_SHADOW_MODE", True)
SCORING_DISPLAY_ENABLED: bool = _env_bool("SCORING_DISPLAY_ENABLED", False)
SHADOW_SCORE_ACTIVE_STALE_DAYS: int = int(
    os.getenv("SHADOW_SCORE_ACTIVE_STALE_DAYS", "120")
)
SHADOW_BACKFILL_BATCH_SIZE: int = int(os.getenv("SHADOW_BACKFILL_BATCH_SIZE", "100"))
SHADOW_BACKFILL_MAX_BATCHES: int = int(os.getenv("SHADOW_BACKFILL_MAX_BATCHES", "20"))
SHADOW_BACKFILL_LOCK_TIMEOUT_MS: int = int(
    os.getenv("SHADOW_BACKFILL_LOCK_TIMEOUT_MS", "3000")
)
ACTIVE_TERMINAL_STATUSES: tuple[str, ...] = tuple(
    item.strip()
    for item in os.getenv(
        "ACTIVE_TERMINAL_STATUSES", "Client,Closed - Not Fit,Not Fit,Archived"
    ).split(",")
    if item.strip()
)
PROSPECT_ENGINE_DEMO_MODE: bool = _env_bool("PROSPECT_ENGINE_DEMO_MODE", False)
PROSPECT_ENGINE_FOOTER_TEXT: str = os.getenv(
    "PROSPECT_ENGINE_FOOTER_TEXT",
    "Internal ARIE Finance working draft. Suggested opening angles are draft notes "
    "for RM review, not automated outreach. Regulatory wording requires approval "
    "before production use.",
)


def _assert_db_host_allowed() -> None:
    """Fail-closed guard: if ALLOWED_DB_HOSTS is set, the host parsed from
    DATABASE_URL must contain at least one of the comma-separated substrings.
    If ALLOWED_DB_HOSTS is unset or empty, no check is performed (opt-in)."""
    raw = os.getenv("ALLOWED_DB_HOSTS", "").strip()
    if not raw:
        return
    allowed = [s.strip().lower() for s in raw.split(",") if s.strip()]
    if not allowed:
        return
    host = (urlparse(DATABASE_URL).hostname or "").lower()
    if not any(token in host for token in allowed):
        raise RuntimeError(
            f"DATABASE_URL host {host!r} is not in ALLOWED_DB_HOSTS={allowed!r}; "
            "refusing to start. Set ALLOWED_DB_HOSTS to a substring matching the "
            "intended DB host for this environment, or unset it to disable the guard."
        )


_assert_db_host_allowed()
