"""DB-free tests for deterministic Route Intelligence v1."""

import inspect
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from scripts.route_intelligence import _args
from src import route_intelligence
from src.config import ACTOR_NAMES
from src.main import app
from src.security.write_auth import _SIGNER
from src.route_intelligence import (
    CONTACTABILITY_LABELS,
    CONTACTABILITY_STATUS_LABELS,
    build_registered_office_clusters,
    build_route_recommendation,
    contactability_decision,
    contactability_status,
    contactability_status_label,
    match_introducers,
    normalise_address,
    suggested_opener,
)

client = TestClient(app)


def _lead(**overrides):
    item = {
        "company_id": "00000000-0000-0000-0000-000000000001",
        "company_name": "Example Holdings Limited",
        "jurisdiction": "Mauritius",
        "entity_type": "GLOBAL BUSINESS COMPANY",
        "registered_address": None,
        "verify_url": "https://onlinesearch.mns.global/",
        "website": None,
        "generic_email": None,
        "contact_form_url": None,
        "linkedin_url": None,
        "contact_confidence": None,
    }
    item.update(overrides)
    return item


def _introducer(**overrides):
    item = {
        "id": "00000000-0000-0000-0000-000000000002",
        "company_name": "Example Corporate Services Limited",
        "category": "CSP",
        "address": "4th Floor, Example Tower, Ebene, Mauritius",
        "contact_email": "info@examplecsp.mu",
        "verify_url": "https://examplecsp.mu/",
        "source": "Internal introducer list",
    }
    item.update(overrides)
    return item


def test_registered_office_normalisation_and_clustering():
    assert normalise_address("4th Floor, Example Tower, Ebene, Mauritius") == (
        "4th fl example tower ebene"
    )
    clusters = build_registered_office_clusters(
        [
            _lead(company_id="1", registered_address="4th Floor, Example Tower"),
            _lead(company_id="2", registered_address="4th floor example tower"),
            _lead(company_id="3", registered_address=None),
        ]
    )
    assert list(clusters.values()) == [["1", "2"]]


def test_exact_address_match_is_high_confidence():
    matches = match_introducers(
        _lead(registered_address="4th Floor, Example Tower, Ebene"),
        [_introducer()],
    )
    assert len(matches) == 1
    assert matches[0]["match_type"] == "address"
    assert matches[0]["match_strength"] == "high"


def test_unrelated_introducer_is_not_invented_as_candidate():
    assert match_introducers(_lead(), [_introducer()]) == []


def test_direct_contact_route_ranks_first():
    recommendation = build_route_recommendation(
        lead=_lead(
            generic_email="info@example.test",
            contact_confidence="high",
        ),
        introducer_matches=[],
    )
    assert recommendation["contactability_bucket"] == "ready_to_contact"
    assert recommendation["best_route_type"] == "direct"
    assert recommendation["confidence"] == "high"


def test_introducer_match_becomes_named_route():
    lead = _lead()
    matches = match_introducers(
        lead,
        [_introducer(company_name="Example Holdings Limited")],
    )
    recommendation = build_route_recommendation(
        lead=lead,
        introducer_matches=matches,
    )
    assert recommendation["contactability_bucket"] == "route_via_introducer_csp"
    assert recommendation["best_route_value"] == "Example Holdings Limited"
    assert recommendation["route_candidate_id"] == matches[0]["introducer_id"]


def test_mauritius_gbc_without_candidate_is_honest():
    recommendation = build_route_recommendation(
        lead=_lead(),
        introducer_matches=[],
    )
    assert (
        recommendation["contactability_bucket"]
        == "management_company_route_likely"
    )
    assert recommendation["best_route_value"] is None
    assert "Registered office address" in recommendation["missing_data"]


def test_operator_requires_bounded_target_and_enforces_caps():
    with pytest.raises(SystemExit):
        _args([])
    with pytest.raises(SystemExit):
        _args(["--top-mauritius", "51"])
    with pytest.raises(SystemExit):
        _args(["--top-high-fit", "101"])
    args = _args(["--seeded"])
    assert args.write is False


