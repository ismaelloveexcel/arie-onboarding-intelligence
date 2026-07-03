"""DB-free tests for the contact-route export/import workflow."""

from scripts.export_research_batch import EXPORT_COLUMNS, build_research_rows
from scripts.import_contact_routes import evaluate_batch
from src.contact_routes import classify_email, is_valid_url, validate_route_row


def _row(**overrides):
    base = {
        "company_id": "00000000-0000-0000-0000-000000000001",
        "company_number": "C123",
        "company_name": "Example Holdings Limited",
        "jurisdiction": "Mauritius",
        "website_url": "https://example.mu",
        "contact_page_url": "",
        "generic_business_email": "info@example.mu",
        "contact_form_url": "",
        "linkedin_company_url": "",
        "introducer_or_csp_name": "",
        "introducer_or_csp_route": "",
        "source_url": "https://example.mu/contact",
        "source_label": "Company website",
        "source_type": "company_website",
        "confidence": "high",
        "evidence_summary": "Generic email published on official site",
        "route_entry_method": "manual",
        "notes": "",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------- validation

def test_valid_generic_email_row_accepted():
    result = validate_route_row(_row())
    assert result["ok"] is True
    assert "generic_email" in result["compliant_paths"]
    assert result["normalized"]["generic_email"] == "info@example.mu"


def test_personal_email_rejected_as_route():
    result = validate_route_row(_row(generic_business_email="john.smith@example.mu"))
    assert result["ok"] is False
    assert any("personal" in e.lower() for e in result["errors"])


def test_public_provider_email_rejected():
    result = validate_route_row(
        _row(generic_business_email="someone@gmail.com", website_url="", source_type="other")
    )
    assert result["ok"] is False


def test_missing_source_url_rejected():
    result = validate_route_row(_row(source_url=""))
    assert result["ok"] is False
    assert "Missing source_url" in result["errors"]


def test_missing_evidence_rejected():
    result = validate_route_row(_row(evidence_summary=""))
    assert result["ok"] is False
    assert "Missing evidence_summary" in result["errors"]


def test_bad_confidence_rejected():
    result = validate_route_row(_row(confidence="very-high"))
    assert result["ok"] is False
    assert any("confidence" in e.lower() for e in result["errors"])


def test_malformed_url_rejected():
    result = validate_route_row(_row(website_url="notaurl", generic_business_email=""))
    assert result["ok"] is False
    assert any("website_url" in e for e in result["errors"])


def test_no_compliant_path_rejected():
    result = validate_route_row(
        _row(website_url="", generic_business_email="", contact_form_url="", introducer_or_csp_name="")
    )
    assert result["ok"] is False
    assert "No compliant contact path" in result["errors"]


def test_missing_company_identity_rejected():
    result = validate_route_row(_row(company_id="", company_number=""))
    assert result["ok"] is False
    assert any("company_id" in e for e in result["errors"])


def test_contact_form_only_is_compliant():
    result = validate_route_row(
        _row(generic_business_email="", website_url="", contact_form_url="https://example.mu/contact-form")
    )
    assert result["ok"] is True
    assert result["compliant_paths"] == ["contact_form"]


def test_introducer_only_is_compliant():
    result = validate_route_row(
        _row(generic_business_email="", website_url="", introducer_or_csp_name="Example CSP Ltd")
    )
    assert result["ok"] is True
    assert "introducer" in result["compliant_paths"]


def test_offsite_generic_email_warns():
    result = validate_route_row(
        _row(generic_business_email="info@other-domain.com")
    )
    assert result["ok"] is True
    assert result["warnings"]


def test_classify_and_url_helpers():
    assert classify_email("info@acme.mu") == "generic"
    assert classify_email("jane.doe@acme.mu") == "personal"
    assert classify_email("x@gmail.com") == "public_provider"
    assert classify_email("bad") == "invalid"
    assert is_valid_url("https://acme.mu")
    assert not is_valid_url("ftp://acme.mu")


# --------------------------------------------------------------- evaluate_batch

def _resolver(**company):
    base = {
        "company_id": "00000000-0000-0000-0000-000000000001",
        "company_name": "Example Holdings Limited",
        "jurisdiction": "Mauritius",
        "entity_type": "GLOBAL BUSINESS COMPANY",
        "registered_address": "Ebene",
        "verify_url": "https://onlinesearch.mns.global/",
        "website": None,
        "priority_score": 85,
        "tier": "HIGH",
        "has_existing_route": False,
    }
    base.update(company)
    return lambda norm: dict(base)


def test_evaluate_batch_generic_email_moves_to_ready():
    result = evaluate_batch([_row()], _resolver(), update_existing=False)
    assert result["summary"]["accepted"] == 1
    assert result["summary"]["moved_to_ready_to_contact"] == 1
    assert result["top_now_rm_ready"]


def test_evaluate_batch_introducer_moves_to_introducer_route():
    row = _row(generic_business_email="", website_url="", introducer_or_csp_name="Example CSP Ltd")
    result = evaluate_batch([row], _resolver(), update_existing=False)
    assert result["summary"]["accepted"] == 1
    assert result["summary"]["moved_to_route_via_introducer"] == 1


def test_evaluate_batch_company_not_found_rejected():
    result = evaluate_batch([_row()], lambda norm: None, update_existing=False)
    assert result["summary"]["accepted"] == 0
    assert result["summary"]["rejected"] == 1


def test_evaluate_batch_existing_route_is_duplicate_without_update():
    result = evaluate_batch(
        [_row()], _resolver(has_existing_route=True), update_existing=False
    )
    assert result["summary"]["duplicates"] == 1
    assert result["summary"]["accepted"] == 0


def test_evaluate_batch_existing_route_updates_with_flag():
    result = evaluate_batch(
        [_row()], _resolver(has_existing_route=True), update_existing=True
    )
    assert result["summary"]["accepted"] == 1


def test_evaluate_batch_within_file_duplicate():
    result = evaluate_batch([_row(), _row()], _resolver(), update_existing=False)
    assert result["summary"]["duplicates"] == 1
    assert result["summary"]["accepted"] == 1


def test_evaluate_batch_rejects_personal_email_row():
    row = _row(generic_business_email="ceo.person@example.mu")
    result = evaluate_batch([row], _resolver(), update_existing=False)
    assert result["summary"]["rejected"] == 1
    assert result["summary"]["accepted"] == 0


# --------------------------------------------------------------- export

def _lead(**overrides):
    base = {
        "company_id": "00000000-0000-0000-0000-0000000000aa",
        "company_number": "C999",
        "company_name": "Registry Only Ltd",
        "jurisdiction": "UK",
        "entity_type": "LTD",
        "incorporation_date": "2026-01-01",
        "registered_address": "London",
        "verify_url": "https://find-and-update.company-information.service.gov.uk/",
        "website": None,
        "generic_email": None,
        "contact_form_url": None,
        "linkedin_url": None,
        "contact_confidence": None,
        "priority_score": 70,
        "tier": "HIGH",
        "reason_codes": ["FRESH_LEI"],
        "reason_summary": "Fresh LEI",
        "officers_summary": "Jane Doe",
        "psc_summary": "Holdco SA",
    }
    base.update(overrides)
    return base


def test_export_includes_research_needed_lead_with_all_columns():
    rows = build_research_rows([_lead()], [], {}, limit=50)
    assert len(rows) == 1
    assert set(rows[0].keys()) == set(EXPORT_COLUMNS)
    assert rows[0]["why_now"] == "Fresh LEI registration"
    assert rows[0]["research_instruction"]


def test_export_excludes_already_actionable_lead():
    ready = _lead(generic_email="info@ready.co.uk", contact_confidence="high")
    rows = build_research_rows([ready], [], {}, limit=50)
    assert rows == []


def test_export_respects_limit():
    leads = [_lead(company_id=f"id-{i}", company_name=f"Co {i}") for i in range(5)]
    rows = build_research_rows(leads, [], {}, limit=3)
    assert len(rows) == 3
