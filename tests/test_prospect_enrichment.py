"""Phase 1 tests — RM/commercial enrichment schema, validation, Ready-to-Work."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import import_prospect_enrichment as imp
from scripts.export_research_batch import EXPORT_COLUMNS
from src.prospect_enrichment import (
    ENRICHMENT_FIELDS,
    IMPORT_FIELDS,
    PROVENANCE_FIELDS,
    ROUTE_DISCOVERY_FIELDS,
    ready_to_work,
    rm_readiness_bucket,
    validate_enrichment_row,
)

ROOT = Path(__file__).resolve().parents[1]


def _a_row(**over):
    base = {
        "company_id": "00000000-0000-0000-0000-000000000001",
        "company_number": "C001", "company_name": "Zephyr Holdings Ltd",
        "jurisdiction": "Mauritius",
        "prospect_quality_grade": "A",
        "likely_arie_service_need": "Multi-currency collections",
        "business_model_summary": "Mauritius GBC doing cross-border B2B payments",
        "suggested_opening_angle": "Reference their cross-border B2B focus",
        "best_contact_route": "info@zephyr.mu", "route_quality": "high",
        "source_reliability": "official", "research_status": "ready",
        "last_researched_date": "2026-06-23", "next_rm_action": "contact_now",
        "source_url": "https://zephyr.mu/contact",
        "evidence_summary": "Generic mailbox on official site",
        "generic_business_email": "info@zephyr.mu",
    }
    base.update(over)
    return base


# --- allowed values / grades ------------------------------------------------

def test_invalid_grade_rejected():
    r = validate_enrichment_row(_a_row(prospect_quality_grade="X"))
    assert r["ok"] is False
    assert any("prospect_quality_grade" in e for e in r["errors"])


@pytest.mark.parametrize("field,bad", [
    ("research_status", "done"), ("route_quality", "great"),
    ("source_reliability", "trustme"), ("next_rm_action", "call"),
    ("rm_status", "pending"), ("route_entry_method", "scraped"),
    ("management_shortlist_flag", "yes"),
])
def test_unsupported_allowed_values_rejected(field, bad):
    r = validate_enrichment_row(_a_row(**{field: bad}))
    assert r["ok"] is False
    assert any(field in e for e in r["errors"])


# --- conditional requirements ----------------------------------------------

@pytest.mark.parametrize("grade", ["A", "B"])
def test_ab_requires_source_url(grade):
    r = validate_enrichment_row(_a_row(prospect_quality_grade=grade, source_url=""))
    assert r["ok"] is False
    assert any("source_url" in e for e in r["errors"])


@pytest.mark.parametrize("grade", ["A", "B"])
def test_ab_requires_evidence(grade):
    r = validate_enrichment_row(_a_row(prospect_quality_grade=grade, evidence_summary=""))
    assert r["ok"] is False
    assert any("evidence_summary" in e for e in r["errors"])


@pytest.mark.parametrize("grade", ["A", "B"])
def test_ab_requires_best_contact_route(grade):
    r = validate_enrichment_row(_a_row(prospect_quality_grade=grade, best_contact_route=""))
    assert r["ok"] is False
    assert any("best_contact_route" in e for e in r["errors"])


def test_d_requires_disqualification_reason():
    r = validate_enrichment_row({
        "company_id": "x", "prospect_quality_grade": "D", "research_status": "rejected",
        "last_researched_date": "2026-06-23", "next_rm_action": "reject",
    })
    assert r["ok"] is False
    assert any("disqualification_reason" in e for e in r["errors"])


def test_lost_status_requires_lost_reason():
    r = validate_enrichment_row(_a_row(rm_status="lost"))
    assert r["ok"] is False
    assert any("lost_reason" in e for e in r["errors"])


def test_not_suitable_status_requires_lost_reason():
    r = validate_enrichment_row(_a_row(rm_status="not_suitable"))
    assert r["ok"] is False
    assert any("lost_reason" in e for e in r["errors"])


@pytest.mark.parametrize("status", ["ready", "rejected"])
def test_ready_or_rejected_requires_last_researched_date(status):
    r = validate_enrichment_row(_a_row(research_status=status, last_researched_date=""))
    assert r["ok"] is False
    assert any("last_researched_date" in e for e in r["errors"])


def test_a_grade_blank_source_reliability_rejected():
    r = validate_enrichment_row(_a_row(source_reliability=""))
    assert r["ok"] is False
    assert any("source_reliability" in e for e in r["errors"])


def test_non_integer_rm_priority_rank_rejected():
    r = validate_enrichment_row(_a_row(rm_priority_rank="high"))
    assert r["ok"] is False
    assert any("rm_priority_rank" in e for e in r["errors"])


def test_valid_integer_rm_priority_rank_accepted():
    r = validate_enrichment_row(_a_row(rm_priority_rank="3"))
    assert r["ok"] is True


def test_researched_requires_last_researched_date():
    r = validate_enrichment_row(_a_row(research_status="researched", last_researched_date=""))
    assert r["ok"] is False
    assert any("last_researched_date" in e for e in r["errors"])


def test_personal_email_rejected():
    r = validate_enrichment_row(_a_row(generic_business_email="john.smith@zephyr.mu",
                                       best_contact_route="john.smith@zephyr.mu"))
    assert r["ok"] is False
    assert any("personal" in e.lower() for e in r["errors"])


# --- Ready-to-Work formula --------------------------------------------------

def test_ready_to_work_positive():
    row = _a_row()
    assert validate_enrichment_row(row)["ok"] is True
    assert ready_to_work(row) is True
    assert rm_readiness_bucket(row) == "ready_to_work"


def test_weak_source_blocks_ready_to_work():
    # B grade with weak reliability avoids the A-only weak check, isolates the formula.
    row = _a_row(prospect_quality_grade="A", source_reliability="weak")
    assert ready_to_work(row) is False


def test_no_source_url_blocks_ready_to_work():
    row = _a_row(source_url="")
    assert ready_to_work(row) is False


def test_registry_only_route_blocks_ready_to_work():
    row = _a_row(best_contact_route="registry_only")
    assert ready_to_work(row) is False


def test_low_route_quality_blocks_ready_to_work():
    row = _a_row(route_quality="low")
    assert ready_to_work(row) is False


def test_non_a_grade_is_not_ready():
    row = _a_row(prospect_quality_grade="B")
    assert ready_to_work(row) is False
    assert rm_readiness_bucket(row) in {"research_route", "hold", "reject"}


# --- dry-run safety / production write block --------------------------------

def _resolver_factory(existing=False):
    return lambda norm: {"company_id": norm.get("company_id") or "x", "has_existing_enrichment": existing}


def test_evaluate_batch_buckets_and_counts():
    rows = [_a_row(), _a_row(company_id="d", prospect_quality_grade="D",
                            research_status="rejected", next_rm_action="reject",
                            disqualification_reason="dormant", source_url="", evidence_summary="",
                            best_contact_route="", route_quality="unusable",
                            source_reliability="weak", generic_business_email="")]
    res = imp.evaluate_enrichment_batch(rows, _resolver_factory(), update_existing=False)
    assert res["summary"]["ready_to_work"] == 1
    assert res["summary"]["reject"] == 1
    assert res["summary"]["weak_or_unusable_routes"] == 1


def test_production_write_blocked_without_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert imp.write_blocked_reason(allow_production=False) is not None
    assert imp.write_blocked_reason(allow_production=True) is None


def test_dry_run_does_not_write(tmp_path, monkeypatch):
    import csv
    p = tmp_path / "e.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=IMPORT_FIELDS)
        w.writeheader()
        w.writerow({k: _a_row().get(k, "") for k in IMPORT_FIELDS})

    conn = MagicMock()
    conn_cm = MagicMock()
    conn_cm.__enter__.return_value = conn
    conn_cm.__exit__.return_value = False
    with (
        patch.object(imp, "get_conn", return_value=conn_cm),
        patch.object(imp, "_make_resolver", return_value=_resolver_factory()),
    ):
        result = imp.run(str(p), write=False, update_existing=False, allow_production=False)

    assert result["mode"] == "dry-run"
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once()


# --- templates / prompts / docs --------------------------------------------

def test_export_template_includes_all_enrichment_fields():
    for f in ROUTE_DISCOVERY_FIELDS + PROVENANCE_FIELDS + ENRICHMENT_FIELDS:
        assert f in EXPORT_COLUMNS


def test_import_template_header_matches_schema():
    header = (ROOT / "docs" / "prospect_enrichment_import_template.csv").read_text(
        encoding="utf-8"
    ).splitlines()[0].split(",")
    assert header == IMPORT_FIELDS


def test_prompts_include_fields_and_prohibitions():
    manus = (ROOT / "docs" / "manus_contact_research_prompt.md").read_text(encoding="utf-8")
    perplexity = (ROOT / "docs" / "perplexity_commercial_research_prompt.md").read_text(encoding="utf-8")
    assert "route_quality" in manus and "source_reliability" in manus
    assert "scrape" in manus.lower() and "source_url" in manus
    for f in ("business_model_summary", "likely_arie_service_need", "suggested_opening_angle"):
        assert f in perplexity
    assert "scrap" in perplexity.lower()
    assert "source_url" in perplexity


def test_manus_prompt_explicitly_prohibits_all_required():
    manus = (ROOT / "docs" / "manus_contact_research_prompt.md").read_text(encoding="utf-8").lower()
    for phrase in (
        "guessed emails",
        "personal email guessing",
        "scrape linkedin",
        "unsupported claims",
        "unsupported speculation",
        "without a `source_url`",
    ):
        assert phrase in manus, f"Manus prompt missing prohibition: {phrase}"
