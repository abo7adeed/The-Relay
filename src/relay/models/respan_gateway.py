"""Respan gateway wiring for the Relay's chat-completion client (gateway logging only).

Two things happen here:

* Requests are routed to the Respan gateway so every completion is logged.
* The project's existing Groq key is sent as a per-request BYOK credential, so
  calls are billed to Groq directly and never need Respan gateway credits.

This is a gateway-only integration: no Respan instrumentation packages are installed.
"""

import os
from typing import Optional

from relay.config import PipelineConfig
from relay.models.groq_provider import GroqProvider

RESPAN_GATEWAY_BASE_URL = "https://gateway.respan.ai/v1"
RESPAN_PROVIDER_ID = "groq"


def respan_api_key(cfg: Optional[PipelineConfig] = None) -> str:
    """Return the Respan API key from the environment or the project's .env."""
    cfg = cfg or PipelineConfig()
    return (os.getenv("RESPAN_API_KEY") or cfg.respan_api_key or "").strip()


def provider_key(cfg: Optional[PipelineConfig] = None) -> str:
    """Return the project's own Groq key, sent through the gateway as BYOK."""
    cfg = cfg or PipelineConfig()
    return (os.getenv("GROQ_API_KEY") or cfg.groq_api_key or "").strip()


def build_gateway_provider(
    model_id: str,
    api_key: Optional[str] = None,
    groq_api_key: Optional[str] = None,
    timeout_seconds: float = 30.0,
    client=None,
) -> GroqProvider:
    """Build a chat-completions client pointed at the Respan gateway."""
    cfg = PipelineConfig()
    key = (api_key or respan_api_key(cfg)).strip()
    own_key = (groq_api_key or provider_key(cfg)).strip()
    extra_body = (
        {"customer_credentials": {RESPAN_PROVIDER_ID: {"api_key": own_key}}}
        if own_key
        else None
    )
    return GroqProvider(
        api_key=key,
        model_id=model_id,
        base_url=RESPAN_GATEWAY_BASE_URL,
        timeout_seconds=timeout_seconds,
        client=client,
        extra_body=extra_body,
    )
