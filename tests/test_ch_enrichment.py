"""
Tests for Companies House PSC + Officers enrichment.

Follows the MagicMock pattern from test_lei_backfill.py — no real DB or
HTTP calls are made; all interactions are mocked at the boundary.
"""
import uuid
from unittest.mock import MagicMock, call, patch

from src.ingestion.companies_house import enrich_company


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn():
    """Return a mock psycopg3 connection with a working cursor context manager."""
    conn = MagicMock()
    cur = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cur)
    ctx.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = ctx
    return conn, cur


def _http_client_mock(responses):
    """
    Build a mock for httpx.Client that returns successive (status_code, json) pairs.

    Each entry in *responses* is either:
      - (int, dict)    → resp.status_code = int, resp.json() = dict
      - int            → resp.status_code = int, raise_for_status raises

    Yields one mock response per .get() call in order.
    """
    client_instance = MagicMock()
    mock_responses = []
    for entry in responses:
        resp = MagicMock()
        if isinstance(entry, tuple):
            status, body = entry
            resp.status_code = status
            resp.json.return_value = body
            resp.raise_for_status = MagicMock()
        else:
            resp.status_code = entry
            resp.raise_for_status.side_effect = Exception(f"HTTP {entry}")
        mock_responses.append(resp)
    client_instance.get.side_effect = mock_responses
    # Support `with httpx.Client(...) as client:`
    client_cm = MagicMock()
    client_cm.__enter__ = MagicMock(return_value=client_instance)
    client_cm.__exit__ = MagicMock(return_value=False)
    return client_cm, client_instance


# ---------------------------------------------------------------------------
# Test 1 — happy path: officers + PSCs populated, status=ok, DB rows inserted
# ---------------------------------------------------------------------------

def test_happy_path_officers_and_pscs():
    company_id = uuid.uuid4()
    company_number = "12345678"

    officers_payload = {
        "items": [
            {
                "name": "SMITH, John",
                "officer_role": "director",
                "appointed_on": "2020-01-15",
                "nationality": "British",
                "country_of_residence": "England",
            }
        ]
    }
    pscs_payload = {
        "items": [
            {
                "name": "Smith Holdings Ltd",
                "kind": "corporate-entity-person-with-significant-control",
                "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
                "notified_on": "2020-01-15",
            }
        ]
    }

    client_cm, client_instance = _http_client_mock([
        (200, officers_payload),
        (200, pscs_payload),
    ])
    conn, cur = _make_conn()

    with patch("src.ingestion.companies_house.httpx.Client", return_value=client_cm):
        result = enrich_company(conn, company_id, company_number)

    assert result["status"] == "ok"
    assert result["officers"] == 1
    assert result["pscs"] == 1
    # Both DB cursor.execute calls made (officer upsert + psc upsert + last_enriched_at)
    assert cur.execute.call_count >= 3
    conn.commit.assert_called()


# ---------------------------------------------------------------------------
# Test 2 — 404 → status=not_found, last_enriched_at set
# ---------------------------------------------------------------------------

def test_404_not_found():
    company_id = uuid.uuid4()
    company_number = "99999999"

    client_cm, _ = _http_client_mock([(404, None)])
    conn, cur = _make_conn()

    with patch("src.ingestion.companies_house.httpx.Client", return_value=client_cm):
        result = enrich_company(conn, company_id, company_number)

    assert result["status"] == "not_found"
    assert result["officers"] == 0
    assert result["pscs"] == 0
    # last_enriched_at must be set
    last_enriched_calls = [
        c for c in cur.execute.call_args_list
        if "last_enriched_at" in str(c)
    ]
    assert len(last_enriched_calls) >= 1
    conn.commit.assert_called()


# ---------------------------------------------------------------------------
# Test 3 — 429 → retry → eventual success (200)
# ---------------------------------------------------------------------------

