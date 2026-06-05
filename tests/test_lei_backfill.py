"""
Tests for src/ingestion/lei_backfill.py.

Uses unittest.mock to isolate DB interactions. No real DB connections are made.
Mocks resolve_company_match (the current matching entry point) rather than the
deprecated _find_company_id.
"""

import uuid
from unittest.mock import MagicMock, patch

from src.ingestion.gleif import MatchResult
from src.ingestion.lei_backfill import backfill_lei_company_links

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verified(company_id: str) -> MatchResult:
    return MatchResult(
        company_id=company_id,
        match_state="VERIFIED",
        confidence_score=1.0,
        match_basis="source_ref",
        candidate_company_ids=[company_id],
        reason="exact registered_as match",
    )


def _unmatched() -> MatchResult:
    return MatchResult(
        company_id=None,
        match_state="UNMATCHED",
        confidence_score=0.0,
        match_basis=None,
        candidate_company_ids=[],
        reason="no match found",
    )


def _ambiguous(ids: list[str]) -> MatchResult:
    return MatchResult(
        company_id=None,
        match_state="AMBIGUOUS",
        confidence_score=0.5,
        match_basis="normalised_name",
        candidate_company_ids=ids,
        reason="multiple candidates",
    )


def _make_conn(batches: list[list]):
    conn = MagicMock()
    cursor_ctx = MagicMock()
    cur = MagicMock()
    cursor_ctx.__enter__ = MagicMock(return_value=cur)
    cursor_ctx.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor_ctx
    cur.fetchall.side_effect = batches
    return conn, cur


# ---------------------------------------------------------------------------
# Test 1 — verified match
# ---------------------------------------------------------------------------


def test_exact_source_ref_match():
    lei_id = uuid.uuid4()
    company_id = str(uuid.uuid4())

    conn, cur = _make_conn([
        [(lei_id, "lei123", "12345678", "Test Holdings Ltd")],
        [],  # second batch — empty, signals end
    ])

    with patch(
        "src.ingestion.lei_backfill.resolve_company_match",
        return_value=_verified(company_id),
    ) as mock_resolve:
        result = backfill_lei_company_links(conn)

    assert result["scanned"] == 1
    assert result["matched"] == 1
    assert result["unmatched"] == 0
    mock_resolve.assert_called_once()
    conn.commit.assert_called()


# ---------------------------------------------------------------------------
# Test 2 — normalised_name fallback (registered_as is None)
# ---------------------------------------------------------------------------


def test_normalised_name_fallback_match():
    lei_id = uuid.uuid4()
    company_id = str(uuid.uuid4())

    conn, cur = _make_conn([
        [(lei_id, "lei456", None, "Global Capital Holdings Ltd")],
        [],
    ])

    with patch(
        "src.ingestion.lei_backfill.resolve_company_match",
        return_value=_verified(company_id),
    ) as mock_resolve:
        result = backfill_lei_company_links(conn)

    assert result["scanned"] == 1
    assert result["matched"] == 1
    assert result["unmatched"] == 0
    mock_resolve.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3 — no match, company_id stays NULL
# ---------------------------------------------------------------------------


def test_no_match_stays_null():
    lei_id = uuid.uuid4()

    conn, cur = _make_conn([
        [(lei_id, "lei789", "UNKNOWN99", "Obscure Entity Ltd")],
        [],
    ])

    with patch(
        "src.ingestion.lei_backfill.resolve_company_match",
        return_value=_unmatched(),
    ):
        result = backfill_lei_company_links(conn)

    assert result["scanned"] == 1
    assert result["matched"] == 0
    assert result["unmatched"] == 1


# ---------------------------------------------------------------------------
# Test 4 — idempotency: second run sees no NULL rows, reports matched=0
# ---------------------------------------------------------------------------


def test_idempotency_second_run_zero():
    conn, cur = _make_conn([[]])  # no NULL rows remaining

    with patch(
        "src.ingestion.lei_backfill.resolve_company_match",
    ) as mock_resolve:
        result = backfill_lei_company_links(conn)

    assert result == {"scanned": 0, "matched": 0, "ambiguous": 0, "unmatched": 0}
    mock_resolve.assert_not_called()
    conn.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5 — chunking: more than one batch processes all rows
# ---------------------------------------------------------------------------


def test_chunking_processes_all_rows():
    ids = [uuid.uuid4() for _ in range(3)]
    lei_codes = ["L001", "L002", "L003"]
    company_ids = [str(uuid.uuid4()) for _ in range(3)]

    conn, cur = _make_conn([
        [
            (ids[0], lei_codes[0], "REF001", "Alpha Ltd"),
            (ids[1], lei_codes[1], "REF002", "Beta Ltd"),
        ],
        [(ids[2], lei_codes[2], "REF003", "Gamma Ltd")],
        [],
    ])

    match_results = [_verified(cid) for cid in company_ids]

    with patch(
        "src.ingestion.lei_backfill.resolve_company_match",
        side_effect=match_results,
    ):
        with patch("src.ingestion.lei_backfill.LEI_BACKFILL_CHUNK_SIZE", 2):
            result = backfill_lei_company_links(conn)

    assert result["scanned"] == 3
    assert result["matched"] == 3
    assert result["unmatched"] == 0
    # commit called once per batch with matched rows (2 batches)
    assert conn.commit.call_count == 2
