"""
Tests for src/ingestion/lei_backfill.py.

Uses unittest.mock (same library as tests/test_ingestion.py) to isolate
DB interactions. No real DB connections are made.
"""
import uuid
from unittest.mock import MagicMock, patch

from src.ingestion.lei_backfill import backfill_lei_company_links


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(batches: list[list], matched_company_id: str | None = None):
    """
    Build a mock connection whose cursor().fetchall() returns successive batches.
    _find_company_id is patched separately per test.
    """
    conn = MagicMock()
    cursor_ctx = MagicMock()
    cur = MagicMock()
    cursor_ctx.__enter__ = MagicMock(return_value=cur)
    cursor_ctx.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor_ctx

    # Queue up fetchall() return values for the SELECT batches
    cur.fetchall.side_effect = batches
    return conn, cur


# ---------------------------------------------------------------------------
# Test 1 — exact source_ref (registered_as) match
# ---------------------------------------------------------------------------

def test_exact_source_ref_match():
    lei_id = uuid.uuid4()
    company_id = str(uuid.uuid4())

    conn = MagicMock()
    cur = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cur)
    ctx.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = ctx

    # First SELECT returns one row; second SELECT returns [] (done)
    cur.fetchall.side_effect = [
        [(lei_id, "12345678", "Test Holdings Ltd")],
        [],
    ]

    with patch("src.ingestion.lei_backfill._find_company_id", return_value=company_id) as mock_find:
        result = backfill_lei_company_links(conn)

    assert result == {"scanned": 1, "matched": 1, "unmatched": 0}
    mock_find.assert_called_once_with(conn, "12345678", "Test Holdings Ltd")
    conn.commit.assert_called()


# ---------------------------------------------------------------------------
# Test 2 — normalised_name fallback match (registered_as is None)
# ---------------------------------------------------------------------------

def test_normalised_name_fallback_match():
    lei_id = uuid.uuid4()
    company_id = str(uuid.uuid4())

    conn = MagicMock()
    cur = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cur)
    ctx.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = ctx

    cur.fetchall.side_effect = [
        [(lei_id, None, "Global Capital Holdings Ltd")],
        [],
    ]

    with patch("src.ingestion.lei_backfill._find_company_id", return_value=company_id) as mock_find:
        result = backfill_lei_company_links(conn)

    assert result == {"scanned": 1, "matched": 1, "unmatched": 0}
    mock_find.assert_called_once_with(conn, None, "Global Capital Holdings Ltd")


# ---------------------------------------------------------------------------
# Test 3 — no match, company_id stays NULL
# ---------------------------------------------------------------------------

def test_no_match_stays_null():
    lei_id = uuid.uuid4()

    conn = MagicMock()
    cur = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cur)
    ctx.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = ctx

    cur.fetchall.side_effect = [
        [(lei_id, "UNKNOWN99", "Obscure Entity Ltd")],
        [],
    ]

    with patch("src.ingestion.lei_backfill._find_company_id", return_value=None):
        result = backfill_lei_company_links(conn)

    assert result == {"scanned": 1, "matched": 0, "unmatched": 1}
    # No UPDATE should have been executed
    execute_calls = [str(c) for c in cur.execute.call_args_list]
    assert not any("UPDATE" in c for c in execute_calls)


# ---------------------------------------------------------------------------
# Test 4 — idempotency: second run sees no NULL rows, reports matched=0
# ---------------------------------------------------------------------------

def test_idempotency_second_run_zero():
    conn = MagicMock()
    cur = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cur)
    ctx.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = ctx

    # No NULL rows remaining
    cur.fetchall.side_effect = [[]]

    with patch("src.ingestion.lei_backfill._find_company_id") as mock_find:
        result = backfill_lei_company_links(conn)

    assert result == {"scanned": 0, "matched": 0, "unmatched": 0}
    mock_find.assert_not_called()
    conn.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5 — chunking: more than one batch processes all rows
# ---------------------------------------------------------------------------

def test_chunking_processes_all_rows():
    ids = [uuid.uuid4() for _ in range(3)]
    company_ids = [str(uuid.uuid4()) for _ in range(3)]

    conn = MagicMock()
    cur = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cur)
    ctx.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = ctx

    # Two batches: first has 2 rows (chunk_size=2), second has 1 row, third is empty
    batch1 = [(ids[0], "REF001", "Alpha Ltd"), (ids[1], "REF002", "Beta Ltd")]
    batch2 = [(ids[2], "REF003", "Gamma Ltd")]
    cur.fetchall.side_effect = [batch1, batch2, []]

    # All match
    find_side_effects = [company_ids[0], company_ids[1], company_ids[2]]
    with patch("src.ingestion.lei_backfill._find_company_id", side_effect=find_side_effects):
        with patch("src.ingestion.lei_backfill.LEI_BACKFILL_CHUNK_SIZE", 2):
            result = backfill_lei_company_links(conn)

    assert result == {"scanned": 3, "matched": 3, "unmatched": 0}
    # commit called once per batch (2 batches with rows)
    assert conn.commit.call_count == 2
