import pytest
from fastapi.testclient import TestClient
from src.main import app, _actor_signer, _ACTOR_COOKIE

client = TestClient(app)


@pytest.fixture
def signed_actor_cookie():
    actor = "isuda"
    signed = _actor_signer.sign(actor.encode("utf-8")).decode("ascii")
    return {_ACTOR_COOKIE: signed}


def test_admin_lei_backfill_requires_auth():
    resp = client.post("/admin/lei-backfill")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Authentication required"


def test_admin_lei_backfill_accepts_signed_actor(signed_actor_cookie):
    resp = client.post("/admin/lei-backfill", cookies=signed_actor_cookie)
    # Accepts, returns result (may be int or dict depending on impl)
    assert resp.status_code == 200
    assert resp.json() is not None or resp.text != ""
