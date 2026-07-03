import os
from urllib.parse import urlparse


RAILWAY_PRODUCTION_HOST_MARKERS = (
    "proxy.rlwy.net",
    "railway.internal",
    "railway.app",
)


def is_railway_database_url(url: str | None) -> bool:
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    return any(marker in host for marker in RAILWAY_PRODUCTION_HOST_MARKERS)


def configure_database_url_for_pytest(
    environ: dict[str, str] | os._Environ[str],
) -> str | None:
    test_database_url = environ.get("TEST_DATABASE_URL", "").strip()
    database_url = environ.get("DATABASE_URL", "").strip()

    if test_database_url:
        if is_railway_database_url(test_database_url):
            raise RuntimeError(
                "pytest refused to start because TEST_DATABASE_URL points to Railway."
            )
        environ["DATABASE_URL"] = test_database_url
        environ.setdefault("APP_ENV", "testing")
        return test_database_url

    if is_railway_database_url(database_url):
        raise RuntimeError(
            "pytest refused to start because DATABASE_URL points to Railway. "
            "Set TEST_DATABASE_URL to a disposable local/staging database."
        )

    if database_url:
        raise RuntimeError(
            "pytest refused to start because DB-backed tests must use "
            "TEST_DATABASE_URL, not DATABASE_URL."
        )

    return None
