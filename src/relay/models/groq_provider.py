"""Groq Cloud API provider implementation with header telemetry, SSE streaming, and rate-limit parsing."""

import os
import time
import json
import logging
from typing import AsyncGenerator, Dict, List, Optional, Any
import httpx

from relay.models.base import LLMProvider, ModelResponse, StreamChunk, ProviderLimits

logger = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"


class GroqProviderError(Exception):
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        retry_after: Optional[float] = None,
        headers: Optional[Dict[str, str]] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.headers = headers or {}
        self.error_code = error_code

    @property
    def is_daily_quota_exhausted(self) -> bool:
        msg_l = str(self).lower()
        return (
            self.error_code == "daily_quota_exhausted"
            or (self.retry_after is not None and self.retry_after > 3600)
            or "daily token limit" in msg_l
            or "per day" in msg_l
        )


class GroqProvider(LLMProvider):
    """Real Groq / OpenAI-compatible REST & SSE provider (works with Groq Cloud and Stand-in Service)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_id: str = DEFAULT_GROQ_MODEL,
        base_url: str = GROQ_BASE_URL,
        timeout_seconds: float = 30.0,
        client: Optional[httpx.AsyncClient] = None,
        extra_body: Optional[Dict[str, Any]] = None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("GROQ_API_KEY", "")
        self.model_id = model_id
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client
        self.extra_body: Dict[str, Any] = dict(extra_body or {})
        self.limits = ProviderLimits(model_id=self.model_id)
        self._last_headers: Dict[str, str] = {}

    def _parse_rate_limit_headers(self, headers: httpx.Headers) -> Dict[str, str]:
        telemetry: Dict[str, str] = {}
        for key, val in headers.items():
            k_lower = key.lower()
            if k_lower.startswith("x-ratelimit-") or k_lower == "retry-after":
                telemetry[k_lower] = val

        if "x-ratelimit-limit-requests" in telemetry:
            try:
                self.limits.rpm_observed = int(telemetry["x-ratelimit-limit-requests"])
            except ValueError:
                pass
        if "x-ratelimit-limit-tokens" in telemetry:
            try:
                self.limits.tpm_observed = int(telemetry["x-ratelimit-limit-tokens"])
            except ValueError:
                pass

        self._last_headers = telemetry
        return telemetry

    def _get_headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise GroqProviderError("GROQ_API_KEY is not set. Cannot authenticate with Groq Cloud.", status_code=401)
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "TheRelayBenchmark/1.0",
        }

    def _raise_for_error_response(self, status_code: int, body_text: str, headers: Dict[str, str]) -> None:
        if status_code == 429:
            retry_after = None
            if "retry-after" in headers:
                try:
                    retry_after = float(headers["retry-after"])
                except ValueError:
                    pass
            error_code = None
            try:
                err_json = json.loads(body_text)
                error_code = err_json.get("error", {}).get("code")
            except Exception:
                pass
            raise GroqProviderError(
                f"Rate limit exceeded (429): {body_text}",
                status_code=429,
                retry_after=retry_after,
                headers=headers,
                error_code=error_code,
            )

        raise GroqProviderError(
            f"Provider returned error status {status_code}: {body_text}",
            status_code=status_code,
            headers=headers,
        )

    async def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> ModelResponse:
        t0 = time.perf_counter()
        headers = self._get_headers()
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            **self.extra_body,
        }

        if self.client:
            try:
                resp = await self.client.post(
                    url, headers=headers, json=payload, timeout=self.timeout_seconds
                )
            except httpx.TimeoutException as e:
                raise GroqProviderError(f"API call timed out after {self.timeout_seconds}s", status_code=504) from e
            except httpx.NetworkError as e:
                raise GroqProviderError(f"API network connection failure: {e}", status_code=503) from e
            except httpx.HTTPError as e:
                raise GroqProviderError(f"API transport failure: {e}", status_code=503) from e
        else:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                try:
                    resp = await client.post(url, headers=headers, json=payload)
                except httpx.TimeoutException as e:
                    raise GroqProviderError(f"API call timed out after {self.timeout_seconds}s", status_code=504) from e
                except httpx.NetworkError as e:
                    raise GroqProviderError(f"API network connection failure: {e}", status_code=503) from e
                except httpx.HTTPError as e:
                    raise GroqProviderError(f"API transport failure: {e}", status_code=503) from e

        t_total = (time.perf_counter() - t0) * 1000
        telemetry_headers = self._parse_rate_limit_headers(resp.headers)

        if resp.status_code != 200:
            self._raise_for_error_response(resp.status_code, resp.text, telemetry_headers)

        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise GroqProviderError(f"Malformed provider JSON response: {e}", status_code=502, headers=telemetry_headers) from e

        finish_reason = choice.get("finish_reason", "stop")
        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)

        return ModelResponse(
            content=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model_id=data.get("model", self.model_id),
            first_token_ms=t_total,
            total_ms=t_total,
            raw_headers=telemetry_headers,
            finish_reason=finish_reason,
        )

    async def generate_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> AsyncGenerator[StreamChunk, None]:
        headers = self._get_headers()
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            **self.extra_body,
        }

        async def _consume(client: httpx.AsyncClient) -> AsyncGenerator[StreamChunk, None]:
            saw_terminal = False
            try:
                async with client.stream(
                    "POST", url, headers=headers, json=payload, timeout=self.timeout_seconds
                ) as resp:
                    telemetry_headers = self._parse_rate_limit_headers(resp.headers)
                    if resp.status_code != 200:
                        body = await resp.aread()
                        self._raise_for_error_response(
                            resp.status_code,
                            body.decode("utf-8", errors="ignore"),
                            telemetry_headers,
                        )

                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            saw_terminal = True
                            break
                        try:
                            chunk_json = json.loads(data_str)
                            choice = chunk_json["choices"][0]
                            delta = choice.get("delta", {})
                            delta_text = delta.get("content", "")
                            finish_reason = choice.get("finish_reason")
                            if finish_reason is not None:
                                saw_terminal = True
                            usage = (
                                chunk_json.get("x_groq", {}).get("usage")
                                or chunk_json.get("usage")
                                or {}
                            )
                            t_in = usage.get("prompt_tokens")
                            t_out = usage.get("completion_tokens")
                            if delta_text or finish_reason or t_in is not None:
                                yield StreamChunk(
                                    delta_content=delta_text,
                                    finish_reason=finish_reason,
                                    tokens_in=t_in,
                                    tokens_out=t_out,
                                )
                        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                            continue
            except httpx.TimeoutException as e:
                raise GroqProviderError(f"Streaming call timed out after {self.timeout_seconds}s", status_code=504) from e
            except httpx.NetworkError as e:
                raise GroqProviderError(f"Streaming network failure: {e}", status_code=503) from e
            except httpx.HTTPError as e:
                raise GroqProviderError(f"Streaming transport failure: {e}", status_code=503) from e

            if not saw_terminal:
                raise GroqProviderError(
                    "Streaming response ended before a terminal [DONE] marker or finish_reason; "
                    "refusing to treat a truncated stream as complete.",
                    status_code=502,
                )

        if self.client:
            async for sc in _consume(self.client):
                yield sc
        else:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                async for sc in _consume(client):
                    yield sc

    def get_limits(self) -> ProviderLimits:
        return self.limits
