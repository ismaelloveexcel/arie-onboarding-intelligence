"""Write-route authentication telemetry and optional enforcement."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Callable

from fastapi import HTTPException, Request

from src.config import ADMIN_TOKEN, WRITE_AUTH_ENFORCE, WRITE_AUTH_TOKEN

logger = logging.getLogger(__name__)


def _ip_hash(request: Request) -> str:
    host = request.client.host if request.client else ""
    if not host:
        return ""
    return hashlib.sha256(host.encode("utf-8")).hexdigest()[:16]


def check_write_access(
    request: Request,
    actor: str,
    actor_valid: bool,
    token_validator: Callable[[str], bool] | None = None,
) -> None:
    """Log write-auth telemetry for every mutation route.

    In phase 1, this is report-only by default. Set WRITE_AUTH_ENFORCE=true to
    start blocking unauthorized write requests.
    """

    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    has_bearer = scheme.lower() == "bearer" and bool(presented)

    token = WRITE_AUTH_TOKEN or ADMIN_TOKEN
    if token_validator is not None:
        token_valid = token_validator(presented) if has_bearer else False
    else:
        token_valid = bool(token and has_bearer and hmac.compare_digest(presented, token))

    auth_valid = token_valid
    if WRITE_AUTH_ENFORCE and not auth_valid:
        outcome = "blocked"
    elif WRITE_AUTH_ENFORCE and auth_valid:
        outcome = "allowed_enforced"
    else:
        outcome = "allowed_report_only"

    logger.info(
        "write_auth_check",
        extra={
            "event": "write_auth_check",
            "route": request.url.path,
            "method": request.method,
            "actor": actor or "unknown",
            "actor_valid": actor_valid,
            "auth_present": has_bearer,
            "auth_valid": auth_valid,
            "write_auth_enforced": WRITE_AUTH_ENFORCE,
            "outcome": outcome,
            "status_code": 401 if outcome == "blocked" else 200,
            "request_id": request.headers.get("x-request-id", ""),
            "ip_hash": _ip_hash(request),
        },
    )

    if outcome == "blocked":
        raise HTTPException(status_code=401, detail="Authentication required")
