"""The Relay 4-Step Pipeline Orchestrator."""

import time
import logging
from typing import Optional
from src.relay.gateway.retry_policy import RetryPolicy
from src.relay.pipeline.state import RequestContext, PipelineResult
from src.relay.pipeline.router import QueryRouter
from src.relay.pipeline.retriever_step import RetrieverStep
from src.relay.pipeline.generator_step import GeneratorStep
from src.relay.pipeline.verifier_step import VerifierStep, VerificationDecision
from src.relay.models.base import LLMProvider
from src.relay.models.groq_provider import GroqProviderError
from src.relay.retrieval.tool import RetrievalTool

logger = logging.getLogger(__name__)


class PipelineEngine:
    """Orchestrator for the 4-step research assistant pipeline."""

    def __init__(
        self,
        retrieval_tool: RetrievalTool,
        provider: LLMProvider,
        max_loops: int = 1,
        retry_policy: Optional[RetryPolicy] = None,
        use_streaming: bool = False,
    ):
        self.router = QueryRouter()
        self.retriever = RetrieverStep(retrieval_tool)
        self.generator = GeneratorStep(provider, retry_policy=retry_policy, use_streaming=use_streaming)
        self.verifier = VerifierStep()
        self.max_loops = max_loops

    async def process_request(self, ctx: RequestContext) -> PipelineResult:
        """Execute end-to-end pipeline with safety invariants and monotonic latency accounting."""
        t_work_start = time.perf_counter()

        # Step 0: Check deadline / TTL
        if ctx.is_expired():
            t_now = time.perf_counter()
            ctx.timers.total_ms = (t_now - ctx.start_mono) * 1000.0
            ctx.timers.working_ms = 0.0
            return PipelineResult(
                request_id=ctx.request_id,
                scenario=ctx.scenario,
                source=ctx.source,
                query=ctx.query,
                response_text="Request timed out prior to processing.",
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

        # Step 1: Query Router
        route = self.router.route(ctx.query)

        # Step 2: Retrieval Tool Execution (uses tool default_top_k and sheds if ctx.quota_pressure)
        passages, context_str = self.retriever.execute(ctx, route, top_k=None)

        # Step 3: Buffered Draft Generation (caught safely on provider errors / timeouts)
        try:
            draft_text, _, _, model_first_tok_ms = await self.generator.execute(
                ctx, context_str, passages
            )
            ctx.timers.model_first_token_ms = model_first_tok_ms
        except TimeoutError as exc:
            return self._error_result(ctx, t_work_start, status="timeout", verification_status="verifier_timeout", msg=str(exc))
        except GroqProviderError as exc:
            st = "rate_limited" if exc.status_code == 429 else ("timeout" if exc.status_code == 504 else "error")
            return self._error_result(ctx, t_work_start, status=st, verification_status="verifier_error", msg=str(exc))
        except Exception as exc:
            return self._error_result(ctx, t_work_start, status="error", verification_status="verifier_error", msg=str(exc))

        # Step 4: Verification & Safety Guard (MANDATORY INVARIANT)
        decision: VerificationDecision = self.verifier.verify(
            ctx, draft_text, passages, loop_count=0
        )

        # Branch/Loop: If ungrounded and loops remaining, re-retrieve once
        loop_iterations = 0
        while decision.loop_recommended and loop_iterations < self.max_loops:
            loop_iterations += 1
            if ctx.is_expired():
                return self._error_result(
                    ctx, t_work_start, status="timeout", verification_status="verifier_timeout",
                    msg="Request deadline exceeded before verification loop completed."
                )

            logger.info("Executing pipeline loop %d with query: %s", loop_iterations, decision.suggested_query)
            loop_passages, loop_context = self.retriever.execute(
                ctx, route, top_k=6, query_override=decision.suggested_query
            )
            try:
                loop_draft, _, _, _ = await self.generator.execute(ctx, loop_context, loop_passages)
            except TimeoutError as exc:
                return self._error_result(ctx, t_work_start, status="timeout", verification_status="verifier_timeout", msg=str(exc))
            except GroqProviderError as exc:
                st = "rate_limited" if exc.status_code == 429 else ("timeout" if exc.status_code == 504 else "error")
                return self._error_result(ctx, t_work_start, status=st, verification_status="verifier_error", msg=str(exc))
            except Exception as exc:
                return self._error_result(ctx, t_work_start, status="error", verification_status="verifier_error", msg=str(exc))

            decision = self.verifier.verify(ctx, loop_draft, loop_passages, loop_count=loop_iterations)

        t_finish = time.perf_counter()
        elapsed_after_work_start_ms = (t_finish - t_work_start) * 1000.0
        ctx.timers.working_ms = max(0.0, elapsed_after_work_start_ms - ctx.timers.retry_wait_ms)
        ctx.timers.user_first_token_ms = (t_finish - ctx.start_mono) * 1000.0
        ctx.timers.total_ms = (t_finish - ctx.start_mono) * 1000.0

        if decision.status == "verified_grounded":
            status = "ok"
            answer_grade = "correct"
        elif decision.status == "verified_refusal":
            status = "ok"
            answer_grade = "refused_correctly"
        else:
            status = "ok"
            answer_grade = "refused_correctly"

        return PipelineResult(
            request_id=ctx.request_id,
            scenario=ctx.scenario,
            source=ctx.source,
            query=ctx.query,
            response_text=decision.final_text,
            citations=decision.extracted_citations,
            status=status,
            answer_grade=answer_grade,
            verification_status=decision.status,
            retries=ctx.retries,
            tokens_in=ctx.tokens_in,
            tokens_out=ctx.tokens_out,
            retrieval_setting=ctx.retrieval_setting,
            timers=ctx.timers,
        )

    def _error_result(
        self,
        ctx: RequestContext,
        t_work_start: float,
        status: str,
        verification_status: str,
        msg: str,
    ) -> PipelineResult:
        t_finish = time.perf_counter()
        elapsed_ms = (t_finish - t_work_start) * 1000.0
        ctx.timers.working_ms = max(0.0, elapsed_ms - ctx.timers.retry_wait_ms)
        ctx.timers.total_ms = (t_finish - ctx.start_mono) * 1000.0
        ctx.timers.user_first_token_ms = ctx.timers.total_ms
        return PipelineResult(
            request_id=ctx.request_id,
            scenario=ctx.scenario,
            source=ctx.source,
            query=ctx.query,
            response_text=f"Safe fallback refusal ({status}): {msg}",
            citations=[],
            status=status,
            answer_grade="refused_wrongly",
            verification_status=verification_status,
            retries=ctx.retries,
            tokens_in=ctx.tokens_in,
            tokens_out=ctx.tokens_out,
            retrieval_setting=ctx.retrieval_setting,
            timers=ctx.timers,
        )
