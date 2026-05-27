"""
Tests for src/pipeline.py:_reap_stuck_runs.

Uses unittest.mock to isolate DB interactions (same convention as
tests/test_lei_backfill.py and tests/test_dashboard.py). No real DB
connection required.
"""

import uuid
from unittest.mock import MagicMock

from src.pipeline import _reap_stuck_runs


def _make_conn(returning_rows: list):
    """Build a mock connection whose UPDATE ... RETURNING cursor yields the given rows."""
    cur = MagicMock()
    cur.fetchall.return_value = returning_rows

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cur)
    ctx.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = ctx
    return conn, cur


def test_reap_returns_count_and_commits_when_rows_reaped():
    reaped = [(uuid.uuid4(),), (uuid.uuid4(),)]
    conn, cur = _make_conn(reaped)

    count = _reap_stuck_runs(conn)

    assert count == 2
    conn.commit.assert_called_once()
    # SQL targets only 'running' rows older than 90 minutes
    sql = cur.execute.call_args[0][0]
    assert "status = 'aborted'" in sql
    assert "status = 'running'" in sql
    assert "90 minutes" in sql
    assert "RETURNING id" in sql


def test_reap_returns_zero_when_no_stuck_rows():
    conn, cur = _make_conn([])

    count = _reap_stuck_runs(conn)

    assert count == 0
    conn.commit.assert_called_once()


def test_reap_swallows_exceptions_and_returns_zero():
    """A reaper failure must NOT abort the pipeline."""
    conn = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(side_effect=RuntimeError("db went away"))
    ctx.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = ctx

    count = _reap_stuck_runs(conn)

    assert count == 0
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_reap_writes_explanatory_error_message():
    reaped = [(uuid.uuid4(),)]
    conn, cur = _make_conn(reaped)

    _reap_stuck_runs(conn)

    sql = cur.execute.call_args[0][0]
    assert "reaped" in sql.lower()
    assert "process killed" in sql.lower() or ">90min" in sql.lower()