@pytest.mark.parametrize(
    ("bucket", "expected_status"),
    [
        ("ready_to_contact", "ready_to_contact"),
        ("route_via_introducer_csp", "route_via_introducer"),
        ("direct_candidate_found", "research_required"),
        ("management_company_route_likely", "research_required"),
        ("registry_evidence_only", "research_required"),
        ("needs_route_research", "research_required"),
        ("no_usable_route", "no_compliant_route_found"),
    ],
)
def test_contactability_status_projects_every_internal_bucket(bucket, expected_status):
    assert contactability_status(bucket) == expected_status
    assert contactability_status_label(bucket) == CONTACTABILITY_STATUS_LABELS[expected_status]


def test_contactability_status_covers_all_internal_buckets():
    # Every rich internal bucket must have an explicit projection — no bucket
    # may silently fall through to the unknown default.
    for bucket in CONTACTABILITY_LABELS:
        assert bucket in route_intelligence._BUCKET_TO_STATUS


def test_contactability_status_unknown_defaults_to_research_required():
    assert contactability_status(None) == "research_required"
    assert contactability_status("") == "research_required"
    assert contactability_status("not_a_real_bucket") == "research_required"


@pytest.mark.parametrize(
    ("bucket", "expected_decision"),
    [
        ("ready_to_contact", "Contact Now"),
        ("route_via_introducer_csp", "Route via Introducer"),
        ("direct_candidate_found", "Research First"),
        ("needs_route_research", "Research First"),
        ("no_usable_route", "Do Not Contact Yet"),
    ],
)
def test_contactability_decision(bucket, expected_decision):
    assert contactability_decision(bucket) == expected_decision


def test_suggested_opener_only_for_actionable_routes():
    # No opener when there is no usable route — never invent one.
    for bucket in (
        "direct_candidate_found",
        "management_company_route_likely",
        "registry_evidence_only",
        "needs_route_research",
        "no_usable_route",
        None,
    ):
        assert (
            suggested_opener(
                company_name="Example Holdings Limited",
                entity_type="GBC",
                jurisdiction="Mauritius",
                contactability_bucket=bucket,
            )
            is None
        )


def test_suggested_opener_ready_route_is_draft_and_factual():
    opener = suggested_opener(
        company_name="Example Holdings Limited",
        entity_type="GLOBAL BUSINESS COMPANY",
        jurisdiction="Mauritius",
        contactability_bucket="ready_to_contact",
    )
    assert opener is not None
    assert opener.startswith("Draft for RM review")
    assert "Example Holdings Limited" in opener
    # Must not fabricate contact details.
    assert "@" not in opener


def test_suggested_opener_introducer_route_names_the_route():
    opener = suggested_opener(
        company_name="Acme Fund Ltd",
        entity_type="FUND",
        jurisdiction="Mauritius",
        contactability_bucket="route_via_introducer_csp",
        best_route_value="Example Corporate Services Limited",
    )
    assert opener is not None
    assert opener.startswith("Draft for RM review")
    assert "Example Corporate Services Limited" in opener


def test_route_intelligence_has_no_fetch_outreach_or_scoring_logic():
    source = inspect.getsource(route_intelligence)
    assert "requests" not in source
    assert "httpx" not in source
    assert "smtplib" not in source
    assert "send_email" not in source
    assert "linkedin.com/in/" not in source
    assert "lead_score" not in source


@pytest.mark.parametrize("decision", ["accept", "reject"])
def test_route_review_requires_actor_before_db(decision):
    lead_id = uuid.uuid4()
    recommendation_id = uuid.uuid4()
    with (
        patch("src.main._read_actor", return_value=""),
        patch("src.main.get_conn") as get_conn,
    ):
        response = client.post(
            f"/leads/{lead_id}/route-recommendations/{recommendation_id}/{decision}"
        )

    assert response.status_code == 400
    get_conn.assert_not_called()


