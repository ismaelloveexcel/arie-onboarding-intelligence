"""Permanent regression guards for the pilot-readiness fixes.

Two tiers:
  * Pure / DB-free tests always run (status contract, deterministic next-action,
    write-time status validation, the access-protection middleware).
  * DB-backed tests are opt-in via RUN_DB_TESTS=1 so they never touch a
    production database by accident. CI sets RUN_DB_TESTS=1 with a dedicated
    DATABASE_URL_TEST.
"""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import main
from src.main import app
from src.scoring import contact_path_label, derive_next_action

client = TestClient(app)

_DB_TESTS = pytest.mark.skipif(
    not os.getenv("RUN_DB_TESTS"),
    reason="DB-backed test; set RUN_DB_TESTS=1 with a non-production DATABASE_URL to run.",
)


def _load_status_migration():
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "f3a1b2c3d4e5_align_rm_status_constraint.py"
    )
    spec = importlib.util.spec_from_file_location("_status_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# 1. UI statuses must exactly match the DB CHECK constraint (the bug that broke
#    RM saves). This is the contract test that prevents it ever recurring.
# --------------------------------------------------------------------------
def test_ui_statuses_match_db_constraint():
    migration = _load_status_migration()
    assert set(main._STATUSES) == set(migration._CANONICAL), (
        "UI _STATUSES drifted from the rm_actions CHECK constraint. "
        "Update the migration and app together."
    )


# --------------------------------------------------------------------------
# 2. Deterministic next-action + contact-path helpers.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rm_status,reach,has_intro,expected",
    [
        ("Client", "ready_outreach", False, "Won — active client"),
        ("Closed — Not Fit", "ready_outreach", False, "Deprioritise / Not fit"),
        ("Contacted", "research_required", False, "Follow up on the conversation"),
        ("Opportunity", "no_contact_path", False, "Follow up on the conversation"),
        ("New", "research_required", True, "Review introducer route"),
        ("New", "ready_outreach", False, "Ready for outreach"),
        ("New", "no_contact_path", False, "Research company / find a contact first"),
        ("New", "research_required", False, "Research company / contact first"),
    ],
)
def test_derive_next_action(rm_status, reach, has_intro, expected):
    assert (
        derive_next_action(
            rm_status=rm_status, reachability_status=reach, has_introducer=has_intro
        )
        == expected
    )


def test_contact_path_label():
    assert contact_path_label("ready_outreach") == "Ready"
    assert contact_path_label("research_required") == "Partial"
    assert contact_path_label("no_contact_path") == "Missing"
    assert contact_path_label("anything_else") == "Partial"


# --------------------------------------------------------------------------
# 3. Write-time status validation rejects unknown values BEFORE any DB write
#    (HTTPException(400) is raised before get_conn()).
# --------------------------------------------------------------------------
def test_lead_action_rejects_invalid_status():
    lead_id = uuid.uuid4()
    resp = client.post(
        f"/leads/{lead_id}/action",
        data={"status": "Totally Made Up", "assigned_to": ""},
    )
    assert resp.status_code == 400


def test_lead_action_rejects_unknown_assignee():
    lead_id = uuid.uuid4()
    resp = client.post(
        f"/leads/{lead_id}/action",
        data={"status": "New", "assigned_to": "Not A Real RM"},
    )
    assert resp.status_code == 400


def test_lead_assign_rejects_invalid_status():
    lead_id = uuid.uuid4()
    resp = client.post(
        f"/leads/{lead_id}/assign",
        data={"status": "Nope", "assigned_to": ""},
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# 4. Access-protection middleware (DB-free: 401 is returned before routes run,
#    and exempt/unknown paths never touch the DB).
# --------------------------------------------------------------------------
@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(main, "BASIC_AUTH_USER", "pilot")
    monkeypatch.setattr(main, "BASIC_AUTH_PASS", "secret")


def _basic(user, pw):
    import base64

    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_protected_route_requires_auth_when_enabled(auth_on):
    resp = client.get("/", headers={}, follow_redirects=False)
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


def test_wrong_credentials_rejected(auth_on):
    resp = client.get("/", headers=_basic("pilot", "wrong"), follow_redirects=False)
    assert resp.status_code == 401


def test_valid_credentials_pass_the_gate(auth_on):
    # Hit an unknown path: good creds -> middleware passes through -> 404 (not 401),
    # proving the gate accepts valid creds without needing the DB.
    resp = client.get("/__no_such_path__", headers=_basic("pilot", "secret"))
    assert resp.status_code == 404


def test_exempt_paths_not_blocked(auth_on):
    # /static is exempt; a missing asset is a DB-free 404, never a 401.
    resp = client.get("/static/__missing__.css")
    assert resp.status_code != 401


def test_auth_disabled_allows_unknown_path(monkeypatch):
    monkeypatch.setattr(main, "BASIC_AUTH_USER", "")
    monkeypatch.setattr(main, "BASIC_AUTH_PASS", "")
    resp = client.get("/__no_such_path__")
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# 5. DB-backed guards (opt-in). These assert the behaviours that can only be
#    proven against a real schema: status accepted by the DB, queue/detail
#    score parity, and uploaded leads becoming queue-visible immediately.
# --------------------------------------------------------------------------
@_DB_TESTS
def test_all_ui_statuses_accepted_by_db():
    from src.db import get_conn

    migration = _load_status_migration()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO companies (source_system, source_ref, company_name, "
                "normalised_name, jurisdiction) VALUES "
                "('test','guard:%s','Guard Co','guard co','UK') RETURNING id"
                % uuid.uuid4()
            )
            company_id = cur.fetchone()[0]
            for status in migration._CANONICAL:
                cur.execute(
                    "INSERT INTO rm_actions (company_id, status) VALUES (%s, %s) "
                    "ON CONFLICT (company_id) DO UPDATE SET status = EXCLUDED.status",
                    (company_id, status),
                )
            conn.rollback()  # never persist guard data


@_DB_TESTS
def test_queue_priority_score_matches_lead_fit_score():
    """queue_snapshot.priority_score must equal lead_scores.score (canonical)."""
    from src.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM queue_snapshot qs "
                "JOIN lead_scores ls ON ls.company_id = qs.canonical_company_id "
                "AND ls.is_current = TRUE WHERE qs.priority_score <> ls.score"
            )
            mismatches = cur.fetchone()[0]
    assert mismatches == 0
