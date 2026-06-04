import pytest
from fastapi.testclient import TestClient

from src import config, main
from src.main import _ACTOR_COOKIE, _actor_signer, app

client = TestClient(app)


@pytest.fixture
def signed_actor_cookie():
    actor = "isuda"
    signed = _actor_signer.sign(actor.encode("utf-8")).decode("ascii")
    return {_ACTOR_COOKIE: signed}


@pytest.fixture
def admin_token(monkeypatch):
    token = "test-admin-token-please-change"
    monkeypatch.setattr(config, "ADMIN_TOKEN", token)
    monkeypatch.setattr(main, "ADMIN_TOKEN", token)
    return token


def test_admin_lei_backfill_rejects_anonymous(admin_token):
    resp = client.post("/admin/lei-backfill")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Authentication required"


def test_admin_lei_backfill_rejects_actor_cookie(admin_token, signed_actor_cookie):
    """Signed actor cookie is for UI attribution only; it must not unlock admin."""
    resp = client.post("/admin/lei-backfill", cookies=signed_actor_cookie)
    assert resp.status_code == 401


def test_admin_lei_backfill_rejects_wrong_bearer(admin_token):
    resp = client.post(
        "/admin/lei-backfill",
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert resp.status_code == 401


def test_admin_lei_backfill_rejects_wrong_scheme(admin_token):
    resp = client.post(
        "/admin/lei-backfill",
        headers={"Authorization": f"Basic {admin_token}"},
    )
    assert resp.status_code == 401


def test_admin_lei_backfill_accepts_valid_bearer(admin_token):
    resp = client.post(
        "/admin/lei-backfill",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


def test_admin_lei_backfill_503_when_token_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    monkeypatch.setattr(main, "ADMIN_TOKEN", "")
    resp = client.post(
        "/admin/lei-backfill",
        headers={"Authorization": "Bearer anything"},
    )
    assert resp.status_code == 503


def test_admin_shadow_backfill_rejects_anonymous(admin_token):
    resp = client.post("/admin/shadow-scoring/backfill")
    assert resp.status_code == 401


def test_admin_shadow_backfill_accepts_valid_bearer(admin_token, monkeypatch):
    fake_conn = object()
    fake_conn_cm = type(
        "FakeConnCM",
        (),
        {
            "__enter__": lambda self: fake_conn,
            "__exit__": lambda self, exc_type, exc, tb: False,
        },
    )()
    monkeypatch.setattr(main, "get_conn", lambda: fake_conn_cm)
    monkeypatch.setattr(
        main,
        "backfill_active_shadow_scores",
        lambda *_args, **_kwargs: {
            "batches_processed": 1,
            "scanned": 1,
            "scored": 1,
            "failed": 0,
        },
    )
    resp = client.post(
        "/admin/shadow-scoring/backfill",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["scanned"] == 1

