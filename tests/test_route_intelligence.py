"""DB-free tests for deterministic Route Intelligence v1."""

import inspect
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from scripts.route_intelligence import _args
from src import route_intelligence
from src.main import app
from src.route_intelligence import (
    build_registered_office_clusters,
    build_route_recommendation,
    match_introducers,
    normalise_address,
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
