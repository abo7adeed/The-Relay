"""Local sliding-window RPM/TPM rate limiter and quota-pressure detector."""

import time
from typing import List, Tuple


class LocalRateLimiter:
    """Tracks sliding 60-second request and token utilization and detects threshold pressure."""

    def __init__(
        self,
        rpm_limit: int = 30,
        tpm_limit: int = 6_000,
        degrade_threshold_pct: float = 85.0,
    ):
        self.rpm_limit = rpm_limit
        self.tpm_limit = tpm_limit
        self.degrade_threshold_pct = degrade_threshold_pct
        self._requests: List[float] = []
        self._tokens: List[Tuple[float, int]] = []

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        self._requests = [t for t in self._requests if t > cutoff]
        self._tokens = [(t, cnt) for (t, cnt) in self._tokens if t > cutoff]

    def record_usage(self, tokens_total: int) -> None:
        now = time.perf_counter()
        self._prune(now)
        self._requests.append(now)
        if tokens_total > 0:
            self._tokens.append((now, tokens_total))

    def current_utilization(self) -> Tuple[int, int]:
        now = time.perf_counter()
        self._prune(now)
        return len(self._requests), sum(cnt for (_, cnt) in self._tokens)

    def is_under_quota_pressure(self) -> bool:
        """Return True when TPM or RPM consumption reaches or exceeds configured pressure threshold (default 85%)."""
        rpm_used, tpm_used = self.current_utilization()
        tpm_pct = (tpm_used / max(1, self.tpm_limit)) * 100.0
        rpm_pct = (rpm_used / max(1, self.rpm_limit)) * 100.0
        return max(tpm_pct, rpm_pct) >= self.degrade_threshold_pct

