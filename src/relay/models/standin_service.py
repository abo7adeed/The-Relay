"""Stand-in controllable model service implementing Groq/OpenAI-compatible REST and SSE endpoints."""

import asyncio
import json
import math
import random
import re
import time
from typing import AsyncGenerator, Dict, List, Optional, Tuple, Any
from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Stand-in Groq Model Service", version="1.1.0")


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "llama-3.1-8b-instant"
    messages: List[ChatMessage]
    temperature: float = 0.0
    max_tokens: int = 1024
    stream: bool = False


class StandInConfig:
    """Configurable simulation parameters calibrated to Groq LPU free-tier behavior."""
    # Latency distribution: Log-normal parameters (calibrated to median ~250ms TTFT)
    ttft_mu: float = 5.45        # exp(5.45) ~ 233 ms median base TTFT
    ttft_sigma: float = 0.35     # Jitter standard deviation
    inter_token_delay_s: float = 0.006  # ~160 tokens/sec simulated stream rate

    # Concurrency and saturation
    capacity_limit: int = 5
    saturation_exponent: float = 1.2
    active_requests: int = 0

    # Rate limits (simulated free tier matching documented llama-3.1-8b-instant)
    rpm_limit: int = 30
    tpm_limit: int = 6_000
    daily_token_limit: int = 500_000

    # Sliding window counters
    request_window: List[float] = []
    token_window: List[Tuple[float, int]] = []
    cumulative_daily_tokens: int = 0

    # Fault & scenario injection
    fault_probability: float = 0.0
    force_daily_exhaustion: bool = False
    rpm_override: Optional[int] = None
    tpm_override: Optional[int] = None


config = StandInConfig()


def sample_ttft() -> float:
    """Sample base TTFT in seconds from calibrated Log-Normal distribution with concurrency degradation."""
    base_ms = random.lognormvariate(config.ttft_mu, config.ttft_sigma)
    concurrency_ratio = config.active_requests / max(1, config.capacity_limit)
    degraded_ms = base_ms * (1.0 + config.saturation_exponent * max(0.0, concurrency_ratio - 1.0))
    return max(0.04, degraded_ms / 1000.0)


def check_rate_limits(incoming_tokens: int) -> Optional[Dict[str, Any]]:
    """Check sliding window RPM/TPM and daily quota limits."""
    now = time.time()
    cutoff_min = now - 60.0

    config.request_window = [t for t in config.request_window if t > cutoff_min]
    config.token_window = [(t, count) for (t, count) in config.token_window if t > cutoff_min]

    effective_rpm = config.rpm_override if config.rpm_override is not None else config.rpm_limit
    effective_tpm = config.tpm_override if config.tpm_override is not None else config.tpm_limit

    # 1. Daily Quota Check
    if config.force_daily_exhaustion or (config.cumulative_daily_tokens + incoming_tokens > config.daily_token_limit):
        return {
            "status": 429,
            "retry_after": 28800,  # 8 hours (until 00:00 UTC)
            "error": {
                "message": "Daily token limit reached for organization. Resets at 00:00 UTC.",
                "type": "tokens",
                "code": "daily_quota_exhausted",
            },
        }

    # 2. Per-Minute RPM Check
    if len(config.request_window) >= effective_rpm:
        oldest = config.request_window[0]
        retry_after = max(1, math.ceil(60.0 - (now - oldest)))
        return {
            "status": 429,
            "retry_after": retry_after,
            "error": {
                "message": f"Rate limit reached for model ({effective_rpm} RPM). Please try again in {retry_after}s.",
                "type": "requests",
                "code": "rate_limit_exceeded",
            },
        }

    # 3. Per-Minute TPM Check
    current_min_tokens = sum(cnt for (_, cnt) in config.token_window)
    if current_min_tokens + incoming_tokens > effective_tpm:
        return {
            "status": 429,
            "retry_after": 3,
            "error": {
                "message": f"Rate limit reached for tokens per minute ({effective_tpm} TPM).",
                "type": "tokens",
                "code": "rate_limit_exceeded",
            },
        }

    # Admitted: record in windows
    config.request_window.append(now)
    config.token_window.append((now, incoming_tokens))
    config.cumulative_daily_tokens += incoming_tokens
    return None


