"""Circuit breaker protecting system capacity upon daily quota exhaustion or severe provider failures."""

import time
from typing import Optional


class CircuitBreaker:
    """Trips OPEN on daily quota exhaustion (never retries) or consecutive systemic 5xx bursts."""

    def __init__(self, reset_cooldown_s: float = 3600.0, consecutive_5xx_threshold: int = 10):
        self.state: str = "CLOSED"  # CLOSED, OPEN
        self.reason: str = ""
        self.opened_at_mono: Optional[float] = None
        self.reset_cooldown_s = reset_cooldown_s
        self.consecutive_5xx_threshold = consecutive_5xx_threshold
        self._consecutive_5xx = 0

    def is_open(self) -> bool:
        if self.state == "OPEN":
            if self.opened_at_mono is not None and (time.perf_counter() - self.opened_at_mono) >= self.reset_cooldown_s:
                self.close()
                return False
            return True
        return False

    def trip_daily_exhaustion(self, reason: str = "Daily token/request quota exhausted") -> None:
        self.state = "OPEN"
        self.reason = reason
        self.opened_at_mono = time.perf_counter()

    def record_5xx(self) -> None:
        self._consecutive_5xx += 1
        if self._consecutive_5xx >= self.consecutive_5xx_threshold:
            self.state = "OPEN"
            self.reason = f"Consecutive 5xx threshold ({self.consecutive_5xx_threshold}) breached"
            self.opened_at_mono = time.perf_counter()

    def record_success(self) -> None:
        self._consecutive_5xx = 0

    def close(self) -> None:
        self.state = "CLOSED"
        self.reason = ""
        self.opened_at_mono = None
        self._consecutive_5xx = 0

