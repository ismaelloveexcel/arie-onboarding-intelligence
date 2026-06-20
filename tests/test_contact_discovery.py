"""DB-free safety and review tests for Contact Discovery Suggestions."""

from datetime import datetime, timezone
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src import main
from src.contact_discovery import (
    acceptance_target,
    build_contact_discovery_suggestions,
    prepare_contact_acceptance,
    review_status,
)

client = TestClient(main.app)


def _suggestion(**overrides):
    item = {
        "suggestion_type": "website",
        "suggested_value": "https://example-company.test/",
        "source_name": "Official company website",
        "source_url": "https://example-company.test/",
        "confidence": "High",
        "status": "Needs Review",
    }
    item.update(overrides)
    return item


def test_discovery_builds_explainable_public_source_routes():
    suggestions = build_contact_discovery_suggestions(
        company_id="00000000-0000-0000-0000-000000000001",
        company_name="Example Global Fund Ltd",
        jurisdiction="Mauritius",
        source_ref="C123456",
        registered_address="1 Example Street, Ebene",
        verify_url="https://onlinesearch.mns.global/",
        officer_names=["Jane Example"],
    )

    types = {item["suggestion_type"] for item in suggestions}
    assert types == {
        "website",
        "contact_page",
        "company_linkedin",
        "registry",
        "regulator",
        "csp_route",
        "introducer_route",
    }
    assert all(item["status"] == "Needs Review" for item in suggestions)
    assert all(item["confidence_reason"] for item in suggestions)
    assert len({item["fingerprint"] for item in suggestions}) == len(suggestions)


def test_search_routes_do_not_populate_contact_research():
    google = "https://www.google.com/search?q=Example+Company"
    assert acceptance_target("website", google) is None
    assert acceptance_target("contact_page", google) is None
    assert acceptance_target("company_linkedin", google) is None


def test_only_company_linkedin_pages_are_acceptable():
    assert (
        acceptance_target(
            "company_linkedin", "https://www.linkedin.com/company/example-company/"
        )
        == "linkedin_url"
    )
    assert acceptance_target("company_linkedin", "https://linkedin.com/in/person") is None


def test_personal_mailboxes_are_not_accepted_as_generic_company_email():
    assert acceptance_target("generic_email", "info@example-company.test") == "generic_email"
    assert acceptance_target("generic_email", "person@gmail.com") is None


def test_acceptance_populates_one_company_field_and_provenance():
    reviewed_at = datetime(2026, 6, 20, tzinfo=timezone.utc)
    merged, target = prepare_contact_acceptance(
        existing={"generic_email": "info@existing.test"},
        suggestion=_suggestion(),
        reviewer="Ismael",
        reviewed_at=reviewed_at,
    )

    assert target == "website"
    assert merged["website"] == "https://example-company.test/"
    assert merged["generic_email"] == "info@existing.test"
    assert merged["confidence"] == "1.0"
    assert merged["verified_at"] == "2026-06-20"
    assert merged["checked_by"] == "Ismael"


def test_accepting_search_route_changes_no_contact_field():
    merged, target = prepare_contact_acceptance(
        existing={"website": None},
        suggestion=_suggestion(
            suggested_value="https://www.google.com/search?q=Example",
            source_url="https://www.google.com/search?q=Example",
            confidence="Low",
        ),
        reviewer="Ismael",
    )
    assert target is None
    assert merged == {"website": None}


def test_review_status_is_append_only_and_single_decision():
    assert review_status("Needs Review", "Accepted") == "Accepted"
    assert review_status("Needs Review", "Rejected") == "Rejected"
    with pytest.raises(ValueError):
        review_status("Accepted", "Rejected")


def test_discovery_module_has_no_outreach_or_linkedin_fetch_client():
    import inspect
    import src.contact_discovery as discovery

    source = inspect.getsource(discovery)
    assert "smtplib" not in source
    assert "send_email" not in source
    assert "httpx" not in source
    assert "requests" not in source


def _connection_with_cursor(cursor):
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_cm
    connection_cm = MagicMock()
    connection_cm.__enter__.return_value = connection
    return connection_cm, connection


def test_accept_route_populates_contact_research_and_audits_without_outreach():
    lead_id = uuid.uuid4()
    suggestion_id = uuid.uuid4()
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        (
            "website",
            "https://example-company.test/",
            "Official company website",
            "https://example-company.test/",
            '"Example Company" official website',
            "High",
            "Exact company match on official website.",
            "Needs Review",
        ),
        None,
    ]
    connection_cm, connection = _connection_with_cursor(cursor)

    with (
        patch("src.main._read_actor", return_value="Ismael"),
        patch("src.main.get_conn", return_value=connection_cm),
    ):
        response = client.post(
            f"/leads/{lead_id}/contact-suggestions/{suggestion_id}/accept",
            follow_redirects=False,
        )

    assert response.status_code == 303
    sql = "\n".join(str(call.args[0]) for call in cursor.execute.call_args_list)
    assert "INSERT INTO company_contacts" in sql
    assert "contact_suggestion_accepted" not in sql  # passed as a bound value
    assert "INSERT INTO audit_log" in sql
    connection.commit.assert_called_once()


def test_reject_route_keeps_history_without_contact_write():
    lead_id = uuid.uuid4()
    suggestion_id = uuid.uuid4()
    cursor = MagicMock()
    cursor.fetchone.return_value = (
        "website",
        "https://www.google.com/search?q=Example",
        "Official website search",
        "https://www.google.com/search?q=Example",
        '"Example" official website',
        "Low",
        "Search route only.",
        "Needs Review",
    )
    connection_cm, connection = _connection_with_cursor(cursor)

    with (
        patch("src.main._read_actor", return_value="Ismael"),
        patch("src.main.get_conn", return_value=connection_cm),
    ):
        response = client.post(
            f"/leads/{lead_id}/contact-suggestions/{suggestion_id}/reject",
            follow_redirects=False,
        )

    assert response.status_code == 303
    sql = "\n".join(str(call.args[0]) for call in cursor.execute.call_args_list)
    assert "INSERT INTO company_contacts" not in sql
    assert "UPDATE contact_discovery_suggestions" in sql
    assert "INSERT INTO audit_log" in sql
    connection.commit.assert_called_once()
