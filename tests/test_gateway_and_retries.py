"""Tests for AdmissionGateway, bounded FIFO queue, circuit breaker, retry budget, and context shedding."""

import asyncio
import pytest
from typing import AsyncGenerator

from relay.gateway.circuit_breaker import CircuitBreaker
from relay.gateway.gateway import AdmissionGateway
from relay.gateway.rate_limiter import LocalRateLimiter
from relay.gateway.retry_policy import RetryPolicy
from relay.models.base import LLMProvider, ModelResponse, StreamChunk, ProviderLimits
from relay.models.groq_provider import GroqProviderError
from relay.pipeline.engine import PipelineEngine
from relay.pipeline.state import RequestContext
from relay.retrieval.bm25_index import BM25Index
from relay.retrieval.tool import RetrievalTool


class FlakyProvider(LLMProvider):
    """Provider injecting configurable 429s, daily exhaustion, or delays."""

    def __init__(self, fail_first_n: int = 0, daily_exhaust: bool = False, delay_s: float = 0.01):
        self.fail_first_n = fail_first_n
        self.daily_exhaust = daily_exhaust
        self.delay_s = delay_s
        self.calls = 0

    async def generate(self, messages, temperature=0.0, max_tokens=1024) -> ModelResponse:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        if self.daily_exhaust:
            raise GroqProviderError(
                "Daily token limit reached", status_code=429, retry_after=28800, error_code="daily_quota_exhausted"
            )
        if self.calls <= self.fail_first_n:
            raise GroqProviderError("Rate limit (429)", status_code=429, retry_after=0.04)
        return ModelResponse(
            content="Warmup steps were 4000 [Attention Is All You Need, 5.3 Optimizer, Page 7].",
            tokens_in=150,
            tokens_out=25,
            model_id="flaky-test",
            first_token_ms=10.0,
            total_ms=15.0,
        )

    async def generate_stream(self, messages, temperature=0.0, max_tokens=1024) -> AsyncGenerator[StreamChunk, None]:
        yield StreamChunk(delta_content="ok", finish_reason="stop")

    def get_limits(self) -> ProviderLimits:
        return ProviderLimits(model_id="flaky-test")


def _build_attention_tool() -> RetrievalTool:
    chunks = [
        {
            "chunk_id": "1706.03762v7_c0022",
            "paper_id": "1706.03762v7",
            "paper_title": "Attention Is All You Need",
            "version": "v7",
            "section_title": "5.3 Optimizer",
            "page_number": 7,
            "text": "We used warmup _ steps = 4000 .",
        }
    ]
    return RetrievalTool(BM25Index(chunks))


@pytest.mark.asyncio
async def test_retry_policy_accounting_and_no_daily_retry():
    tool = _build_attention_tool()
    breaker = CircuitBreaker()
    retry_pol = RetryPolicy(max_attempts=2, retry_budget_pct=100.0, backoff_base_s=0.04, circuit_breaker=breaker)
    provider = FlakyProvider(fail_first_n=1, delay_s=0.02)
    engine = PipelineEngine(tool, provider, retry_policy=retry_pol)

    ctx = RequestContext(query="warmup steps 4000?", ttl_seconds=5.0)
    res = await engine.process_request(ctx)

    assert res.status == "ok"
    assert res.retries == 1
    assert provider.calls == 2
    assert res.timers.retry_wait_ms >= 30.0
    assert res.timers.working_ms >= 35.0  # includes both attempt 1 and attempt 2 execution
    epsilon = abs(res.timers.total_ms - (res.timers.queue_wait_ms + res.timers.working_ms + res.timers.retry_wait_ms))
    assert epsilon < 15.0

    # Now test daily exhaustion -> MUST NOT retry and MUST trip breaker OPEN
    breaker_daily = CircuitBreaker()
    retry_daily = RetryPolicy(max_attempts=2, circuit_breaker=breaker_daily)
    provider_daily = FlakyProvider(daily_exhaust=True, delay_s=0.005)
    engine_daily = PipelineEngine(tool, provider_daily, retry_policy=retry_daily)

    ctx2 = RequestContext(query="warmup steps?", ttl_seconds=5.0)
    res2 = await engine_daily.process_request(ctx2)
    assert res2.status == "rate_limited"
    assert res2.retries == 0
    assert provider_daily.calls == 1
    assert breaker_daily.is_open() is True


@pytest.mark.asyncio
async def test_retry_policy_max_attempts_is_total_attempts_not_retries():
    tool = _build_attention_tool()
    provider = FlakyProvider(fail_first_n=99, delay_s=0.005)
    retry_pol = RetryPolicy(
        max_attempts=2,
        retry_budget_pct=100.0,
        backoff_base_s=0.001,
        circuit_breaker=CircuitBreaker(),
    )
    engine = PipelineEngine(tool, provider, retry_policy=retry_pol)

    ctx = RequestContext(query="warmup steps 4000?", ttl_seconds=5.0)
    res = await engine.process_request(ctx)

    assert res.status == "rate_limited"
    assert provider.calls == 2, "max_attempts=2 must mean two total calls, not three"
    assert res.retries == 1


@pytest.mark.asyncio
async def test_gateway_fifo_queue_ttl_and_breaker():
    tool = _build_attention_tool()
    breaker = CircuitBreaker()
    provider = FlakyProvider(delay_s=0.12)
    engine = PipelineEngine(tool, provider)
    # Concurrency = 1, max_queue = 2
    gw = AdmissionGateway(engine, queue_max_size=2, concurrency_limit=1, circuit_breaker=breaker)
    await gw.start()

    try:
        # Submit 1 active + 2 queued + 1 overflow (queue full!)
        r1 = RequestContext(query="warmup steps 4000?", ttl_seconds=2.0)
        r2 = RequestContext(query="warmup steps 4000?", ttl_seconds=0.05)  # Will expire in queue behind r1!
        r3 = RequestContext(query="warmup steps 4000?", ttl_seconds=2.0)
        r4 = RequestContext(query="warmup steps 4000?", ttl_seconds=2.0)

        t1 = asyncio.create_task(gw.submit(r1))
        await asyncio.sleep(0.01)  # ensure r1 is picked up by the single worker
        t2 = asyncio.create_task(gw.submit(r2))
        t3 = asyncio.create_task(gw.submit(r3))
        t4 = asyncio.create_task(gw.submit(r4))  # queue maxsize=2 -> overflow

        res1, res2, res3, res4 = await asyncio.gather(t1, t2, t3, t4)
        assert res1.status == "ok"
        assert res2.status == "timeout"
        assert res2.tokens_in == 0
        assert res3.status == "ok"
        assert res4.status == "rate_limited"  # Queue full shedding
        assert gw.rejected_count == 1
    finally:
        await gw.stop()


def test_rate_limiter_quota_pressure_triggers_context_shedding():
    limiter = LocalRateLimiter(rpm_limit=30, tpm_limit=1000, degrade_threshold_pct=85.0)
    assert limiter.is_under_quota_pressure() is False
    limiter.record_usage(860)
    assert limiter.is_under_quota_pressure() is True

    chunks = [
        {"chunk_id": f"c{i}", "paper_id": "p1", "paper_title": "Attention", "version": "v7",
         "section_title": "Sec", "page_number": i + 1, "text": f"warmup steps {i}"}
        for i in range(6)
    ]
    tool = RetrievalTool(BM25Index(chunks), default_top_k=4)
    normal = tool.retrieve_passages("warmup steps", quota_pressure=False)
    shed = tool.retrieve_passages("warmup steps", quota_pressure=True)
    assert len(normal) == 4
    assert len(shed) == 2

