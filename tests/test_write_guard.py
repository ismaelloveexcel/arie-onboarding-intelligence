from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from scripts.pilot_gates.check_write_guard import check_paths
from src.config import ACTOR_NAMES
from src.main import app
from src.security.write_auth import REGISTERED_MUTATION_HANDLERS, _SIGNER, write_guard_required

_MUTATION_VERBS = ("post", "patch", "put", "delete", "assign", "action", "upload", "contact")


def _request_with_cookie(cookie_value: str | None) -> Request:
    headers = []
    if cookie_value is not None:
        headers.append((b"cookie", f"actor={cookie_value}".encode("utf-8")))
    return Request({"type": "http", "headers": headers})


def _valid_signed_actor() -> str:
    actor = ACTOR_NAMES[0] if ACTOR_NAMES else "pilot-user"
    return _SIGNER.sign(actor.encode("utf-8")).decode("ascii")


def test_write_guard_rejects_missing_actor_cookie():
    @write_guard_required
    def guarded(request: Request):
        return "ok"

    with pytest.raises(HTTPException) as exc:
        guarded(_request_with_cookie(None))
    assert exc.value.status_code == 401


def test_write_guard_accepts_valid_signed_actor_cookie():
    @write_guard_required
    def guarded(request: Request):
        return "ok"

    assert guarded(_request_with_cookie(_valid_signed_actor())) == "ok"


def test_runtime_registry_covers_mutation_routes():
    mutation_routes = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = {method.upper() for method in route.methods}
        if not methods & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        if route.path.startswith("/admin") or route.path == "/me":
            continue
        if not any(verb in route.endpoint.__name__.lower() for verb in _MUTATION_VERBS):
            continue
        mutation_routes.append(route)

    assert mutation_routes, "No mutation routes discovered for registry test"
    missing_decorators = [
        route.path
        for route in mutation_routes
        if not getattr(route.endpoint, "_write_guard_required", False)
    ]
    assert not missing_decorators, f"Missing write guard on: {missing_decorators}"

    missing_registry = []
    for route in mutation_routes:
        key = f"{route.endpoint.__module__}:{route.endpoint.__name__}"
        if key not in REGISTERED_MUTATION_HANDLERS:
            missing_registry.append((route.path, key))
    assert not missing_registry, f"Missing registry entries: {missing_registry}"


def test_write_guard_ast_scan_passes():
    repo_root = Path(__file__).resolve().parents[1]
    failures = check_paths(
        [repo_root / "src" / "main.py", repo_root / "src" / "introducers.py"]
    )
    assert failures == []
