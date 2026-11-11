"""Retry policy with full jitter, global retry budget (15%), deadline guard, and strict timer isolation."""

import asyncio
import random
import time
from typing import Awaitable, Callable, TypeVar

from relay.gateway.circuit_breaker import CircuitBreaker
from relay.models.groq_provider import GroqProviderError
from relay.pipeline.state import RequestContext

T = TypeVar("T")


class RetryPolicy:
    """Executes async provider calls with bounded retries, jitter, retry budget, and TTL enforcement."""

    def __init__(
        self,
        max_attempts: int = 2,
        retry_budget_pct: float = 15.0,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 4.0,
        circuit_breaker: CircuitBreaker | None = None,
    ):
        self.max_attempts = max_attempts
        self.retry_budget_pct = retry_budget_pct
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

        # Global window tracking for retry budget enforcement
        self.total_primary_calls: int = 0
        self.total_retry_calls: int = 0

    def can_spend_retry_budget(self) -> bool:
        if self.total_primary_calls < 5:
            return True
        current_pct = (self.total_retry_calls / max(1, self.total_primary_calls)) * 100.0
        return current_pct < self.retry_budget_pct

    async def execute_with_retry(
        self,
        ctx: RequestContext,
        fn: Callable[[], Awaitable[T]],
    ) -> T:
        """Invoke fn() with deadline checks and isolated retry_wait_ms sleep accounting."""
        self.total_primary_calls += 1
        attempt = 1

        while True:
            if ctx.is_expired():
                raise TimeoutError("Client deadline expired prior to provider dispatch.")

            if self.circuit_breaker.is_open():
                raise GroqProviderError(
                    f"Circuit breaker OPEN: {self.circuit_breaker.reason}",
                    status_code=429,
                    error_code="daily_quota_exhausted",
                )

            try:
                result = await fn()
                self.circuit_breaker.record_success()
                return result

            except GroqProviderError as err:
                # Rule 2: Daily quota exhaustion -> NEVER retry; trip breaker OPEN immediately
                if err.is_daily_quota_exhausted:
                    self.circuit_breaker.trip_daily_exhaustion(str(err))
                    raise

                # Non-retryable client/auth errors (400, 401, 403, 404)
                if err.status_code is not None and err.status_code in {400, 401, 403, 404}:
                    raise

                if err.status_code is not None and err.status_code >= 500:
                    self.circuit_breaker.record_5xx()

                # Check if retries remain, budget allows, and deadline has slack
                if attempt >= self.max_attempts:
                    raise
                if not self.can_spend_retry_budget():
                    raise

                remaining_s = ctx.deadline_mono - time.perf_counter()
                if remaining_s <= 0.05:
                    raise TimeoutError("Insufficient TTL remaining for retry backoff.") from err

                # Exponential backoff with full jitter
                cap = min(self.backoff_max_s, self.backoff_base_s * (2 ** attempt))
                sleep_s = random.uniform(0.0, cap)
                if err.retry_after is not None:
                    # Respect upstream retry-after if within remaining deadline
                    if err.retry_after > remaining_s:
                        raise GroqProviderError(
                            f"Upstream retry-after ({err.retry_after}s) exceeds remaining TTL ({remaining_s:.2f}s)",
                            status_code=429,
                            retry_after=err.retry_after,
                            headers=err.headers,
                        ) from err
                    sleep_s = max(sleep_s, min(err.retry_after, remaining_s * 0.8))
                else:
                    sleep_s = min(sleep_s, remaining_s * 0.5)

                attempt += 1
                self.total_retry_calls += 1
                ctx.retries += 1

                # Strictly isolate pure sleep interval into retry_wait_ms
                t_sleep_start = time.perf_counter()
                await asyncio.sleep(sleep_s)
                ctx.timers.retry_wait_ms += (time.perf_counter() - t_sleep_start) * 1000.0

