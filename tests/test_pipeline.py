"""Integration tests for the 4-step pipeline engine (Milestone M4)."""

import os
import pytest
from relay.retrieval.bm25_index import BM25Index
from relay.retrieval.tool import RetrievalTool
from relay.pipeline.engine import PipelineEngine
from relay.pipeline.state import RequestContext
from relay.models.base import LLMProvider, ModelResponse, StreamChunk
from typing import AsyncGenerator


class ScriptedLLMProvider(LLMProvider):
    """Deterministic scripted provider for testing pipeline logic."""

    def __init__(self, scripted_responses):
        self.scripted_responses = scripted_responses
        self.index = 0

    async def generate(self, messages, temperature=0.0, max_tokens=1024) -> ModelResponse:
        content = self.scripted_responses[self.index % len(self.scripted_responses)]
        self.index += 1
        return ModelResponse(
            content=content,
            tokens_in=120,
            tokens_out=30,
            model_id="scripted-test-model",
            first_token_ms=25.0,
            total_ms=50.0,
        )

    async def generate_stream(self, messages, temperature=0.0, max_tokens=1024) -> AsyncGenerator[StreamChunk, None]:
        yield StreamChunk(delta_content="Scripted", finish_reason="stop")

    def get_limits(self):
        from relay.models.base import ProviderLimits
        return ProviderLimits(model_id="scripted-test-model")


@pytest.fixture
def retrieval_tool():
    path = "data/processed/chunks_setting_1.json"
    if not os.path.exists(path):
        pytest.skip(f"Processed chunks not found at {path}")
    index = BM25Index.from_json_file(path)
    return RetrievalTool(index)


@pytest.mark.asyncio
async def test_pipeline_happy_path_grounded(retrieval_tool):
    # Scripted response with valid citation to Attention paper
    valid_cited_text = (
        "In the Transformer, the Adam optimizer warmup steps are set to 4000 "
        "[Attention Is All You Need, 5.3 Optimizer, Page 7]."
    )
    provider = ScriptedLLMProvider([valid_cited_text])
    engine = PipelineEngine(retrieval_tool, provider)

    ctx = RequestContext(query="How many warmup steps were used in the Transformer?")
    result = await engine.process_request(ctx)

    assert result.status == "ok"
    assert result.verification_status == "verified_grounded"
    assert result.answer_grade == "correct"
    assert valid_cited_text in result.response_text
    assert len(result.citations) >= 1
    assert result.timers.working_ms > 0
    assert result.timers.user_first_token_ms > 0
    assert result.tokens_in > 0


@pytest.mark.asyncio
async def test_pipeline_refusal_for_unanswerable_query(retrieval_tool):
    refusal_text = "The provided papers do not state any information regarding Whisper or LLaMA 3."
    provider = ScriptedLLMProvider([refusal_text])
    engine = PipelineEngine(retrieval_tool, provider)

    ctx = RequestContext(query="What was the learning rate for Whisper on LibriSpeech?")
    result = await engine.process_request(ctx)

    assert result.status == "ok"
    assert result.verification_status == "verified_refusal"
    assert result.answer_grade == "refused_correctly"
    assert "do not state sufficient evidence" in result.response_text.lower()


@pytest.mark.asyncio
async def test_pipeline_re_retrieval_loop_on_missing_citation(retrieval_tool):
    # Response 1: missing citations (triggers loop)
    # Response 2: with valid citations (passes verification on loop 1)
    response_1_uncited = "The warmup steps were 4000 without citation."
    response_2_cited = (
        "Warmup steps were 4000 in Adam "
        "[Attention Is All You Need, 5.3 Optimizer, Page 7]."
    )
    provider = ScriptedLLMProvider([response_1_uncited, response_2_cited])
    engine = PipelineEngine(retrieval_tool, provider, max_loops=1)

    ctx = RequestContext(query="Warmup steps in optimizer?")
    result = await engine.process_request(ctx)

    # Provider should have been called twice (draft 1, then loop draft 2)
    assert provider.index == 2
    assert result.verification_status == "verified_grounded"
    assert result.status == "ok"
    assert response_2_cited in result.response_text

