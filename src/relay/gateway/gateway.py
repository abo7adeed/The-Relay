"""Bounded FIFO Admission Gateway, Concurrency Pool, TTL Cancelation, and Telemetry Integration."""

import asyncio
import time
from typing import Optional, List, Tuple

from relay.gateway.circuit_breaker import CircuitBreaker
from relay.gateway.rate_limiter import LocalRateLimiter
from relay.pipeline.engine import PipelineEngine
from relay.pipeline.state import RequestContext, PipelineResult


class AdmissionGateway:
    """Traffic control gateway enforcing bounded FIFO queuing, concurrency limits, TTL, and breaker state."""

    def __init__(
        self,
        engine: PipelineEngine,
        queue_max_size: int = 100,
        concurrency_limit: int = 5,
        rate_limiter: Optional[LocalRateLimiter] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self.engine = engine
        self.queue_max_size = queue_max_size
        self.concurrency_limit = concurrency_limit
        self.rate_limiter = rate_limiter or LocalRateLimiter()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

        self.queue: Optional[asyncio.Queue] = None
        self.workers: List[asyncio.Task] = []
        self.peak_queue_length: int = 0
        self.rejected_count: int = 0
        self._running: bool = False

    async def start(self) -> None:
        if self._running:
            return
        self.queue = asyncio.Queue(maxsize=self.queue_max_size)
        self.peak_queue_length = 0
        self.rejected_count = 0
        self._running = True
        self.workers = [
            asyncio.create_task(self._worker_loop(i))
            for i in range(self.concurrency_limit)
        ]

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self.queue is not None:
            for _ in self.workers:
                await self.queue.put(None)
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        self.queue = None

    async def _worker_loop(self, worker_id: int) -> None:
        assert self.queue is not None
        while True:
            item: Optional[Tuple[RequestContext, asyncio.Future]] = await self.queue.get()
            if item is None:
                self.queue.task_done()
                break

            ctx, fut = item
            try:
                t_dequeue = time.perf_counter()
                ctx.timers.queue_wait_ms = (t_dequeue - ctx.start_mono) * 1000.0

                # Fast-fail if circuit breaker is OPEN (3pm daily exhaustion scenario)
                if self.circuit_breaker.is_open():
                    t_now = time.perf_counter()
                    ctx.timers.working_ms = (t_now - t_dequeue) * 1000.0
                    ctx.timers.total_ms = (t_now - ctx.start_mono) * 1000.0
                    res = PipelineResult(
                        request_id=ctx.request_id,
                        scenario=ctx.scenario,
                        source=ctx.source,
                        query=ctx.query,
                        response_text="Daily provider token budget is exhausted for today. Please retry after 00:00 UTC reset.",
                        citations=[],
                        status="rate_limited",
                        answer_grade="refused_wrongly",
                        verification_status="failed_verification",
                        retries=ctx.retries,
                        tokens_in=0,
                        tokens_out=0,
                        retrieval_setting=ctx.retrieval_setting,
                        timers=ctx.timers,
                    )
                    if not fut.done():
                        fut.set_result(res)
                    continue

                # Check deadline expiration before executing
                if ctx.is_expired():
                    t_now = time.perf_counter()
                    ctx.timers.working_ms = 0.0
                    ctx.timers.total_ms = (t_now - ctx.start_mono) * 1000.0
                    res = PipelineResult(
                        request_id=ctx.request_id,
                        scenario=ctx.scenario,
                        source=ctx.source,
                        query=ctx.query,
                        response_text="Request timed out while waiting in queue prior to execution.",
                        citations=[],
                        status="timeout",
                        answer_grade="refused_wrongly",
                        verification_status="verifier_timeout",
                        retries=ctx.retries,
                        tokens_in=0,
                        tokens_out=0,
                        retrieval_setting=ctx.retrieval_setting,
                        timers=ctx.timers,
                    )
                    if not fut.done():
                        fut.set_result(res)
                    continue

                # Context shedding if approaching per-minute quota
                if self.rate_limiter.is_under_quota_pressure():
                    ctx.quota_pressure = True

                res = await self.engine.process_request(ctx)
                self.rate_limiter.record_usage(res.tokens_in + res.tokens_out)
                if not fut.done():
                    fut.set_result(res)

            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)
            finally:
                self.queue.task_done()

    async def submit(self, ctx: RequestContext) -> PipelineResult:
        """Admit a request into the bounded FIFO queue and await its verified PipelineResult."""
        if not self._running or self.queue is None:
            raise RuntimeError("AdmissionGateway must be started before submitting requests.")

        # Immediate rejection if circuit breaker OPEN
        if self.circuit_breaker.is_open():
            t_now = time.perf_counter()
            ctx.timers.queue_wait_ms = 0.0
            ctx.timers.working_ms = (t_now - ctx.start_mono) * 1000.0
            ctx.timers.total_ms = (t_now - ctx.start_mono) * 1000.0
            return PipelineResult(
                request_id=ctx.request_id,
                scenario=ctx.scenario,
                source=ctx.source,
                query=ctx.query,
                response_text="Daily provider token budget is exhausted for today. Service circuit breaker is OPEN.",
                citations=[],
                status="rate_limited",
                answer_grade="refused_wrongly",
                verification_status="failed_verification",
                retries=0,
                tokens_in=0,
                tokens_out=0,
                retrieval_setting=ctx.retrieval_setting,
                timers=ctx.timers,
            )

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        try:
            self.queue.put_nowait((ctx, fut))
            q_len = self.queue.qsize()
            if q_len > self.peak_queue_length:
                self.peak_queue_length = q_len
        except asyncio.QueueFull:
            self.rejected_count += 1
            t_now = time.perf_counter()
            ctx.timers.total_ms = (t_now - ctx.start_mono) * 1000.0
            return PipelineResult(
                request_id=ctx.request_id,
                scenario=ctx.scenario,
                source=ctx.source,
                query=ctx.query,
                response_text="System overload: admission queue is full. Please retry later.",
                citations=[],
                status="rate_limited",
                answer_grade="refused_wrongly",
                verification_status="failed_verification",
                retries=0,
                tokens_in=0,
                tokens_out=0,
                retrieval_setting=ctx.retrieval_setting,
                timers=ctx.timers,
            )

        return await fut

