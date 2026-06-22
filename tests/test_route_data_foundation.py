"""DB-free tests for the Route Intelligence data-foundation operators."""

import inspect
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scripts import import_mauritius_registered_offices, normalize_introducers
from scripts.import_mauritius_registered_offices import read_csv
from src.route_intelligence import categorise_introducer, normalise_introducer


def test_registered_office_csv_requires_registry_key_column(tmp_path: Path):
    path = tmp_path / "offices.csv"
    path.write_text(
        "company_name,registered_office_address,source,checked_at,confidence\n"
        "Example Ltd,1 Example St,Operator source,2026-06-21T10:00:00+04:00,High\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="registry_file_number or company_number"):
        read_csv(path)


def test_registered_office_csv_parses_provenance_and_explicit_route(tmp_path: Path):
    path = tmp_path / "offices.csv"
    path.write_text(
        "registry_file_number,company_name,registered_office_address,source,checked_at,confidence,management_company\n"
        "C123,Example Ltd,1 Example St,Operator source,2026-06-21T10:00:00+04:00,Medium,Example Management Ltd\n",
        encoding="utf-8",
    )
    rows, errors = read_csv(path)
    assert errors == []
    assert rows[0]["identifier"] == "C123"
    assert rows[0]["confidence"] == "medium"
    assert rows[0]["context"]["management_company"] == "Example Management Ltd"


def test_import_replace_requires_write():
    with pytest.raises(SystemExit):
        import_mauritius_registered_offices._args(
            ["--csv", "offices.csv", "--replace"]
        )


def test_import_does_not_overwrite_existing_address_without_replace():
    row = {
        "row_number": 2,
        "identifier": "C123",
        "company_name": "Example Ltd",
        "normalised_name": "example ltd",
        "registered_address": "New Address",
        "source": "Operator source",
        "checked_at": datetime.now(timezone.utc),
        "confidence": "high",
        "context": {},
    }
    cursor = MagicMock()
    cursor.fetchall.return_value = [("company-id", "Example Ltd", "Existing Address")]
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_cm
    connection_cm = MagicMock()
    connection_cm.__enter__.return_value = connection
    args = SimpleNamespace(csv=Path("unused.csv"), write=True, replace=False)

    with (
        patch.object(
            import_mauritius_registered_offices,
            "read_csv",
            return_value=([row], []),
        ),
        patch.object(
            import_mauritius_registered_offices,
            "get_conn",
            return_value=connection_cm,
        ),
        patch.object(import_mauritius_registered_offices, "_write_company") as writer,
    ):
        report = import_mauritius_registered_offices.run(args)

    assert report["rows_updated"] == 0
    assert report["skipped_existing"] == [
        {"row": 2, "company_name": "Example Ltd"}
    ]
    writer.assert_not_called()


def test_introducer_category_uses_explicit_evidence_only_for_csp_types():
    result = categorise_introducer(
        {
            "company_name": "Example Holdings Management Ltd",
            "category": "",
            "source": "Internal introducer list",
        }
    )
    assert result["introducer_type"] == "unknown"
    explicit = categorise_introducer(
        {
            "company_name": "Example Holdings Ltd",
            "category": "Corporate Service Provider",
            "source": "Internal introducer list",
        }
    )
    assert explicit["introducer_type"] == "csp"
    assert explicit["category_confidence"] == "high"


def test_clear_law_firm_name_is_medium_confidence():
    result = categorise_introducer(
        {
            "company_name": "Example Legal Partners",
            "category": "",
            "source": "Internal introducer list",
        }
    )
    assert result["introducer_type"] == "law_firm"
    assert result["category_confidence"] == "medium"


def test_normalizer_derives_domains_and_does_not_promote_registry_url():
    result = normalise_introducer(
        {
            "company_name": "Example Ltd",
            "contact_email": "Info@Example.mu",
            "address": "4th Floor, Example Tower, Ebene, Mauritius",
            "verify_url": "https://onlinesearch.mns.global/",
            "source": "Internal introducer list",
        }
    )
    assert result["email_domain"] == "example.mu"
    assert result["website"] is None
    assert result["website_domain"] is None
    assert result["normalized_address"] == "4th fl example tower ebene"


def test_normalizer_defaults_to_dry_run():
    args = normalize_introducers._args([])
    assert args.write is False


def test_data_foundation_has_no_fetch_scoring_or_outreach_logic():
    source = "\n".join(
        (
            inspect.getsource(import_mauritius_registered_offices),
            inspect.getsource(normalize_introducers),
        )
    )
    assert "requests" not in source
    assert "httpx" not in source
    assert "linkedin" not in source.casefold()
    assert "smtplib" not in source
    assert "lead_scores" not in source
    assert "outreach" not in source.casefold()
