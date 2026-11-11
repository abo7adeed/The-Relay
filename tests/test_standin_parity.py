"""Tests verifying StandInService parity with Groq API schema, SSE streaming, 429 headers, and fault injection."""

import pytest
import httpx
from relay.models.standin_service import StandInConfig, app, config as standin_cfg
from relay.models.groq_provider import GroqProvider, GroqProviderError


@pytest.fixture(autouse=True)
def _restore_standin_config():
    """Prevent mutable global simulator state from leaking between tests."""
    standin_cfg.reset()
    yield
    standin_cfg.reset()


def test_standin_config_instances_have_independent_windows():
    first = StandInConfig()
    second = StandInConfig()
    first.request_window.append(1.0)
    first.token_window.append((1.0, 10))
    assert second.request_window == []
    assert second.token_window == []


def test_standin_config_reset_restores_defaults_in_place():
    original_id = id(standin_cfg)
    standin_cfg.ttft_mu = 1.0
    standin_cfg.capacity_limit = 1
    standin_cfg.request_window.append(1.0)
    standin_cfg.force_daily_exhaustion = True

    standin_cfg.reset()

    assert id(standin_cfg) == original_id
    assert standin_cfg.ttft_mu == 5.45
    assert standin_cfg.capacity_limit == 5
    assert standin_cfg.request_window == []
    assert standin_cfg.force_daily_exhaustion is False


@pytest.fixture
def standin_client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://standin.local")


@pytest.mark.asyncio
async def test_standin_non_streaming_and_sse_streaming_parity(standin_client):
    async with standin_client as client:
        await client.post("/admin/reset")
        # Speed up test latency
        standin_cfg.ttft_mu = 3.9  # ~50ms
        standin_cfg.ttft_sigma = 0.05
        standin_cfg.inter_token_delay_s = 0.001

        provider = GroqProvider(api_key="standin-key", base_url="http://standin.local/v1", client=client)

        msgs = [
            {"role": "system", "content": "Research assistant"},
            {
                "role": "user",
                "content": (
                    "--- EVIDENCE PASSAGE 1 [Attention Is All You Need, 5.3 Optimizer, Page 7] ---\n"
                    "We used warmup _ steps = 4000 .\n"
                    "USER QUESTION: How many warmup steps were used?\nPlease formulate answer."
                ),
            },
        ]

        # 1. Non-streaming completion
        resp = await provider.generate(msgs)
        assert "4000" in resp.content
        assert "Page 7" in resp.content
        assert resp.tokens_in > 0
        assert resp.tokens_out > 0
        assert "x-ratelimit-limit-requests" in resp.raw_headers

        # 2. Streaming SSE completion
        chunks = []
        tok_in = 0
        tok_out = 0
        async for sc in provider.generate_stream(msgs):
            if sc.delta_content:
                chunks.append(sc.delta_content)
            if sc.tokens_in is not None:
                tok_in = sc.tokens_in
            if sc.tokens_out is not None:
                tok_out = sc.tokens_out

        joined = "".join(chunks)
        assert "4000" in joined
        assert "Page 7" in joined
        assert tok_in > 0 and tok_out > 0


@pytest.mark.asyncio
async def test_standin_admin_reset_restores_simulator_defaults(standin_client):
    async with standin_client as client:
        await client.post(
            "/admin/configure",
            params={"ttft_mu": 1.0, "capacity_limit": 1, "fault_probability": 0.5},
        )
        assert standin_cfg.ttft_mu == 1.0
        assert standin_cfg.capacity_limit == 1

        await client.post("/admin/reset")

        assert standin_cfg.ttft_mu == 5.45
        assert standin_cfg.capacity_limit == 5
        assert standin_cfg.fault_probability == 0.0


@pytest.mark.asyncio
async def test_standin_429_rpm_limit_and_daily_exhaustion(standin_client):
    async with standin_client as client:
        await client.post("/admin/reset")
        standin_cfg.ttft_mu = 3.9
        standin_cfg.inter_token_delay_s = 0.001

        provider = GroqProvider(api_key="standin-key", base_url="http://standin.local/v1", client=client)
        msgs = [{"role": "user", "content": "Hello"}]

        # 1. Configure tiny RPM limit = 1
        await client.post("/admin/configure", params={"rpm_override": 1})
        await provider.generate(msgs)
        with pytest.raises(GroqProviderError) as exc_info:
            await provider.generate(msgs)
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after is not None and exc_info.value.retry_after <= 60
        assert exc_info.value.is_daily_quota_exhausted is False

        # 2. Configure daily quota exhaustion
        await client.post("/admin/reset")
        await client.post("/admin/configure", params={"force_daily_exhaustion": True})
        with pytest.raises(GroqProviderError) as daily_exc:
            await provider.generate(msgs)
        assert daily_exc.value.status_code == 429
        assert daily_exc.value.retry_after == 28800
        assert daily_exc.value.is_daily_quota_exhausted is True