def _extract_question_and_context(messages: List[ChatMessage]) -> Tuple[str, str]:
    user_content = ""
    for m in messages:
        if m.role == "user":
            user_content = m.content
    q_part = user_content
    ctx_part = user_content
    if "USER QUESTION:" in user_content:
        parts = user_content.split("USER QUESTION:", 1)
        ctx_part = parts[0]
        q_part = parts[1].split("Please formulate", 1)[0].strip()
    return q_part.lower(), ctx_part


def _norm_nums(text: str) -> str:
    return re.sub(r"(\d)\s*\.\s*(\d)", r"\1.\2", text)


def _find_citation_in_context(ctx_text: str, keywords: List[str]) -> Optional[str]:
    """Find the passage header [Paper, Section, Page X] whose passage body contains all keywords."""
    blocks = re.split(r"---\s*EVIDENCE PASSAGE \d+\s+", ctx_text)
    for b in blocks[1:]:
        m = re.match(r"(\[[^\]]+\])\s*---\s*(.*)", b, re.DOTALL)
        if not m:
            continue
        cite, body = m.group(1), _norm_nums(m.group(2).lower())
        if all(_norm_nums(kw.lower()) in body for kw in keywords):
            return cite
    return None


def generate_synthesized_response(messages: List[ChatMessage]) -> str:
    """Generate response strictly grounded in the retrieved evidence passages provided in the prompt."""
    q_lower, ctx_text = _extract_question_and_context(messages)
    ctx_lower = ctx_text.lower()

    # 1. Explicit Out-of-library or Cross-corpus False Friend Traps
    if any(k in q_lower for k in ["llama 3", "llama-3", "whisper"]):
        return "The provided papers do not state sufficient evidence to answer this question."
    if "streaming speech tokens" in q_lower and "h-net" in q_lower:
        return "The provided papers do not state sufficient evidence to answer this question."
    if "attention is all you need" in q_lower and "dynamically adjust chunk boundaries" in q_lower:
        return "The provided papers do not state sufficient evidence to answer this question."

    # 2. Check evidence passages for each factual question type
    if "warmup" in q_lower:
        cite = _find_citation_in_context(ctx_text, ["4000", "warmup"])
        if cite:
            return f"In the Transformer, warmup_steps = 4000 were used in the Adam optimizer learning rate schedule {cite}."
    if "dropout" in q_lower:
        cite = _find_citation_in_context(ctx_text, ["dropout", "embeddings", "0 . 1"]) or _find_citation_in_context(ctx_text, ["dropout", "0.1"])
        if cite:
            return f"A dropout rate of P_drop = 0.1 was applied to the sums of the embeddings and positional encodings in both stacks {cite}."
    if "smoothing" in q_lower:
        cite = _find_citation_in_context(ctx_text, ["label smoothing", "0 . 1"]) or _find_citation_in_context(ctx_text, ["label smoothing"])
        if cite:
            return f"During training, label smoothing of value epsilon_ls = 0.1 was employed {cite}."
    if ("dimension" in q_lower or "d_k" in q_lower) and ("head" in q_lower or "64" in ctx_lower):
        cite = _find_citation_in_context(ctx_text, ["64", "heads"])
        if cite:
            return f"For each of the h = 8 parallel attention heads, d_k = d_v = d_model / h = 64 {cite}."
    if "bitrate" in q_lower or ("frame rate" in q_lower and "mimi" in q_lower):
        cite11 = _find_citation_in_context(ctx_text, ["1.1kbps", "12.5"])
        if cite11:
            return f"At 12.5Hz, Mimi represents a bitrate of 1.1kbps {cite11}."
        cite10 = _find_citation_in_context(ctx_text, ["12.5 frames", "80ms"])
        if cite10:
            return f"Mimi projects audio to 12.5 frames per second with an 80ms stride {cite10}."
    if "acoustic delay" in q_lower:
        cite26 = _find_citation_in_context(ctx_text, ["160ms", "acoustic delay"])
        if cite26:
            cite10 = _find_citation_in_context(ctx_text, ["80ms", "stride"])
            if cite10:
                return f"Each audio step corresponds to an 80ms stride {cite10}, and Moshi settles on a 160ms theoretical latency using an acoustic delay of 2 during pretraining and 1 during finetuning {cite26}."
            return f"Moshi settles on a theoretical latency of 160ms by pretraining with an acoustic delay of 2 and finetuning with an acoustic delay of 1 {cite26}."
    if "rvq" in q_lower or "codebook" in q_lower:
        cite = _find_citation_in_context(ctx_text, ["2048", "quantizers"]) or _find_citation_in_context(ctx_text, ["quantizers"])
        if cite:
            return f"Mimi utilizes Q = 8 RVQ quantizers, each with a codebook size of N_A = 2048 {cite}."
    if "latency" in q_lower and "moshi" in q_lower:
        cite = _find_citation_in_context(ctx_text, ["160ms", "200ms"])
        if cite:
            return f"Moshi achieves a theoretical latency of 160ms and 200ms in practice {cite}."
    if "dna" in q_lower and ("efficiency" in q_lower or "gain" in q_lower):
        cite_abs = _find_citation_in_context(ctx_text, ["dna", "4 ×"])
        if cite_abs:
            return f"H-Net achieved nearly a 4x improvement in data efficiency over baselines on DNA sequences {cite_abs}."
        cite_p4 = _find_citation_in_context(ctx_text, ["dna", "3.6"])
        if cite_p4:
            return f"H-Net demonstrated 3.6x improved data efficiency on DNA language modeling compared to isotropic models {cite_p4}."
    if "chunk boundaries" in q_lower or ("boundary" in q_lower and "tokenizer" in q_lower):
        cite = _find_citation_in_context(ctx_text, ["cosine similarity", "routing"])
        if cite:
            return f"H-Net's chunking layer uses a routing module that computes cosine similarity between adjacent encoder outputs to predict boundary probabilities p_t and discrete indicators b_t {cite}."
    if "modalities" in q_lower or ("chinese" in ctx_lower and "dna" in ctx_lower):
        cite = _find_citation_in_context(ctx_text, ["chinese", "dna", "code"])
        if cite:
            return f"Beyond English text, H-Net was evaluated on Chinese text, computer source code, and DNA sequences {cite}."

    # Fallback: If prompt comes from an isolated unit test / load stress prompt with context citation
    m_any = re.search(r"(\[[^\]]+,\s*[^\]]+,\s*Page\s*\d+\])", ctx_text)
    if m_any and ("no relevant passages" not in ctx_lower) and len(ctx_text) > 100:
        # Only refuse if it looks like an unanswerable test without matching facts
        if "exact section" in q_lower or "specification details" in q_lower:
            return f"Based on the retrieved specification, the mechanism is documented here {m_any.group(1)}."

    return "The provided papers do not state sufficient evidence to answer this question."


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, response: Response):
    total_prompt_chars = sum(len(m.content) for m in req.messages)
    estimated_prompt_tokens = max(10, total_prompt_chars // 4)

    # 1. Rate Limiting Check
    rate_error = check_rate_limits(estimated_prompt_tokens)
    if rate_error:
        response.status_code = 429
        response.headers["retry-after"] = str(rate_error["retry_after"])
        response.headers["x-ratelimit-limit-requests"] = str(config.rpm_override or config.rpm_limit)
        response.headers["x-ratelimit-limit-tokens"] = str(config.tpm_override or config.tpm_limit)
        response.headers["x-ratelimit-remaining-requests"] = "0"
        response.headers["x-ratelimit-remaining-tokens"] = "0"
        return {"error": rate_error["error"]}

    # 2. Fault Injection Check
    if config.fault_probability > 0.0 and random.random() < config.fault_probability:
        response.status_code = 500
        return {"error": {"message": "Simulated upstream 500 Internal Server Error", "type": "server_error"}}

    config.active_requests += 1
    ttft_delay = sample_ttft()

    try:
        await asyncio.sleep(ttft_delay)

        generated_text = generate_synthesized_response(req.messages)
        output_tokens = max(5, len(generated_text) // 4)

        response.headers["x-ratelimit-limit-requests"] = str(config.rpm_override or config.rpm_limit)
        response.headers["x-ratelimit-limit-tokens"] = str(config.tpm_override or config.tpm_limit)
        response.headers["x-ratelimit-remaining-requests"] = str(
            max(0, (config.rpm_override or config.rpm_limit) - len(config.request_window))
        )
        response.headers["x-ratelimit-remaining-tokens"] = str(
            max(0, (config.tpm_override or config.tpm_limit) - sum(cnt for (_, cnt) in config.token_window))
        )

        if not req.stream:
            gen_delay = output_tokens * config.inter_token_delay_s
            await asyncio.sleep(gen_delay)
            return {
                "id": f"chatcmpl-standin-{int(time.time() * 1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": req.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": generated_text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": estimated_prompt_tokens,
                    "completion_tokens": output_tokens,
                    "total_tokens": estimated_prompt_tokens + output_tokens,
                },
            }

        async def event_generator() -> AsyncGenerator[str, None]:
            try:
                words = generated_text.split(" ")
                for i, word in enumerate(words):
                    chunk_text = word + (" " if i < len(words) - 1 else "")
                    is_last = i == len(words) - 1
                    chunk_payload: Dict[str, Any] = {
                        "id": f"chatcmpl-standin-{int(time.time() * 1000)}",
                        "object": "chat.completion.chunk",
                        "model": req.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": chunk_text},
                                "finish_reason": "stop" if is_last else None,
                            }
                        ],
                    }
                    if is_last:
                        chunk_payload["x_groq"] = {
                            "usage": {
                                "prompt_tokens": estimated_prompt_tokens,
                                "completion_tokens": output_tokens,
                                "total_tokens": estimated_prompt_tokens + output_tokens,
                            }
                        }
                    yield f"data: {json.dumps(chunk_payload)}\n\n"
                    await asyncio.sleep(config.inter_token_delay_s)
                yield "data: [DONE]\n\n"
            finally:
                config.active_requests = max(0, config.active_requests - 1)

        # Active request counter decremented when stream finishes
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    except Exception:
        config.active_requests = max(0, config.active_requests - 1)
        raise
    finally:
        if not req.stream:
            config.active_requests = max(0, config.active_requests - 1)


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "active_requests": config.active_requests,
        "daily_tokens_used": config.cumulative_daily_tokens,
        "daily_tokens_limit": config.daily_token_limit,
    }


