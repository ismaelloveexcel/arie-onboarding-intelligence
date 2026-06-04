from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from src.shadow_scoring import recompute_lead


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
    return conn, cur


def _extract_run_inserts(cur) -> tuple[tuple, tuple]:
    signal_insert = None
    evidence_insert = None
    for call in cur.execute.call_args_list:
        sql = call.args[0]
        params = call.args[1] if len(call.args) > 1 else ()
        if "INSERT INTO lead_signal_scores" in sql:
            signal_insert = params
        if "INSERT INTO lead_score_evidence" in sql:
            evidence_insert = params
    assert signal_insert is not None
    assert evidence_insert is not None
    return signal_insert, evidence_insert


def test_parity_manual_nightly_backfill_identical_invariants(monkeypatch):
    conn, cur = _mock_conn()
    fixed_snapshot = _snapshot()
    fixed_timestamp = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "src.shadow_scoring.load_lead_snapshot",
        lambda *_args, **_kwargs: fixed_snapshot,
    )
    monkeypatch.setattr(
        "src.shadow_scoring._record_score_run",
        lambda *_args, **_kwargs: "33333333-3333-3333-3333-333333333333",
    )

    per_trigger = {}
    for trigger in ("manual", "nightly", "backfill"):
        cur.execute.reset_mock()
        result = recompute_lead(
            conn,
            "11111111-1111-1111-1111-111111111111",
            trigger_type=trigger,
            snapshot_timestamp=fixed_timestamp,
        )
        signal_insert, evidence_insert = _extract_run_inserts(cur)
        per_trigger[trigger] = {
            "result": result,
            "signal_insert": signal_insert,
            "evidence_insert": evidence_insert,
        }

    manual = per_trigger["manual"]
    nightly = per_trigger["nightly"]
    backfill = per_trigger["backfill"]

    # Final score parity
    assert manual["result"]["priority_score"] == nightly["result"]["priority_score"]
    assert nightly["result"]["priority_score"] == backfill["result"]["priority_score"]

    # Evidence hash parity
    assert manual["result"]["evidence_hash"] == nightly["result"]["evidence_hash"]
    assert nightly["result"]["evidence_hash"] == backfill["result"]["evidence_hash"]

    # Component score + why parity from DB insert payload
    # params: (lead_id, snapshot_timestamp, fit, founder, keyword, risk, priority, ...)
    assert manual["signal_insert"][2:12] == nightly["signal_insert"][2:12]
    assert nightly["signal_insert"][2:12] == backfill["signal_insert"][2:12]

    # Evidence payload + why parity
    # params: (score_id, company_id, evidence_json, evidence_hash, why_output)
    assert manual["evidence_insert"][2:5] == nightly["evidence_insert"][2:5]
    assert nightly["evidence_insert"][2:5] == backfill["evidence_insert"][2:5]
