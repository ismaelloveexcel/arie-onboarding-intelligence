from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from src.config import ACTOR_NAMES, SECRET_KEY

_ACTOR_COOKIE = "actor"
_ACTOR_MAX_AGE = 30 * 24 * 3600
_SIGNER = TimestampSigner(SECRET_KEY)

REGISTERED_MUTATION_HANDLERS: set[str] = set()


def _request_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Request:
    req = kwargs.get("request")
    if isinstance(req, Request):
        return req
    for arg in args:
        if isinstance(arg, Request):
            return arg
    raise RuntimeError("write_guard_required expects a FastAPI Request argument")


def _read_actor_from_cookie(request: Request) -> str:
    raw = request.cookies.get(_ACTOR_COOKIE)
    if not raw:
        return ""
    try:
        unsigned = _SIGNER.unsign(raw, max_age=_ACTOR_MAX_AGE)
    except (SignatureExpired, BadSignature):
        return ""
    actor = unsigned.decode("utf-8", errors="replace")
    if ACTOR_NAMES and actor not in ACTOR_NAMES:
        return ""
    return actor


def write_guard_required(func: Callable[..., Any]) -> Callable[..., Any]:
    registry_key = f"{func.__module__}:{func.__name__}"
    REGISTERED_MUTATION_HANDLERS.add(registry_key)

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        request = _request_from_call(args, kwargs)
        actor = _read_actor_from_cookie(request)
        if not actor:
            raise HTTPException(status_code=401, detail="Write authentication required")
        setattr(request.state, "write_actor", actor)
        return func(*args, **kwargs)

    # Preserve the wrapped handler's *resolved* signature. functools.wraps sets
    # __wrapped__ (so inspect.signature reads the original params) but cannot copy
    # __globals__ — the wrapper's globals belong to this module. With
    # `from __future__ import annotations` in the route modules, string
    # annotations like "UploadFile" would otherwise be resolved against this
    # module's globals and fail (ForwardRef('UploadFile')). Resolving them here
    # against the handler's own module keeps FastAPI dependency analysis working.
    try:
        wrapper.__signature__ = inspect.signature(func, eval_str=True)
    except (NameError, TypeError, ValueError):
        pass

    setattr(wrapper, "_write_guard_required", True)
    setattr(wrapper, "_write_guard_key", registry_key)
    return wrapper
