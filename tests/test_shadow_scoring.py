from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.shadow_scoring import (
    MODEL_VERSION,
    RULES_VERSION,
    SCORE_VERSION,
    WEIGHTS_VERSION,
    compute_shadow_score,
    recompute_lead,
)


def _snapshot() -> dict:
    return {
        "snapshot_timestamp": datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        "company": {
            "id": "11111111-1111-1111-1111-111111111111",
            "company_name": "Global Capital Holdings Ltd",
            "jurisdiction": "UK",
            "entity_type": "holding company",
            "sic_codes": ["64200"],
            "incorporation_date": date(2026, 5, 15),
            "updated_at": datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
        },
        "lei": {"days_since_registration": 20},
        "pscs": [{"country_of_residence": "AE", "ceased_on": None}],
        "officers": [{"nationality": "French", "resigned_on": None}],
    }


def _mock_conn():
    cur = MagicMock()
    cur.fetchone.return_value = ("22222222-2222-2222-2222-222222222222",)
    cursor_cm = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cursor_cm
    return conn


def test_compute_shadow_score_populates_version_fields():
    score = compute_shadow_score(
        _snapshot(),
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
    )
    assert score["score_version"] == SCORE_VERSION
    assert score["weights_version"] == WEIGHTS_VERSION
    assert score["rules_version"] == RULES_VERSION
    assert score["model_version"] == MODEL_VERSION
    assert score["evidence_hash"]
    assert isinstance(score["priority_score"], int)
    assert 0 <= score["priority_score"] <= 100


def test_compute_shadow_score_is_reproducible_from_same_snapshot():
    first = compute_shadow_score(
        _snapshot(),
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
    )
    second = compute_shadow_score(
        _snapshot(),
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
    )
    assert first["evidence_hash"] == second["evidence_hash"]
    assert first["priority_score"] == second["priority_score"]
    assert first["why_output"] == second["why_output"]
    assert first["evidence"] == second["evidence"]


def test_evidence_drives_why_output_consistently():
    score = compute_shadow_score(
        _snapshot(),
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
    )
    positive_labels = {
        item["label"] for item in score["evidence"] if int(item["impact"]) > 0
    }
    if positive_labels:
        assert score["why_output"] != "Not yet scored."
        assert any(label in score["why_output"] for label in positive_labels)
    else:
        assert score["why_output"] == "Not yet scored."


def test_recompute_parity_manual_nightly_backfill(monkeypatch):
    snapshot = _snapshot()
    conn = _mock_conn()

    monkeypatch.setattr("src.shadow_scoring.load_lead_snapshot", lambda *_args, **_kw: snapshot)
    monkeypatch.setattr(
        "src.shadow_scoring._record_score_run",
        lambda *_args, **_kw: "33333333-3333-3333-3333-333333333333",
    )

    fixed_timestamp = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    manual = recompute_lead(
        conn,
        "11111111-1111-1111-1111-111111111111",
        trigger_type="manual",
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
        snapshot_timestamp=fixed_timestamp,
    )
    nightly = recompute_lead(
        conn,
        "11111111-1111-1111-1111-111111111111",
        trigger_type="nightly",
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
        snapshot_timestamp=fixed_timestamp,
    )
    backfill = recompute_lead(
        conn,
        "11111111-1111-1111-1111-111111111111",
        trigger_type="backfill",
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
        snapshot_timestamp=fixed_timestamp,
    )

    assert manual["status"] == nightly["status"] == backfill["status"] == "success"
    assert manual["priority_score"] == nightly["priority_score"] == backfill["priority_score"]
    assert manual["evidence_hash"] == nightly["evidence_hash"] == backfill["evidence_hash"]
    assert manual["score_version"] == nightly["score_version"] == backfill["score_version"]
    assert manual["weights_version"] == nightly["weights_version"] == backfill["weights_version"]
    assert manual["rules_version"] == nightly["rules_version"] == backfill["rules_version"]
    assert manual["model_version"] == nightly["model_version"] == backfill["model_version"]
    assert conn.commit.call_count == 3


def test_recompute_rejects_unknown_trigger_type():
    conn = _mock_conn()
    with pytest.raises(ValueError):
        recompute_lead(
            conn,
            "11111111-1111-1111-1111-111111111111",
            trigger_type="cron",
            scoring_version=SCORE_VERSION,
            weights_version=WEIGHTS_VERSION,
            rules_version=RULES_VERSION,
            model_version=MODEL_VERSION,
        )



def test_recompute_idempotency_returns_skipped(monkeypatch):
    conn = _mock_conn()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = [("existing-run",), ("ignored-score-id",)]

    monkeypatch.setattr(
        "src.shadow_scoring._record_score_run",
        lambda *_args, **_kw: "44444444-4444-4444-4444-444444444444",
    )
    result = recompute_lead(
        conn,
        "11111111-1111-1111-1111-111111111111",
        trigger_type="webhook",
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
        idempotency_key="evt-123",
    )
    assert result["status"] == "skipped"
    assert result["run_id"] == "44444444-4444-4444-4444-444444444444"