def test_429_retry_eventual_success():
    """One rate-limit response followed by a successful 200."""
    company_id = uuid.uuid4()
    company_number = "11111111"

    officers_payload = {"items": []}
    pscs_payload = {"items": []}

    # Build raw response mocks: first get() → 429, second get() → 200 officers,
    # third get() → 200 pscs
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.raise_for_status = MagicMock()

    resp_200_officers = MagicMock()
    resp_200_officers.status_code = 200
    resp_200_officers.json.return_value = officers_payload
    resp_200_officers.raise_for_status = MagicMock()

    resp_200_pscs = MagicMock()
    resp_200_pscs.status_code = 200
    resp_200_pscs.json.return_value = pscs_payload
    resp_200_pscs.raise_for_status = MagicMock()

    client_instance = MagicMock()
    client_instance.get.side_effect = [resp_429, resp_200_officers, resp_200_pscs]
    client_cm = MagicMock()
    client_cm.__enter__ = MagicMock(return_value=client_instance)
    client_cm.__exit__ = MagicMock(return_value=False)

    conn, cur = _make_conn()

    with patch("src.ingestion.companies_house.httpx.Client", return_value=client_cm), \
         patch("src.ingestion.companies_house.time.sleep"):  # skip actual sleep
        result = enrich_company(conn, company_id, company_number)

    assert result["status"] == "ok"
    assert client_instance.get.call_count == 3  # 429, then 200 ×2


# ---------------------------------------------------------------------------
# Test 4 — 429 × (3 retries + 1 final) → status=failed, last_enriched_at set
# ---------------------------------------------------------------------------

def test_429_exhausted_fails():
    """Four 429 responses exhaust retries — enrich_company returns status=failed."""
    company_id = uuid.uuid4()
    company_number = "22222222"

    def _429_resp():
        r = MagicMock()
        r.status_code = 429
        r.raise_for_status = MagicMock()
        return r

    client_instance = MagicMock()
    client_instance.get.side_effect = [_429_resp() for _ in range(4)]
    client_cm = MagicMock()
    client_cm.__enter__ = MagicMock(return_value=client_instance)
    client_cm.__exit__ = MagicMock(return_value=False)

    conn, cur = _make_conn()

    with patch("src.ingestion.companies_house.httpx.Client", return_value=client_cm), \
         patch("src.ingestion.companies_house.time.sleep"):
        result = enrich_company(conn, company_id, company_number)

    assert result["status"] == "failed"
    assert result["officers"] == 0
    assert result["pscs"] == 0
    last_enriched_calls = [
        c for c in cur.execute.call_args_list
        if "last_enriched_at" in str(c)
    ]
    assert len(last_enriched_calls) >= 1
    conn.commit.assert_called()


# ---------------------------------------------------------------------------
# Test 5 — malformed payload → status=failed
# ---------------------------------------------------------------------------

def test_malformed_payload_fails():
    """Officers endpoint returns 200 but payload is not a dict — graceful failure."""
    company_id = uuid.uuid4()
    company_number = "33333333"

    resp_bad = MagicMock()
    resp_bad.status_code = 200
    resp_bad.json.return_value = "not-a-dict"  # malformed: should be dict with 'items'
    resp_bad.raise_for_status = MagicMock()

    client_instance = MagicMock()
    client_instance.get.return_value = resp_bad
    client_cm = MagicMock()
    client_cm.__enter__ = MagicMock(return_value=client_instance)
    client_cm.__exit__ = MagicMock(return_value=False)

    conn, cur = _make_conn()

    with patch("src.ingestion.companies_house.httpx.Client", return_value=client_cm):
        # Malformed payload causes AttributeError on .get("items") — caught as failed
        result = enrich_company(conn, company_id, company_number)

    assert result["status"] == "failed"
    conn.commit.assert_called()


# ---------------------------------------------------------------------------
# Test 6 — gating: already-enriched companies not re-fetched
# ---------------------------------------------------------------------------

def test_gating_already_enriched_not_refetched():
    """run_ch_enrichment_batch skips companies with last_enriched_at already set."""
    from src.ingestion.companies_house import run_ch_enrichment_batch

    conn, cur = _make_conn()
    # Simulate: no rows returned by the SELECT (all companies already enriched)
    cur.fetchall.return_value = []

    with patch("src.ingestion.companies_house.httpx.Client") as mock_client_cls:
        result = run_ch_enrichment_batch(conn, limit=10)

    # No HTTP calls should have been made
    mock_client_cls.assert_not_called()
    assert result["enriched"] == 0
    assert result["officers"] == 0
    assert result["pscs"] == 0
    assert result["failed"] == 0
