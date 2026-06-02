import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src import write_auth


def _request(path: str, auth_header: str | None = None) -> Request:
    headers = []
    if auth_header is not None:
        headers.append((b"authorization", auth_header.encode("utf-8")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def test_report_only_mode_allows_missing_auth(monkeypatch):
    monkeypatch.setattr(write_auth, "WRITE_AUTH_ENFORCE", False)
    monkeypatch.setattr(write_auth, "WRITE_AUTH_TOKEN", "write-token")
    req = _request("/leads/abc/action")
    write_auth.check_write_access(req, actor="isuda", actor_valid=True)


def test_enforced_mode_blocks_missing_auth(monkeypatch):
    monkeypatch.setattr(write_auth, "WRITE_AUTH_ENFORCE", True)
    monkeypatch.setattr(write_auth, "WRITE_AUTH_TOKEN", "write-token")
    req = _request("/leads/abc/action")
    with pytest.raises(HTTPException) as exc:
        write_auth.check_write_access(req, actor="isuda", actor_valid=True)
    assert exc.value.status_code == 401


def test_enforced_mode_allows_valid_bearer(monkeypatch):
    monkeypatch.setattr(write_auth, "WRITE_AUTH_ENFORCE", True)
    monkeypatch.setattr(write_auth, "WRITE_AUTH_TOKEN", "write-token")
    req = _request("/leads/abc/action", auth_header="Bearer write-token")
    write_auth.check_write_access(req, actor="isuda", actor_valid=True)
