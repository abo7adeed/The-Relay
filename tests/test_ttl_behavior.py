"""Essential Acceptance Test 2: Request Deadline and TTL Behavior.

Validates that:
1. Requests expire according to configured client_ttl_s.
2. Expired requests are actively canceled before invoking downstream models.
3. Expiration is observable in telemetry (status: timeout, tokens_in: 0).
"""

import time
import pytest
from src.relay.pipeline.state import RequestContext
from src.relay.pipeline.engine import PipelineEngine
from src.relay.retrieval.tool import RetrievalTool
from src.relay.retrieval.bm25_index import BM25Index
from src.relay.models.base import LLMProvider, ModelResponse, StreamChunk
from typing import List, Dict, AsyncGenerator


class MockLLMProvider(LLMProvider):
    def __init__(self):
        self.call_count = 0

    async def generate(self, messages, temperature=0.0, max_tokens=1024) -> ModelResponse:
        self.call_count += 1
        return ModelResponse(
            content="Mock response",
            tokens_in=50,
            tokens_out=10,
            model_id="mock-llm",
            first_token_ms=10.0,
            total_ms=10.0
        )

    async def generate_stream(self, messages, temperature=0.0, max_tokens=1024) -> AsyncGenerator[StreamChunk, None]:
        self.call_count += 1
        yield StreamChunk(delta_content="Mock", finish_reason="stop")

    def get_limits(self):
        from src.relay.models.base import ProviderLimits
        return ProviderLimits(model_id="mock-llm")


@pytest.mark.asyncio
async def test_expired_request_not_executed():
    index = BM25Index([])
    tool = RetrievalTool(index)
    mock_provider = MockLLMProvider()
    engine = PipelineEngine(tool, mock_provider)

    # Create request with tiny TTL of 20ms and force it to expire
    ctx = RequestContext(query="What is Transformer?", ttl_seconds=0.02)
    time.sleep(0.03)  # Wait 30ms -> deadline exceeded!

    assert ctx.is_expired() is True

    result = await engine.process_request(ctx)

    # Downstream model MUST NOT be invoked!
    assert mock_provider.call_count == 0
    assert result.status == "timeout"
    assert result.tokens_in == 0
    assert result.tokens_out == 0
    assert result.timers.working_ms == 0.0
    assert "timed out" in result.response_text.lower()

