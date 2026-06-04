from unittest.mock import MagicMock

from src.shadow_scoring import backfill_active_shadow_scores


def _mock_conn():
    cur = MagicMock()
    cursor_cm = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cursor_cm
    return conn, cur


def test_backfill_respects_max_batches_and_lock_timeout(monkeypatch):
    conn, cur = _mock_conn()
    calls = {"select": 0, "recompute": 0}

    def _select(_conn, *, stale_days, limit):
        calls["select"] += 1
        assert stale_days == 120
        assert limit == 2
        return ["lead-1", "lead-2"]

    def _recompute(_conn, lead_id, *, trigger_type):
        calls["recompute"] += 1
        assert trigger_type == "backfill"
        return {"lead_id": lead_id}

    monkeypatch.setattr("src.shadow_scoring.select_active_lead_ids", _select)
    monkeypatch.setattr("src.shadow_scoring.recompute_lead", _recompute)

    result = backfill_active_shadow_scores(
        conn,
        stale_days=120,
        batch_size=2,
        max_batches=2,
        lock_timeout_ms=3000,
    )

    assert result == {"batches_processed": 2, "scanned": 4, "scored": 4, "failed": 0}
    assert calls["select"] == 2
    assert calls["recompute"] == 4
    lock_timeout_calls = [
        item for item in cur.execute.call_args_list if "SET LOCAL lock_timeout" in item.args[0]
    ]
    assert len(lock_timeout_calls) == 2
    assert lock_timeout_calls[0].args[1] == ("3000ms",)


def test_backfill_counts_failures_without_crashing(monkeypatch):
    conn, _ = _mock_conn()

    monkeypatch.setattr(
        "src.shadow_scoring.select_active_lead_ids",
        lambda *_a, **_k: ["lead-1", "lead-2"],
    )

    def _recompute(_conn, lead_id, *, trigger_type):
        if lead_id == "lead-2":
            raise RuntimeError("forced failure")
        return {"lead_id": lead_id, "trigger_type": trigger_type}

    monkeypatch.setattr("src.shadow_scoring.recompute_lead", _recompute)

    result = backfill_active_shadow_scores(
        conn,
        stale_days=120,
        batch_size=2,
        max_batches=1,
        lock_timeout_ms=3000,
    )

    assert result == {"batches_processed": 1, "scanned": 2, "scored": 1, "failed": 1}


def test_backfill_exits_cleanly_when_no_active_leads(monkeypatch):
    conn, cur = _mock_conn()
    monkeypatch.setattr(
        "src.shadow_scoring.select_active_lead_ids",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "src.shadow_scoring.recompute_lead",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    result = backfill_active_shadow_scores(
        conn,
        stale_days=120,
        batch_size=2,
        max_batches=3,
        lock_timeout_ms=3000,
    )

    assert result == {"batches_processed": 0, "scanned": 0, "scored": 0, "failed": 0}
    lock_timeout_calls = [
        item for item in cur.execute.call_args_list if "SET LOCAL lock_timeout" in item.args[0]
    ]
    # First loop iteration still sets lock timeout before discovering empty selection.
    assert len(lock_timeout_calls) == 1
