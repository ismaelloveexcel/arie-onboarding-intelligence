from unittest.mock import MagicMock

import pytest

from src.ingestion.enrichment.base import (
    BaseEnrichmentProvider,
    ProviderCircuitOpen,
    ProviderLimitExceeded,
    ProviderRunContext,
)


class _FakeProvider(BaseEnrichmentProvider):
    def __init__(self, *, fail_times: int = 0, now_fn=None, sleep_fn=None):
        super().__init__(
            provider_name="fake",
            daily_cap=5,
            per_run_cap=2,
            max_retries=2,
            failure_threshold=2,
            circuit_cooldown_seconds=60,
            cache_ttl_seconds=600,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
        )
        self.fail_times = fail_times
        self.calls = 0

    def _fetch_impl(self, company):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("temporary provider failure")
        return {"company_id": company["id"], "ok": True, "attempts": self.calls}


def test_provider_enforces_per_run_cap():
    provider = _FakeProvider()
    run = ProviderRunContext(calls_made=2)
    with pytest.raises(ProviderLimitExceeded):
        provider.fetch(company={"id": "c1"}, request_cache_key=None, run_context=run)


def test_provider_retries_then_succeeds():
    sleeper = MagicMock()
    provider = _FakeProvider(fail_times=1, sleep_fn=sleeper)
    run = ProviderRunContext()
    result = provider.fetch(company={"id": "c1"}, request_cache_key=None, run_context=run)
    assert result["ok"] is True
    assert provider.calls == 2
    sleeper.assert_called_once()


def test_provider_opens_circuit_after_failure_threshold():
    current_time = {"value": 1000.0}

    def _now():
        return current_time["value"]

    provider = _FakeProvider(fail_times=10, now_fn=_now, sleep_fn=lambda *_: None)
    run = ProviderRunContext()
    with pytest.raises(RuntimeError):
        provider.fetch(company={"id": "c1"}, request_cache_key=None, run_context=run)

    with pytest.raises(ProviderCircuitOpen):
        provider.fetch(company={"id": "c2"}, request_cache_key=None, run_context=run)


def test_provider_cache_returns_cached_payload():
    provider = _FakeProvider()
    run = ProviderRunContext()

    first = provider.fetch(
        company={"id": "c1"},
        request_cache_key="provider:c1",
        run_context=run,
    )
    second = provider.fetch(
        company={"id": "c1"},
        request_cache_key="provider:c1",
        run_context=run,
    )

    assert first == second
    assert provider.calls == 1
