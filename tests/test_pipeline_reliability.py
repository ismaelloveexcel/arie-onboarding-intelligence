import pytest

from src.pipeline import _run_step


class FakeConn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_run_step_commits_successful_source():
    conn = FakeConn()

    result = _run_step(
        conn,
        "companies_house",
        lambda: 12,
        timeout_seconds=0,
    )

    assert result.status == "completed"
    assert result.count == 12
    assert conn.commits == 1
    assert conn.rollbacks == 0


def test_run_step_isolates_non_critical_failure():
    conn = FakeConn()

    result = _run_step(
        conn,
        "mauritius_mns",
        lambda: (_ for _ in ()).throw(RuntimeError("source unavailable")),
        timeout_seconds=0,
    )

    assert result.status == "failed"
    assert "source unavailable" in result.error
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_run_step_reraises_critical_failure():
    conn = FakeConn()

    with pytest.raises(RuntimeError, match="queue failed"):
        _run_step(
            conn,
            "queue_refresh",
            lambda: (_ for _ in ()).throw(RuntimeError("queue failed")),
            timeout_seconds=0,
            critical=True,
        )

    assert conn.commits == 0
    assert conn.rollbacks == 1
