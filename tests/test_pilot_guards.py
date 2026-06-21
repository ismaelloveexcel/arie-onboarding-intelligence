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
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src import main
from src.main import app, build_contact_research_links
from src.scoring import (
    contact_path_label,
    derive_next_action,
    derive_queue_next_action,
    introducer_route_hint,
    suggested_contact_route,
)

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
        ("Client", "ready_outreach", False, "Client relationship active"),
        ("Closed — Not Fit", "ready_outreach", False, "No further action"),
        ("Contacted", "research_required", False, "Follow up"),
        ("Opportunity", "no_contact_path", False, "Follow up"),
        ("New", "research_required", True, "Review introducer route"),
        ("New", "ready_outreach", False, "Ready to contact"),
        ("New", "no_contact_path", False, "Research contact route"),
        ("New", "research_required", False, "Verify contact details"),
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
    assert contact_path_label("ready_outreach") == "Ready to Contact"
    assert contact_path_label("research_required") == "Research Required"
    assert contact_path_label("no_contact_path") == "No Contact Route Yet"
    assert contact_path_label("anything_else") == "Research Required"


def test_queue_next_action_prioritises_ownership_and_route():
    assert (
        derive_queue_next_action(
            assigned_to=None,
            rm_status="New",
            reachability_status="ready_outreach",
            jurisdiction="UK",
            entity_type="ltd",
        )
        == "Assign RM"
    )
    assert (
        derive_queue_next_action(
            assigned_to="RM 1",
            rm_status="New",
            reachability_status="no_contact_path",
            jurisdiction="Mauritius",
            entity_type="Global Business Company",
        )
        == "Review introducer route"
    )


def test_suggested_contact_and_introducer_routes():
    assert (
        introducer_route_hint("Mauritius", "GBC")
        == "Research management company / CSP route"
    )
    assert introducer_route_hint("UK", "ltd") is None
    assert (
        suggested_contact_route(
            jurisdiction="UK",
            entity_type="ltd",
            reachability_status="research_required",
            has_officers=True,
            has_pscs=False,
        )
        == "Director / officer research"
    )


def test_contact_research_links_are_safe_manual_shortcuts():
    links = build_contact_research_links(
        company_name="Acme & Partners Ltd",
        jurisdiction="UK",
        source_ref="12345678",
        registered_address="1 High Street, London",
        verify_url="https://find-and-update.company-information.service.gov.uk/company/12345678",
        officers=[
            {"name": "Jane Doe", "resigned_on": None},
            {"name": "Former Director", "resigned_on": "2020-01-01"},
        ],
    )

    assert links["company"][0]["url"].startswith("https://www.google.com/search?")
    assert "Acme+%26+Partners+Ltd" in links["company"][0]["url"]
    assert [item["label"] for item in links["people"]] == [
        "Jane Doe on LinkedIn"
    ]
    assert links["registry"][0]["label"] == "Open official registry"
    assert any(item["label"] == "FCA context" for item in links["registry"])


def test_queue_contact_readiness_filter_and_badge():
    lead_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    row = (
        lead_id,
        "Example Company",
        "UK",
        "ltd",
        None,
        None,
        82,
        "HIGH",
        "Strong fit",
        None,
        "New",
        now,
        "no_contact_path",
    )

    cursor = MagicMock()
    cursor.fetchone.side_effect = [(1,), (now,)]
    cursor.fetchall.return_value = [row]
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_cm
    connection_cm = MagicMock()
    connection_cm.__enter__.return_value = connection

    with patch("src.main.get_conn", return_value=connection_cm):
        response = client.get("/?contact_readiness=no_contact_path")

    assert response.status_code == 200
    assert "No Contact Route Yet" in response.text
    assert "Assign RM" in response.text
    assert f'/leads/{lead_id}#find-contact-route' in response.text
    sql_calls = [str(call.args[0]) for call in cursor.execute.call_args_list]
    assert any("ls.reachability_status" in sql for sql in sql_calls)


def test_queue_contact_suggestion_filter_and_summary():
    lead_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    row = (
        lead_id,
        "Suggestion Test Company",
        "UK",
        "ltd",
        None,
        None,
        82,
        "HIGH",
        "Strong fit",
        None,
        "New",
        now,
        "no_contact_path",
        7,
        5,
    )
    cursor = MagicMock()
    cursor.fetchone.side_effect = [(1,), (now,)]
    cursor.fetchall.return_value = [row]
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_cm
    connection_cm = MagicMock()
    connection_cm.__enter__.return_value = connection

    with patch("src.main.get_conn", return_value=connection_cm):
        response = client.get("/?contact_suggestions=needs_review")

    assert response.status_code == 200
    assert "7 candidate routes" in response.text
    assert "5 to review" in response.text
    assert "Needs Candidate Review" in response.text
    sql_calls = [str(call.args[0]) for call in cursor.execute.call_args_list]
    assert any("contact_discovery_suggestions" in sql for sql in sql_calls)