@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [("accept", "accepted"), ("reject", "rejected")],
)
def test_route_review_updates_history_and_audit_only(decision, expected_status):
    lead_id = uuid.uuid4()
    recommendation_id = uuid.uuid4()
    introducer_id = uuid.uuid4()
    cursor = MagicMock()
    cursor.fetchone.return_value = (
        "suggested",
        introducer_id,
        "route_via_introducer_csp",
        "introducer",
        "Example Corporate Services Limited",
        ["Exact registered-office match"],
    )
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_cm
    connection_cm = MagicMock()
    connection_cm.__enter__.return_value = connection

    with (
        patch("src.main._read_actor", return_value="Ismael"),
        patch("src.main.get_conn", return_value=connection_cm),
    ):
        response = client.post(
            f"/leads/{lead_id}/route-recommendations/{recommendation_id}/{decision}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    sql = "\n".join(str(call.args[0]) for call in cursor.execute.call_args_list)
    assert "UPDATE route_recommendations" in sql
    assert "UPDATE introducer_matches" in sql
    assert "INSERT INTO audit_log" in sql
    assert "lead_scores" not in sql
    assert "rm_actions" not in sql
    assert "outreach" not in sql.lower()
    assert any(
        expected_status in call.args[1]
        for call in cursor.execute.call_args_list
        if len(call.args) > 1 and isinstance(call.args[1], tuple)
    )
    connection.commit.assert_called_once()


# --- Lead-detail Action Recommendation panel rendering ----------------------

def _lead_detail_conn_mock(*, route_recommendation_row):
    """Mock get_conn() for the lead_detail route.

    Call order: fetchone(lead) -> fetchall(audit) -> fetchone(lei) ->
    fetchall(officers) -> fetchall(pscs) -> fetchall(contacts) ->
    fetchall(timeline) -> fetchone(route_rec) -> fetchall(introducer_matches).
    """
    lead_row = (
        "00000000-0000-0000-0000-000000000001",  # id
        "Example Holdings Limited",               # company_name
        "Mauritius",                              # jurisdiction
        "GLOBAL BUSINESS COMPANY",                # entity_type
        None,                                     # incorporation_date
        "Cybercity, Ebene",                       # registered_address
        "mauritius_mns",                          # source_system
        "C12345",                                 # source_ref
        "https://onlinesearch.mns.global/",       # verify_url
        None,                                     # website
        82,                                       # score
        "HIGH",                                   # tier
        ["FRESH_LEI"],                            # reason_codes
        "Fresh LEI registration",                 # reason_summary
        "v1",                                     # scoring_version
        "Ismael",                                 # assigned_to
        "new",                                    # status
        "",                                       # notes
        None,                                     # contacted_at
        None,                                     # follow_up_at
        None,                                     # next_action
        None,                                     # next_action_due_date
        None,                                     # feedback
        None,                                     # feedback_note
    )
    cur = MagicMock()
    cur.fetchone.side_effect = [lead_row, None, route_recommendation_row]
    cur.fetchall.side_effect = [[], [], [], [], [], []]

    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cur
    cursor_cm.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor_cm
    conn_cm = MagicMock()
    conn_cm.__enter__.return_value = conn
    conn_cm.__exit__.return_value = False
    return conn_cm


def test_lead_detail_renders_action_recommendation_panel():
    route_row = (
        uuid.uuid4(),                       # id
        "ready_to_contact",                 # contactability_bucket
        "direct",                           # best_route_type
        "info@example.com",                 # best_route_value
        None,                               # route_candidate_id
        "A company-level email is stored.", # rationale
        ["Stored company contact route with provenance."],  # evidence_summary
        [],                                 # missing_data
        "RM to verify the saved route.",    # next_action
        "high",                             # confidence
        "route-intelligence-v1",            # generated_by
        datetime(2026, 6, 1, tzinfo=timezone.utc),  # generated_at
        None,                               # reviewed_by
        None,                               # reviewed_at
        "suggested",                        # status
        None,                               # secondary_contact_route
        None,                               # route_source_url
        "MNS registry",                     # route_source_label
        "registry",                         # route_source_type
        "system_detected",                  # route_entry_method
        None,                               # route_last_checked_at
    )
    with patch(
        "src.main.get_conn",
        return_value=_lead_detail_conn_mock(route_recommendation_row=route_row),
    ):
        resp = client.get("/leads/00000000-0000-0000-0000-000000000001")

    assert resp.status_code == 200
    assert "Recommended decision" in resp.text
    assert "Contact Now" in resp.text
    assert "Suggested RM next action" in resp.text
    # Opener is offered for a ready route, clearly flagged as a draft.
    assert "Suggested opener" in resp.text
    # RM feedback capture + route provenance are exposed.
    assert "RM Feedback" in resp.text
    assert "Provenance" in resp.text
    assert "MNS registry" in resp.text  # route_source_label


def test_lead_detail_without_route_intelligence_shows_fallback():
    with patch(
        "src.main.get_conn",
        return_value=_lead_detail_conn_mock(route_recommendation_row=None),
    ):
        resp = client.get("/leads/00000000-0000-0000-0000-000000000001")

    assert resp.status_code == 200
    assert "No route intelligence generated for this lead yet" in resp.text


# --- RM feedback update flow ------------------------------------------------

def _signed_actor_cookie():
    actor = ACTOR_NAMES[0] if ACTOR_NAMES else "pilot-user"
    return _SIGNER.sign(actor.encode("utf-8")).decode("ascii")


def test_lead_feedback_update_flow_writes_rm_actions_and_audit():
    lead_id = uuid.uuid4()
    cursor = MagicMock()
    cursor.fetchone.return_value = (None, None, None, None)  # existing feedback row
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_cm
    connection_cm = MagicMock()
    connection_cm.__enter__.return_value = connection

    client.cookies.set("actor", _signed_actor_cookie())
    try:
        with patch("src.main.get_conn", return_value=connection_cm):
            response = client.post(
                f"/leads/{lead_id}/feedback",
                data={
                    "feedback": "meeting_booked",
                    "feedback_note": "Intro call booked for next week",
                    "next_action": "Send proposal pack",
                    "next_action_due_date": "2026-07-01",
                },
                follow_redirects=False,
            )
    finally:
        client.cookies.clear()

    assert response.status_code == 303
    sql = "\n".join(str(call.args[0]) for call in cursor.execute.call_args_list)
    assert "INSERT INTO rm_actions" in sql
    assert "INSERT INTO audit_log" in sql
    assert "lead_scores" not in sql
    assert "outreach" not in sql.lower()
    assert any(
        "meeting_booked" in call.args[1]
        for call in cursor.execute.call_args_list
        if len(call.args) > 1 and isinstance(call.args[1], tuple)
    )
    connection.commit.assert_called_once()


def test_lead_feedback_rejects_unknown_value_before_db():
    lead_id = uuid.uuid4()
    client.cookies.set("actor", _signed_actor_cookie())
    try:
        with patch("src.main.get_conn") as get_conn:
            response = client.post(
                f"/leads/{lead_id}/feedback",
                data={"feedback": "definitely_not_valid"},
                follow_redirects=False,
            )
    finally:
        client.cookies.clear()

    assert response.status_code == 422
    get_conn.assert_not_called()


def test_lead_feedback_requires_write_actor():
    lead_id = uuid.uuid4()
    with patch("src.main.get_conn") as get_conn:
        response = client.post(
            f"/leads/{lead_id}/feedback",
            data={"feedback": "useful"},
            follow_redirects=False,
        )
    assert response.status_code == 401
    get_conn.assert_not_called()
