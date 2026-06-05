import time
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from src.shadow_scoring import MODEL_VERSION, RULES_VERSION, SCORE_VERSION, WEIGHTS_VERSION, recompute_lead


def _mock_conn():
    cur = MagicMock()
    cur.fetchone.return_value = ("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",)
    cursor_cm = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cursor_cm
    return conn


def _snapshot(ts: datetime) -> dict:
    return {
        "snapshot_timestamp": ts,
        "company": {
            "id": "11111111-1111-1111-1111-111111111111",
            "company_name": "Determinism Test Ltd",
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


def test_recompute_uses_exact_snapshot_timestamp_across_triggers(monkeypatch):
    conn = _mock_conn()
    fixed_ts = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    observed_timestamps = []

    def _loader(_conn, _lead_id, snapshot_timestamp):
        observed_timestamps.append(snapshot_timestamp)
        return _snapshot(snapshot_timestamp)

    monkeypatch.setattr("src.shadow_scoring.load_lead_snapshot", _loader)
    monkeypatch.setattr(
        "src.shadow_scoring._record_score_run",
        lambda *_args, **_kwargs: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )

    results = [
        recompute_lead(
            conn,
            "11111111-1111-1111-1111-111111111111",
            trigger_type=trigger,
            scoring_version=SCORE_VERSION,
            weights_version=WEIGHTS_VERSION,
            rules_version=RULES_VERSION,
            model_version=MODEL_VERSION,
            snapshot_timestamp=fixed_ts,
        )
        for trigger in ("manual", "nightly", "backfill")
    ]

    assert observed_timestamps == [fixed_ts, fixed_ts, fixed_ts]
    assert all(item["snapshot_timestamp"] == fixed_ts.isoformat() for item in results)


def test_same_snapshot_timestamp_yields_same_scoring_fingerprint(monkeypatch):
    conn = _mock_conn()
    fixed_ts = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "src.shadow_scoring.load_lead_snapshot",
        lambda _conn, _lead_id, snapshot_timestamp: _snapshot(snapshot_timestamp),
    )
    monkeypatch.setattr(
        "src.shadow_scoring._record_score_run",
        lambda *_args, **_kwargs: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    )

    first = recompute_lead(
        conn,
        "11111111-1111-1111-1111-111111111111",
        trigger_type="manual",
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
        snapshot_timestamp=fixed_ts,
    )
    second = recompute_lead(
        conn,
        "11111111-1111-1111-1111-111111111111",
        trigger_type="nightly",
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
        snapshot_timestamp=fixed_ts,
    )

    assert first["evidence_hash"] == second["evidence_hash"]
    assert first["priority_score"] == second["priority_score"]
    assert first["score_version"] == second["score_version"]
    assert first["weights_version"] == second["weights_version"]
    assert first["rules_version"] == second["rules_version"]
    assert first["model_version"] == second["model_version"]


def test_same_snapshot_remains_stable_across_wall_clock_and_timezone_changes(monkeypatch):
    conn = _mock_conn()
    fixed_ts = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "src.shadow_scoring.load_lead_snapshot",
        lambda _conn, _lead_id, snapshot_timestamp: _snapshot(snapshot_timestamp),
    )
    monkeypatch.setattr(
        "src.shadow_scoring._record_score_run",
        lambda *_args, **_kwargs: "dddddddd-dddd-dddd-dddd-dddddddddddd",
    )

    class DateOne(date):
        @classmethod
        def today(cls):
            return cls(1999, 1, 1)

    class DateTwo(date):
        @classmethod
        def today(cls):
            return cls(2040, 12, 31)

    monkeypatch.setattr("src.scoring.datetime.date", DateOne)
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    first = recompute_lead(
        conn,
        "11111111-1111-1111-1111-111111111111",
        trigger_type="manual",
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
        snapshot_timestamp=fixed_ts,
    )

    monkeypatch.setattr("src.scoring.datetime.date", DateTwo)
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    if hasattr(time, "tzset"):
        time.tzset()
    second = recompute_lead(
        conn,
        "11111111-1111-1111-1111-111111111111",
        trigger_type="nightly",
        scoring_version=SCORE_VERSION,
        weights_version=WEIGHTS_VERSION,
        rules_version=RULES_VERSION,
        model_version=MODEL_VERSION,
        snapshot_timestamp=fixed_ts,
    )

    assert first["evidence_hash"] == second["evidence_hash"]
    assert first["priority_score"] == second["priority_score"]
    assert first["score_version"] == second["score_version"]
    assert first["weights_version"] == second["weights_version"]
    assert first["rules_version"] == second["rules_version"]
    assert first["model_version"] == second["model_version"]
