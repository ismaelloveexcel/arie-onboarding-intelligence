from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.shadow_scoring import MODEL_VERSION, RULES_VERSION, SCORE_VERSION, WEIGHTS_VERSION, recompute_lead


def _snapshot() -> dict:
    ts = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    return {
        "snapshot_timestamp": ts,
        "company": {
            "id": "11111111-1111-1111-1111-111111111111",
            "company_name": "Audit Trail Test Ltd",
            "jurisdiction": "UK",
            "entity_type": "holding company",
            "sic_codes": ["64200"],
            "incorporation_date": date(2026, 5, 15),
            "updated_at": ts,
        },
        "lei": {"days_since_registration": 10},
        "pscs": [{"country_of_residence": "AE", "ceased_on": None}],
        "officers": [{"nationality": "French", "resigned_on": None}],
    }


def _mock_conn(fetchone_side_effect=None):
    cur = MagicMock()
    if fetchone_side_effect is None:
        cur.fetchone.return_value = ("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",)
    else:
        cur.fetchone.side_effect = fetchone_side_effect
    cursor_cm = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cursor_cm
    return conn


def test_score_runs_success_record_contains_versions_and_evidence_hash(monkeypatch):
    conn = _mock_conn()
    captured = []

    monkeypatch.setattr("src.shadow_scoring.load_lead_snapshot", lambda *_a, **_k: _snapshot())

    def _capture_record(_conn, **kwargs):
        captured.append(kwargs)
        return "run-success"

    monkeypatch.setattr("src.shadow_scoring._record_score_run", _capture_record)
    result = recompute_lead(
        conn,
        "11111111-1111-1111-1111-111111111111",
        trigger_type="manual",
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
        snapshot_timestamp=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "success"
    assert captured and captured[-1]["status"] == "success"
    assert captured[-1]["score_version"] == result["score_version"]
    assert captured[-1]["weights_version"] == result["weights_version"]
    assert captured[-1]["rules_version"] == result["rules_version"]
    assert captured[-1]["model_version"] == result["model_version"]
    assert captured[-1]["evidence_hash"] == result["evidence_hash"]


def test_score_runs_idempotent_replay_records_skipped(monkeypatch):
    conn = _mock_conn(fetchone_side_effect=[("existing-success",)])
    captured = []

    def _capture_record(_conn, **kwargs):
        captured.append(kwargs)
        return "run-skipped"

    monkeypatch.setattr("src.shadow_scoring._record_score_run", _capture_record)

    result = recompute_lead(
        conn,
        "11111111-1111-1111-1111-111111111111",
        trigger_type="webhook",
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
        idempotency_key="evt-123",
        snapshot_timestamp=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "skipped"
    assert captured and captured[-1]["status"] == "skipped"
    assert captured[-1]["error_code"] == "idempotent_replay"
    assert captured[-1]["idempotency_key"] == "evt-123"


def test_score_runs_failure_record_contains_error_metadata(monkeypatch):
    conn = _mock_conn()
    captured = []

    monkeypatch.setattr("src.shadow_scoring.load_lead_snapshot", lambda *_a, **_k: _snapshot())
    monkeypatch.setattr(
        "src.shadow_scoring.compute_shadow_score",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    def _capture_record(_conn, **kwargs):
        captured.append(kwargs)
        return "run-failed"

    monkeypatch.setattr("src.shadow_scoring._record_score_run", _capture_record)

    with pytest.raises(RuntimeError, match="boom"):
        recompute_lead(
            conn,
            "11111111-1111-1111-1111-111111111111",
            trigger_type="manual",
            scoring_version=SCORE_VERSION,
            weights_version=WEIGHTS_VERSION,
            rules_version=RULES_VERSION,
            model_version=MODEL_VERSION,
            snapshot_timestamp=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

    assert captured and captured[-1]["status"] == "failure"
    assert captured[-1]["error_code"] == "RuntimeError"
    assert captured[-1]["error_message"] == "boom"
