"""Unit tests for model provider abstractions and Groq client telemetry (Milestone M3)."""

import pytest
import httpx
from relay.models.base import ProviderLimits, ModelResponse, StreamChunk
from relay.models.groq_provider import GroqProvider, GroqProviderError


def test_provider_limits_defaults():
    limits = ProviderLimits(model_id="llama-3.1-8b-instant")
    assert limits.model_id == "llama-3.1-8b-instant"
    assert limits.rpm_documented == 30
    assert limits.tpm_documented == 6000
    assert limits.rpd_documented == 14400
    assert limits.tpd_documented == 500000
    assert limits.rpm_observed is None
    assert limits.tpm_observed is None


def test_groq_provider_missing_key_raises():
    provider = GroqProvider(api_key="")
    with pytest.raises(GroqProviderError) as exc_info:
        provider._get_headers()
    assert exc_info.value.status_code == 401
    assert "GROQ_API_KEY is not set" in str(exc_info.value)


def test_groq_provider_parses_rate_limit_headers():
    provider = GroqProvider(api_key="gsk_dummy_test_key_12345")
    headers = httpx.Headers({
        "x-ratelimit-limit-requests": "30",
        "x-ratelimit-limit-tokens": "6000",
        "x-ratelimit-remaining-requests": "29",
        "x-ratelimit-remaining-tokens": "5850",
        "x-ratelimit-reset-requests": "2.0s",
        "retry-after": "5",
    })
    telemetry = provider._parse_rate_limit_headers(headers)
    assert telemetry["x-ratelimit-limit-requests"] == "30"
    assert telemetry["x-ratelimit-limit-tokens"] == "6000"
    assert telemetry["retry-after"] == "5"

    limits = provider.get_limits()
    assert limits.rpm_observed == 30
    assert limits.tpm_observed == 6000


@pytest.mark.asyncio
async def test_groq_provider_mock_response():
    # Test generation flow with mock transport
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer gsk_dummy_test_key_12345"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "model": "llama-3.1-8b-instant",
                "choices": [
                    {
                        "message": {"content": "Verified mock response text with citation [Paper, Sec 1]"},
                        "finish_reason": "stop"
                    }
                ],
                "usage": {"prompt_tokens": 150, "completion_tokens": 25}
            },
            headers={"x-ratelimit-limit-requests": "30", "x-ratelimit-limit-tokens": "6000"}
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GroqProvider(api_key="gsk_dummy_test_key_12345", client=client)
        resp = await provider.generate([{"role": "user", "content": "hello"}])
        assert resp.content == "Verified mock response text with citation [Paper, Sec 1]"
        assert resp.tokens_in == 150
        assert resp.tokens_out == 25
        assert resp.total_ms > 0
        assert resp.model_id == "llama-3.1-8b-instant"


class _FakeResponse:
    status_code = 200
    text = ""
    headers = httpx.Headers(
        {"x-ratelimit-limit-requests": "30", "x-ratelimit-limit-tokens": "6000"}
    )

    def json(self):
        return {
            "id": "chatcmpl-fake",
            "model": "llama-3.1-8b-instant",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }


class _CapturingClient:
    def __init__(self):
        self.timeout = "unset"

    async def post(self, url, headers, json, timeout=None):
        self.timeout = timeout
        return _FakeResponse()


@pytest.mark.asyncio
async def test_injected_client_receives_configured_timeout():
    client = _CapturingClient()
    provider = GroqProvider(api_key="gsk_dummy", client=client, timeout_seconds=2.5)
    await provider.generate([{"role": "user", "content": "hello"}])
    assert client.timeout == 2.5


@pytest.mark.asyncio
async def test_groq_provider_maps_non_network_transport_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("malformed HTTP response")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GroqProvider(api_key="gsk_dummy", client=client)
        with pytest.raises(GroqProviderError) as exc_info:
            await provider.generate([{"role": "user", "content": "hello"}])
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_groq_stream_truncation_without_terminal_marker_raises():
    body = (
        'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GroqProvider(api_key="gsk_dummy", client=client)
        with pytest.raises(GroqProviderError, match="ended before a terminal") as exc_info:
            async for _ in provider.generate_stream([{"role": "user", "content": "hello"}]):
                pass
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_groq_stream_finish_reason_is_accepted_as_terminal():
    body = (
        'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\n'
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GroqProvider(api_key="gsk_dummy", client=client)
        chunks = [
            sc.delta_content
            async for sc in provider.generate_stream([{"role": "user", "content": "hello"}])
        ]
    assert "".join(chunks) == "hi"