def test_lead_detail_renders_contact_research_shortcuts():
    lead_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    lead_row = (
        lead_id,
        "Acme & Partners Ltd",
        "UK",
        "ltd",
        None,
        "1 High Street, London",
        "companies_house",
        "12345678",
        "https://find-and-update.company-information.service.gov.uk/company/12345678",
        None,
        82,
        "HIGH",
        [],
        "Strong cross-border fit",
        main.SCORING_VERSION,
        "RM 1",
        "New",
        None,
        None,
        None,
        82,
        82,
        "research_required",
        "discovered",
        "B",
        [],
        10,
        10,
        now,
        now,
    )
    officer_row = (
        uuid.uuid4(),
        "Jane Doe",
        "director",
        None,
        None,
        None,
        None,
        None,
    )
    cursor = MagicMock()
    cursor.fetchone.side_effect = [lead_row, None, None, None]
    cursor.fetchall.side_effect = [[], [], [officer_row], [], [], []]
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_cm
    connection_cm = MagicMock()
    connection_cm.__enter__.return_value = connection

    with patch("src.main.get_conn", return_value=connection_cm):
        response = client.get(f"/leads/{lead_id}")

    assert response.status_code == 200
    assert 'id="find-contact-route"' in response.text
    assert "Official website" in response.text
    assert "Jane Doe on LinkedIn" in response.text
    assert "Registered office route" in response.text
    assert 'target="_blank"' in response.text
    assert "nothing is fetched or verified automatically" in response.text.lower()


def test_mauritius_lead_separates_candidates_from_search_shortcuts():
    lead_id = uuid.UUID("6d1ad165-846d-43c8-ac0f-ab4cb4c60b4e")
    now = datetime.now(timezone.utc)
    lead_row = (
        lead_id,
        "Global Trading X (CFD)",
        "Mauritius",
        "GLOBAL BUSINESS COMPANY",
        None,
        None,
        "mauritius_mns",
        "C235321",
        "https://onlinesearch.mns.global/",
        None,
        95,
        "HIGH",
        [],
        "Strong fit",
        main.SCORING_VERSION,
        "Ismael",
        "Researching",
        "Research CSP route",
        None,
        None,
        95,
        95,
        "no_contact_path",
        "discovered",
        "C",
        [],
        10,
        10,
        now,
        now,
    )
    suggestion_rows = [
        (
            uuid.uuid4(),
            "registry",
            "https://onlinesearch.mns.global/",
            "Mauritius CBRD",
            "https://onlinesearch.mns.global/",
            '"Global Trading X (CFD)" C235321',
            "High",
            "Official registry route.",
            "Needs Review",
            now,
            None,
            None,
            None,
        ),
        (
            uuid.uuid4(),
            "website",
            "https://www.google.com/search?q=Global+Trading+X",
            "Official website search",
            "https://www.google.com/search?q=Global+Trading+X",
            '"Global Trading X (CFD)" official website',
            "Low",
            "Search route only.",
            "Needs Review",
            now,
            None,
            None,
            None,
        ),
    ]
    cursor = MagicMock()
    cursor.fetchone.side_effect = [lead_row, None, None, None]
    cursor.fetchall.side_effect = [[], [], [], [], suggestion_rows, []]
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_cm
    connection_cm = MagicMock()
    connection_cm.__enter__.return_value = connection

    with patch("src.main.get_conn", return_value=connection_cm):
        response = client.get(f"/leads/{lead_id}")

    assert response.status_code == 200
    assert "RM Action Summary" in response.text
    assert "Management Company Route Likely" in response.text
    assert "No named route candidate" in response.text
    assert "Registered office address" in response.text
    assert "Verification sources" in response.text
    assert "Research Shortcuts" in response.text
    assert "Accept as research route" not in response.text
    assert "2 awaiting review" not in response.text


def test_contact_research_rejects_invalid_values_before_db():
    lead_id = uuid.uuid4()
    bad_confidence = client.post(
        f"/leads/{lead_id}/contact-research",
        data={"confidence": "Certain"},
    )
    bad_url = client.post(
        f"/leads/{lead_id}/contact-research",
        data={"confidence": "Low", "website": "company.example"},
    )
    assert bad_confidence.status_code == 400
    assert bad_url.status_code == 400


@pytest.mark.parametrize(
    "missing_field",
    ["source", "confidence", "last_checked", "checked_by"],
)
def test_contact_research_requires_complete_provenance_before_db(missing_field):
    lead_id = uuid.uuid4()
    payload = {
        "website": "https://company.example",
        "source": "Company website",
        "confidence": "High",
        "last_checked": "2026-06-19",
        "checked_by": "Researcher",
    }
    payload[missing_field] = ""

    with patch("src.main.get_conn") as get_conn:
        response = client.post(
            f"/leads/{lead_id}/contact-research",
            data=payload,
        )

    assert response.status_code == 400
    get_conn.assert_not_called()


@pytest.mark.parametrize("decision", ["accept", "reject"])
def test_contact_suggestion_review_requires_selected_actor_before_db(decision):
    lead_id = uuid.uuid4()
    suggestion_id = uuid.uuid4()
    with (
        patch("src.main._read_actor", return_value=""),
        patch("src.main.get_conn") as get_conn,
    ):
        response = client.post(
            f"/leads/{lead_id}/contact-suggestions/{suggestion_id}/{decision}"
        )

    assert response.status_code == 400
    get_conn.assert_not_called()


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
def test_person_email_columns_exist():
    """Regression: lead detail selects company_officers.email / company_pscs.email;
    these must exist (migration f6a4b5c6d7e8 re-ensures them)."""
    from src.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            for table in ("company_officers", "company_pscs"):
                cur.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = %s AND column_name = 'email'",
                    (table,),
                )
                assert cur.fetchone() is not None, f"{table}.email missing"


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
