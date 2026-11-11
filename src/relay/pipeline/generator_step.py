"""Step 3: Grounded Draft Generation Step (Buffered internally, never leaked to user)."""

import time
from typing import List, Tuple, Optional
from src.relay.gateway.retry_policy import RetryPolicy
from src.relay.models.base import LLMProvider, ModelResponse
from src.relay.pipeline.state import RequestContext
from src.relay.retrieval.tool import RetrievedPassage


SYSTEM_PROMPT = """You are a rigorous technical research assistant answering questions exclusively from the provided paper library.
CRITICAL INSTRUCTIONS:
1. Answer ONLY using the facts present in the provided evidence passages.
2. For EVERY factual statement, you MUST provide an exact inline citation in the format: [Paper Title, Section Title, Page X].
3. If the provided evidence passages DO NOT contain the answer, or if the question asks about concepts outside these papers, you MUST state: "The provided papers do not state sufficient evidence to answer this question." Do not guess, extrapolate, or use outside knowledge.
4. Do not speculate or make unsupported claims."""


class GeneratorStep:
    """Step 3: Generates an internal draft response that is BUFFERED in memory."""

    def __init__(
        self,
        provider: LLMProvider,
        retry_policy: Optional[RetryPolicy] = None,
        use_streaming: bool = False,
    ):
        self.provider = provider
        self.retry_policy = retry_policy
        self.use_streaming = use_streaming

    async def _invoke_once(self, messages: List[dict]) -> Tuple[str, int, int, float]:
        t_first_tok_abs: Optional[float] = None

        if self.use_streaming:
            chunks: List[str] = []
            tok_in = 0
            tok_out = 0
            async for sc in self.provider.generate_stream(messages, temperature=0.0, max_tokens=600):
                if sc.delta_content:
                    if t_first_tok_abs is None:
                        t_first_tok_abs = time.perf_counter()
                    chunks.append(sc.delta_content)
                if sc.tokens_in is not None:
                    tok_in = sc.tokens_in
                if sc.tokens_out is not None:
                    tok_out = sc.tokens_out
            if t_first_tok_abs is None:
                t_first_tok_abs = time.perf_counter()
            full_text = "".join(chunks)
            if tok_in == 0:
                tok_in = max(10, sum(len(m["content"]) for m in messages) // 4)
            if tok_out == 0:
                tok_out = max(1, len(full_text) // 4)
            return full_text, tok_in, tok_out, t_first_tok_abs

        resp: ModelResponse = await self.provider.generate(messages, temperature=0.0, max_tokens=600)
        t_first_tok_abs = time.perf_counter()
        return resp.content, resp.tokens_in, resp.tokens_out, t_first_tok_abs

    async def execute(
        self,
        ctx: RequestContext,
        context_str: str,
        passages: List[RetrievedPassage],
    ) -> Tuple[str, int, int, float]:
        """Generate draft response internally while buffering output before verification.

        Returns:
            Tuple of (draft_text, prompt_tokens, completion_tokens, model_first_token_ms)
        """
        user_prompt = f"""EVIDENCE PASSAGES FROM AUTHORITATIVE PAPERS:
{context_str}

USER QUESTION:
{ctx.query}

Please formulate a cited answer based strictly on the evidence above, or state that the papers do not say."""

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        if self.retry_policy is not None:
            content, tok_in, tok_out, t_first_abs = await self.retry_policy.execute_with_retry(
                ctx, lambda: self._invoke_once(messages)
            )
        else:
            content, tok_in, tok_out, t_first_abs = await self._invoke_once(messages)

        model_first_tok_ms = (t_first_abs - ctx.start_mono) * 1000.0
        ctx.tokens_in += tok_in
        ctx.tokens_out += tok_out

        return content, tok_in, tok_out, model_first_tok_ms
