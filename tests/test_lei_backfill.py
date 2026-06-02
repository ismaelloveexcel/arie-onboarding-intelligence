"""Tests for src/ingestion/lei_backfill.py."""

import uuid
from unittest.mock import MagicMock, patch

from src.ingestion.lei_backfill import backfill_lei_company_links


def _conn_with_batches(batches: list[list[tuple]]):
    conn = MagicMock()
    cur = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cur)
    ctx.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = ctx
    cur.fetchall.side_effect = batches
    return conn, cur


def test_exact_registered_as_unique_match():
    lei_id = uuid.uuid4()
    company_id = str(uuid.uuid4())
    conn, _ = _conn_with_batches(
        [[(lei_id, "LEI123", "12345678", "Test Holdings Ltd", "UK")], []]
    )

    with patch(
        "src.ingestion.lei_backfill._find_company_match",
        return_value=(company_id, "registered_as_unique", [company_id]),
    ) as mock_match:
        result = backfill_lei_company_links(conn)

    assert result == {"scanned": 1, "matched": 1, "unmatched": 0}
    mock_match.assert_called_once_with(conn, "12345678", "Test Holdings Ltd", "UK")
    conn.commit.assert_called()


def test_no_match_leaves_unlinked():
    lei_id = uuid.uuid4()
    conn, cur = _conn_with_batches(
        [[(lei_id, "LEI999", "UNKNOWN99", "Obscure Entity Ltd", "UK")], []]
    )

    with patch(
        "src.ingestion.lei_backfill._find_company_match",
        return_value=(None, "no_match", []),
    ), patch("src.ingestion.lei_backfill._queue_link_review") as mock_queue:
        result = backfill_lei_company_links(conn)

    assert result == {"scanned": 1, "matched": 0, "unmatched": 1}
    execute_calls = [str(c) for c in cur.execute.call_args_list]
    assert not any("UPDATE lei_records SET company_id" in c for c in execute_calls)
    mock_queue.assert_not_called()


def test_ambiguous_match_is_queued_for_manual_review():
    lei_id = uuid.uuid4()
    conn, _ = _conn_with_batches(
        [[(lei_id, "LEIAMB", "12345678", "Ambiguous Co", "UK")], []]
    )
    candidates = [str(uuid.uuid4()), str(uuid.uuid4())]

    with patch(
        "src.ingestion.lei_backfill._find_company_match",
        return_value=(None, "ambiguous_registered_as", candidates),
    ), patch("src.ingestion.lei_backfill._queue_link_review") as mock_queue:
        result = backfill_lei_company_links(conn)

    assert result == {"scanned": 1, "matched": 0, "unmatched": 1}
    mock_queue.assert_called_once_with(
        conn,
        lei_code="LEIAMB",
        registered_as="12345678",
        legal_name="Ambiguous Co",
        jurisdiction="UK",
        match_reason="ambiguous_registered_as",
        candidate_company_ids=candidates,
    )


def test_chunking_processes_all_rows():
    ids = [uuid.uuid4() for _ in range(3)]
    company_ids = [str(uuid.uuid4()) for _ in range(3)]
    batch1 = [
        (ids[0], "LEI1", "REF001", "Alpha Ltd", "UK"),
        (ids[1], "LEI2", "REF002", "Beta Ltd", "UK"),
    ]
    batch2 = [(ids[2], "LEI3", "REF003", "Gamma Ltd", "UK")]
    conn, _ = _conn_with_batches([batch1, batch2, []])

    with patch(
        "src.ingestion.lei_backfill._find_company_match",
        side_effect=[
            (company_ids[0], "registered_as_unique", [company_ids[0]]),
            (company_ids[1], "registered_as_unique", [company_ids[1]]),
            (company_ids[2], "registered_as_unique", [company_ids[2]]),
        ],
    ):
        with patch("src.ingestion.lei_backfill.LEI_BACKFILL_CHUNK_SIZE", 2):
            result = backfill_lei_company_links(conn)

    assert result == {"scanned": 3, "matched": 3, "unmatched": 0}
    assert conn.commit.call_count == 2
