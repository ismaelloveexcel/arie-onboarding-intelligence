"""
Ingestion tests.
- Mock HTTP for Companies House (no real API calls)
- Use Railway PostgreSQL (no SQLite per spec)
- Verify idempotency: running twice produces identical row count
- Verify single source failure does not crash the other
"""
import json
import uuid
from unittest.mock import MagicMock, patch

from src.db import get_conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_SOURCE = "companies_house"
_TEST_REF_PREFIX = "TEST_INGESTION_"


def _cleanup(conn):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM companies WHERE source_ref LIKE %s",
            (f"{_TEST_REF_PREFIX}%",),
        )
    conn.commit()


def _count_test_rows(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM companies WHERE source_ref LIKE %s",
            (f"{_TEST_REF_PREFIX}%",),
        )
        return cur.fetchone()[0]


def _fake_ch_response(page: int = 0) -> dict:
    """Single fake Companies House page with 2 items."""
    return {
        "total_results": 2,
        "start_index": page * 100,
        "items_per_page": 100,
        "items": [
            {
                "company_number": f"{_TEST_REF_PREFIX}001",
                "company_name": "Test Holdings Ltd",
                "company_type": "holding company",
                "company_status": "active",
                "date_of_creation": "2026-05-01",
                "registered_office_address": {"address_line_1": "1 Test St", "postal_code": "EC1A 1AA"},
                "sic_codes": ["64200"],
            },
            {
                "company_number": f"{_TEST_REF_PREFIX}002",
                "company_name": "Global Test Fund Partners",
                "company_type": "investment vehicle",
                "company_status": "active",
                "date_of_creation": "2026-05-02",
                "registered_office_address": {},
                "sic_codes": ["66300"],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Test: Companies House upsert is idempotent
# ---------------------------------------------------------------------------

def test_companies_house_idempotent():
    """Running ingestion twice must produce the same row count."""
    fake_resp = MagicMock()
    fake_resp.json.return_value = _fake_ch_response()
    fake_resp.raise_for_status.return_value = None

    with get_conn() as conn:
        _cleanup(conn)
        try:
            with patch("src.ingestion.companies_house.httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.return_value = fake_resp
                mock_client_cls.return_value = mock_client

                from src.ingestion.companies_house import fetch_uk_incorporations

                count_first = fetch_uk_incorporations(conn)
                rows_after_first = _count_test_rows(conn)

                count_second = fetch_uk_incorporations(conn)
                rows_after_second = _count_test_rows(conn)

            assert count_first == 2
            assert count_second == 2
            assert rows_after_first == rows_after_second == 2, (
                f"Idempotency failed: {rows_after_first} vs {rows_after_second}"
            )
        finally:
            _cleanup(conn)


# ---------------------------------------------------------------------------
# Test: Companies House failure does not crash Mauritius ingestion
# ---------------------------------------------------------------------------

def test_ch_failure_does_not_block_mauritius():
    """If Companies House raises, the call returns 0 and does not propagate."""
    with patch("src.ingestion.companies_house.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("network error")
        mock_client_cls.return_value = mock_client

        from src.ingestion.companies_house import fetch_uk_incorporations

        with get_conn() as conn:
            count = fetch_uk_incorporations(conn)

    assert count == 0  # failed gracefully, did not raise


# ---------------------------------------------------------------------------
# Test: Mauritius failure does not raise
# ---------------------------------------------------------------------------

def test_mauritius_uses_current_registry_url():
    from src.ingestion.mauritius import _BASE_URL

    assert _BASE_URL == "https://onlinesearch.mns.global/"


def test_mauritius_failure_does_not_raise():
    """Mauritius scraper failure returns 0, never raises."""
    with patch("src.ingestion.mauritius._scrape_mns", side_effect=Exception("playwright error")):
        from src.ingestion.mauritius import fetch_mauritius_incorporations

        with get_conn() as conn:
            count = fetch_mauritius_incorporations(conn)

    assert count == 0


# ---------------------------------------------------------------------------
# Test: upsert ON CONFLICT updates, not duplicates
# ---------------------------------------------------------------------------

def test_upsert_on_conflict_updates():
    """Upserting the same source_ref twice must not create duplicate rows."""
    from src.db import upsert_company

    # Use a unique ref outside the _TEST_REF_PREFIX cleanup namespace so that
    # concurrent runs of the other tests in this file (whose _cleanup() deletes
    # rows matching source_ref LIKE 'TEST_INGESTION_%') cannot race-delete the
    # row this test just upserted on the shared CI database.
    ref = f"TEST_UPSERT_CONFLICT_{uuid.uuid4().hex}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM companies WHERE source_ref = %s", (ref,))
        conn.commit()
        try:
            base = dict(
                source_system="companies_house",
                source_ref=ref,
                company_name="Conflict Test Ltd",
                normalised_name="conflict test ltd",
                jurisdiction="UK",
                entity_type="ltd",
                incorporation_date=None,
                registered_address=None,
                sic_codes=[],
                website=None,
                verify_url=None,
                raw_data=json.dumps({}),
            )
            upsert_company(conn, base)
            upsert_company(conn, {**base, "company_name": "Conflict Test Ltd Updated"})
            conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*), MAX(company_name) FROM companies WHERE source_ref = %s",
                    (ref,),
                )
                count, name = cur.fetchone()

            assert count == 1
            assert name == "Conflict Test Ltd Updated"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM companies WHERE source_ref = %s", (ref,))
            conn.commit()
