"""
Dashboard route tests.
All DB calls are mocked — no real connection required.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn_mock(fetchone_returns: list, fetchall_returns: list):
    """
    Build a mock get_conn() context manager.

    The route makes exactly 9 fetchone() calls and 2 fetchall() calls (in order):
      fetchone[0] = last pipeline run row  (or None)
      fetchone[1] = last success run row   (or None)
      fetchone[2] = (last_enriched_at,)
      fetchone[3] = (last_lei_seen,)
      fetchone[4] = vol_row  (total, last_7d, last_30d)
      fetchone[5] = score_row (s0_39, s40_59, s60_79, s80_100, total_scored)
      fetchone[6] = cov_row  (total_uk, enriched_uk, with_officers, with_pscs,
                               total_lei, linked_lei, total_mu)
      fetchone[7] = (high_fit_contact_missing,)
      fetchone[8] = introducer metrics
      fetchall[0] = status_counts  [(status, cnt), ...]
      fetchall[1] = recent introducers [(name, category, status, assigned_to, updated_at), ...]
    """
    mock_cur = MagicMock()
    mock_cur.fetchone.side_effect = fetchone_returns
    mock_cur.fetchall.side_effect = fetchall_returns

    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__enter__ = MagicMock(return_value=mock_cur)
    mock_cursor_cm.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor_cm

    mock_conn_cm = MagicMock()
    mock_conn_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn_cm.__exit__ = MagicMock(return_value=False)

    return mock_conn_cm


_EMPTY_FETCHONE = [
    None,               # last_run_row
    None,               # last_success_row
    (None,),            # last_enriched_at
    (None,),            # last_lei_seen
    (0, 0, 0),          # vol_row
    (0, 0, 0, 0, 0),    # score_row
    (0, 0, 0, 0, 0, 0, 0),  # cov_row
    (0,),              # high_fit_contact_missing
    (0, 0, 0, 0),     # introducer metrics
]
_EMPTY_FETCHALL = [[], []]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_dashboard_returns_200():
    with patch("src.main.get_conn", return_value=_make_conn_mock(_EMPTY_FETCHONE, _EMPTY_FETCHALL)):
        resp = client.get("/dashboard")
    assert resp.status_code == 200


def test_dashboard_empty_db_no_exception():
    """Empty DB (all zeros / None) must render without raising."""
    with patch("src.main.get_conn", return_value=_make_conn_mock(_EMPTY_FETCHONE, _EMPTY_FETCHALL)):
        resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Dashboard" in resp.text
    # Key metric labels are rendered
    assert "Total Leads" in resp.text
    assert "Pipeline Freshness" in resp.text
    assert "Enrichment Coverage" in resp.text


def test_dashboard_seeded_data():
    """Seeded values appear correctly in rendered HTML."""
    now = datetime.now(timezone.utc)
    recent = now - timedelta(hours=1)

    fetchone_returns = [
        (recent, now, "success", 120, 30, 150, 300, 45.2),  # last_run_row
        (recent, 120, 30, 150, 300),                         # last_success_row
        (recent,),                                            # last_enriched_at
        (recent,),                                            # last_lei_seen
        (500, 12, 48),                                        # vol_row
        (80, 120, 200, 100, 500),                             # score_row
        (300, 150, 80, 70, 1200, 900, 200),                   # cov_row
        (14,),                                                  # high_fit_contact_missing
        (20, 12, 4, 6),                                        # introducer metrics
    ]
    fetchall_returns = [
        [("New", 150), ("Reviewing", 80), ("Qualified", 60)],
        [
            ("Acme Partners", "Corporate Services", "Qualified", "RM 1", recent),
            ("Global Funds Ltd", "Fund Services", "New", None, recent),
        ],
    ]

    with patch("src.main.get_conn", return_value=_make_conn_mock(fetchone_returns, fetchall_returns)):
        resp = client.get("/dashboard")

    assert resp.status_code == 200
    assert "500" in resp.text   # total_leads
    assert "12" in resp.text    # leads_7d
    assert "New" in resp.text
    assert "Acme Partners" in resp.text
    assert "High-fit leads missing contact path" in resp.text
    assert "14" in resp.text


def test_dashboard_stale_source_flag():
    """Sources with last run > 36 hours ago are flagged with danger colour."""
    old_ts = datetime.now(timezone.utc) - timedelta(hours=40)

    fetchone_returns = [
        (old_ts, old_ts, "success", 50, 10, 60, 100, 30.0),  # last_run_row — stale
        (old_ts, 50, 10, 60, 100),                             # last_success_row
        (old_ts,),                                             # last_enriched_at — stale
        (old_ts,),                                             # last_lei_seen — stale
        (100, 0, 0),                                           # vol_row
        (0, 0, 0, 0, 0),                                       # score_row
        (50, 25, 10, 8, 200, 150, 30),                         # cov_row
        (3,),                                                    # high_fit_contact_missing
        (0, 0, 0, 0),                                           # introducer metrics
    ]
    fetchall_returns = [[], []]

    with patch("src.main.get_conn", return_value=_make_conn_mock(fetchone_returns, fetchall_returns)):
        resp = client.get("/dashboard")

    assert resp.status_code == 200
    # Stale rows are rendered with danger colour
    assert "var(--danger)" in resp.text
