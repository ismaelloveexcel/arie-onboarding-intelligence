import os
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.environ["DATABASE_URL"]
COMPANIES_HOUSE_API_KEY: str = os.environ["COMPANIES_HOUSE_API_KEY"]
APP_ENV: str = os.getenv("APP_ENV", "production")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
RM_NAMES: list[str] = [
    name.strip()
    for name in os.getenv("RM_NAMES", "").split(",")
    if name.strip()
]
ACTOR_NAMES: list[str] = [
    name.strip()
    for name in os.getenv("ACTOR_NAMES", "").split(",")
    if name.strip()
]


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
