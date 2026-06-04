from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class ProviderLimitExceeded(RuntimeError):
    """Raised when daily/per-run provider caps are exceeded."""


class ProviderCircuitOpen(RuntimeError):
    """Raised when the provider circuit breaker is open."""


@dataclass
class ProviderRunContext:
    calls_made: int = 0


class BaseEnrichmentProvider:
    """
    Shared provider guardrails. Providers only implement `_fetch_impl`.

    Controls enforced centrally:
    - daily cap
    - per-run cap
    - retries with bounded backoff
    - circuit breaker
    - cache TTL
    - telemetry + health metrics
    """

    def __init__(
        self,
        *,
        provider_name: str,
        daily_cap: int,
        per_run_cap: int,
        max_retries: int = 2,
        failure_threshold: int = 3,
        circuit_cooldown_seconds: int = 300,
        cache_ttl_seconds: int = 1800,
        now_fn: Any = None,
        sleep_fn: Any = None,
    ) -> None:
        self.provider_name = provider_name
        self.daily_cap = daily_cap
        self.per_run_cap = per_run_cap
        self.max_retries = max_retries
        self.failure_threshold = failure_threshold
        self.circuit_cooldown_seconds = circuit_cooldown_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self._now_fn = now_fn or time.time
        self._sleep_fn = sleep_fn or time.sleep

        self._daily_usage = 0
        self._daily_window_key = self._current_day_key()
        self._failure_streak = 0
        self._circuit_open_until = 0.0
        self._cache: dict[str, tuple[float, Any]] = {}
        self._telemetry: list[dict[str, Any]] = []

    def _current_day_key(self) -> str:
        ts = datetime.fromtimestamp(self._now_fn(), tz=timezone.utc)
        return ts.strftime("%Y-%m-%d")

    def _reset_daily_usage_if_needed(self) -> None:
        day_key = self._current_day_key()
        if day_key != self._daily_window_key:
            self._daily_window_key = day_key
            self._daily_usage = 0

    def _record(self, event: str, **data: Any) -> None:
        self._telemetry.append(
            {
                "event": event,
                "provider": self.provider_name,
                "timestamp": datetime.fromtimestamp(
                    self._now_fn(), tz=timezone.utc
                ).isoformat(),
                **data,
            }
        )

    def drain_telemetry(self) -> list[dict[str, Any]]:
        events = list(self._telemetry)
        self._telemetry.clear()
        return events

    def health_snapshot(self) -> dict[str, Any]:
        self._reset_daily_usage_if_needed()
        return {
            "provider": self.provider_name,
            "daily_usage": self._daily_usage,
            "daily_cap": self.daily_cap,
            "failure_streak": self._failure_streak,
            "circuit_open": self._now_fn() < self._circuit_open_until,
            "circuit_open_until": self._circuit_open_until or None,
            "cache_entries": len(self._cache),
        }

    def _retry_delay_seconds(self, attempt_number: int) -> float:
        # 0.5s, 1.0s, 2.0s (bounded exponential)
        return min(2.0, 0.5 * (2 ** max(0, attempt_number - 1)))

    def _fetch_impl(self, company: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def fetch(
        self,
        *,
        company: dict[str, Any],
        request_cache_key: str | None,
        run_context: ProviderRunContext,
    ) -> dict[str, Any]:
        self._reset_daily_usage_if_needed()

        if run_context.calls_made >= self.per_run_cap:
            self._record(
                "provider_per_run_cap_exceeded",
                per_run_cap=self.per_run_cap,
                calls_made=run_context.calls_made,
            )
            raise ProviderLimitExceeded("per-run cap exceeded")

        if self._daily_usage >= self.daily_cap:
            self._record(
                "provider_daily_cap_exceeded",
                daily_cap=self.daily_cap,
                daily_usage=self._daily_usage,
            )
            raise ProviderLimitExceeded("daily cap exceeded")

        now = self._now_fn()
        if now < self._circuit_open_until:
            self._record(
                "provider_circuit_open",
                circuit_open_until=self._circuit_open_until,
                failure_streak=self._failure_streak,
            )
            raise ProviderCircuitOpen("circuit breaker open")

        if request_cache_key:
            cached = self._cache.get(request_cache_key)
            if cached:
                expires_at, payload = cached
                if now < expires_at:
                    self._record("provider_cache_hit", cache_key=request_cache_key)
                    return payload
                self._cache.pop(request_cache_key, None)

        attempt = 0
        start = self._now_fn()
        while True:
            attempt += 1
            try:
                payload = self._fetch_impl(company)
                self._failure_streak = 0
                self._daily_usage += 1
                run_context.calls_made += 1
                if request_cache_key:
                    self._cache[request_cache_key] = (
                        self._now_fn() + self.cache_ttl_seconds,
                        payload,
                    )
                duration_ms = int((self._now_fn() - start) * 1000)
                self._record(
                    "provider_fetch_success",
                    attempt=attempt,
                    duration_ms=duration_ms,
                    daily_usage=self._daily_usage,
                )
                return payload
            except Exception as exc:
                self._failure_streak += 1
                self._record(
                    "provider_fetch_failure",
                    attempt=attempt,
                    error=str(exc),
                    failure_streak=self._failure_streak,
                )
                if self._failure_streak >= self.failure_threshold:
                    self._circuit_open_until = self._now_fn() + self.circuit_cooldown_seconds
                    self._record(
                        "provider_circuit_opened",
                        failure_streak=self._failure_streak,
                        circuit_open_until=self._circuit_open_until,
                    )
                if attempt > self.max_retries:
                    raise
                self._sleep_fn(self._retry_delay_seconds(attempt))
