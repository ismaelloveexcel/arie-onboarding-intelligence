from __future__ import annotations

from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


def sanitize_external_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    candidate = raw_url.strip()
    if not candidate:
        return None

    parsed = urlparse(candidate)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return None
    if not parsed.netloc:
        return None
    return candidate
