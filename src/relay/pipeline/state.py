"""State management, request contexts, and latency tracking structures."""

import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class LatencyTimers:
    """Independent monotonic timing instrumentation conforming to Revision 3."""
    queue_wait_ms: float = 0.0
    working_ms: float = 0.0
    retry_wait_ms: float = 0.0
    model_first_token_ms: float = 0.0
    user_first_token_ms: float = 0.0
    total_ms: float = 0.0

    def compute_accounting_overhead(self) -> float:
        """Compute epsilon = total_ms - (queue_wait_ms + working_ms + retry_wait_ms)."""
        return self.total_ms - (self.queue_wait_ms + self.working_ms + self.retry_wait_ms)


@dataclass
class RequestContext:
    """Context carried through all pipeline stages."""
    query: str
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    scenario: str = "default"
    source: str = "real"  # 'real' or 'standin'
    retrieval_setting: str = "setting_1_bm25_base"
    ttl_seconds: float = 10.0

    start_mono: float = field(default_factory=time.perf_counter)
    deadline_mono: float = 0.0
    timers: LatencyTimers = field(default_factory=LatencyTimers)

    tokens_in: int = 0
    tokens_out: int = 0
    retries: int = 0
    quota_pressure: bool = False

    def __post_init__(self):
        if self.deadline_mono == 0.0:
            self.deadline_mono = self.start_mono + self.ttl_seconds

    def is_expired(self) -> bool:
        return time.perf_counter() > self.deadline_mono


@dataclass
class PipelineResult:
    """Final output produced by the 4-step pipeline."""
    request_id: str
    scenario: str
    source: str
    query: str
    response_text: str
    citations: List[str]
    status: str  # ok, rate_limited, timeout, error
    answer_grade: str  # correct, wrong, refused_correctly, refused_wrongly
    verification_status: str  # verified_grounded, verified_refusal, failed_verification, verifier_error, verifier_timeout
    retries: int
    tokens_in: int
    tokens_out: int
    retrieval_setting: str
    timers: LatencyTimers