@app.post("/admin/reset")
def reset_counters():
    config.request_window.clear()
    config.token_window.clear()
    config.cumulative_daily_tokens = 0
    config.active_requests = 0
    config.force_daily_exhaustion = False
    config.rpm_override = None
    config.tpm_override = None
    config.fault_probability = 0.0
    return {"status": "reset_successful"}


@app.post("/admin/configure")
def configure(
    rpm_override: Optional[int] = None,
    tpm_override: Optional[int] = None,
    fault_probability: Optional[float] = None,
    force_daily_exhaustion: Optional[bool] = None,
    capacity_limit: Optional[int] = None,
    ttft_mu: Optional[float] = None,
    ttft_sigma: Optional[float] = None,
):
    if rpm_override is not None:
        config.rpm_override = rpm_override if rpm_override > 0 else None
    if tpm_override is not None:
        config.tpm_override = tpm_override if tpm_override > 0 else None
    if fault_probability is not None:
        config.fault_probability = fault_probability
    if force_daily_exhaustion is not None:
        config.force_daily_exhaustion = force_daily_exhaustion
    if capacity_limit is not None:
        config.capacity_limit = capacity_limit
    if ttft_mu is not None:
        config.ttft_mu = ttft_mu
    if ttft_sigma is not None:
        config.ttft_sigma = ttft_sigma
    return {
        "rpm_override": config.rpm_override,
        "tpm_override": config.tpm_override,
        "fault_probability": config.fault_probability,
        "force_daily_exhaustion": config.force_daily_exhaustion,
        "capacity_limit": config.capacity_limit,
    }
