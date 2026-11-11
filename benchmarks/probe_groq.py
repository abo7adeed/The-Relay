"""Minimal live probe script verifying provider credentials, models, and rate-limit headers."""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("src"))

from relay.config import PipelineConfig
from relay.models.groq_provider import DEFAULT_GROQ_MODEL, GroqProvider, GroqProviderError
from relay.models.respan_gateway import RESPAN_GATEWAY_BASE_URL, build_gateway_provider

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _parse_int(val: Optional[str]) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _classify_request_limit_window(
    limit_requests: Optional[int], reset_requests: Optional[str]
) -> Dict[str, Optional[int]]:
    """Classify x-ratelimit-limit-requests into rpm vs rpd without guessing unmeasured fields."""
    if limit_requests is None:
        return {"rpm": None, "rpd": None}
    reset = (reset_requests or "").lower()
    # Daily quotas reset across ~86400s / limit (e.g. 1000 RPD -> ~1m26.4s per request token).
    if "h" in reset or "m" in reset or limit_requests >= 500:
        return {"rpm": None, "rpd": limit_requests}
    return {"rpm": limit_requests, "rpd": None}


async def _fetch_available_models(endpoint_url: str, api_key: str) -> Optional[List[str]]:
    """Query GET /models without exposing credentials. Returns sorted model IDs or None on failure."""
    url = f"{endpoint_url.rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
    if resp.status_code != 200:
        logger.warning("GET %s returned HTTP %s", url, resp.status_code)
        return None
    payload = resp.json()
    items = payload.get("data", []) if isinstance(payload, dict) else []
    model_ids = sorted(str(item["id"]) for item in items if isinstance(item, dict) and "id" in item)
    return model_ids


async def run_probe(output_file: str = "data/observed_limits.json") -> Dict[str, Any]:
    """Run a single minimal live probe against the configured provider (Respan gateway or Direct Groq)."""
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    cfg = PipelineConfig()
    respan_key = (os.getenv("RESPAN_API_KEY") or cfg.respan_api_key or "").strip()
    groq_key = (os.getenv("GROQ_API_KEY") or cfg.groq_api_key or "").strip()
    verified_at = datetime.now(timezone.utc).isoformat()

    if not respan_key and not groq_key:
        logger.warning("Neither RESPAN_API_KEY nor GROQ_API_KEY is configured.")
        result: Dict[str, Any] = {
            "status": "NOT_VERIFIED_MISSING_CREDENTIALS",
            "provider_type": None,
            "endpoint_url": None,
            "auth_mode": None,
            "respan_configured": False,
            "verified_at_utc": verified_at,
            "model_configured": DEFAULT_GROQ_MODEL,
            "model_returned": None,
            "available_models": [],
            "probe_successful": False,
            "error_category": "missing_credentials",
            "error_message": "Neither RESPAN_API_KEY nor GROQ_API_KEY is set in the environment or .env.",
            "documented_limits": {
                "note": "No live credentials configured; no provider limits could be queried or measured.",
                "rpm": None,
                "tpm": None,
                "rpd": None,
                "tpd": None,
            },
            "observed_limits": {
                "rpm": None,
                "tpm": None,
                "rpd": None,
                "tpd": None,
                "reset_requests": None,
                "reset_tokens": None,
            },
            "unverified_fields": ["rpm", "tpm", "rpd", "tpd"],
            "raw_headers": {},
            "limitations": [
                "Live provider was not probed because no API credentials were present."
            ],
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return result

    if respan_key:
        provider_type = "respan_gateway"
        endpoint_url = RESPAN_GATEWAY_BASE_URL
        auth_mode = "respan_gateway_byok" if groq_key else "respan_gateway_managed"
        provider = build_gateway_provider(
            model_id=DEFAULT_GROQ_MODEL, api_key=respan_key, cfg=cfg
        )
        probe_key = respan_key
    else:
        provider_type = "direct_groq"
        endpoint_url = cfg.groq_base_url
        auth_mode = "direct_groq_api_key"
        provider = GroqProvider(
            api_key=groq_key, model_id=DEFAULT_GROQ_MODEL, base_url=cfg.groq_base_url
        )
        probe_key = groq_key

    logger.info(
        "Executing minimal live probe on model=%s via provider=%s (%s)...",
        DEFAULT_GROQ_MODEL,
        provider_type,
        endpoint_url,
    )

    available_models: List[str] = []
    try:
        fetched_models = await _fetch_available_models(endpoint_url, probe_key)
        if fetched_models is not None:
            available_models = fetched_models
    except Exception as exc:
        logger.warning("Model inventory query failed (%s): %s", type(exc).__name__, exc)

    try:
        resp = await provider.generate(
            messages=[{"role": "user", "content": "Reply with only the single word: READY"}],
            max_tokens=16,
            temperature=0.0,
        )
        raw_headers = dict(provider._last_headers)
        req_limit = _parse_int(raw_headers.get("x-ratelimit-limit-requests"))
        tok_limit = _parse_int(raw_headers.get("x-ratelimit-limit-tokens"))
        reset_req = raw_headers.get("x-ratelimit-reset-requests")
        reset_tok = raw_headers.get("x-ratelimit-reset-tokens")
        req_window = _classify_request_limit_window(req_limit, reset_req)

        has_default_legacy = cfg.default_model in available_models if available_models else None
        result = {
            "status": "VERIFIED_ONLINE",
            "provider_type": provider_type,
            "endpoint_url": endpoint_url,
            "auth_mode": auth_mode,
            "respan_configured": bool(respan_key),
            "verified_at_utc": verified_at,
            "model_configured": DEFAULT_GROQ_MODEL,
            "model_returned": resp.model_id,
            "available_models": available_models,
            "legacy_standin_model_id": cfg.default_model,
            "legacy_standin_model_available_on_account": has_default_legacy,
            "probe_successful": True,
            "error_category": None,
            "error_message": None,
            "limits_source": "http_response_headers",
            "documented_limits": {
                "note": (
                    f"Measured on live {provider_type} ({endpoint_url}) for {resp.model_id}. "
                    f"Headers emit x-ratelimit-limit-requests={req_limit} (reset={reset_req}) and "
                    f"x-ratelimit-limit-tokens={tok_limit} (reset={reset_tok}). "
                    "RPM and TPD are not directly returned in headers and remain null (unverified)."
                ),
                "rpm": req_window["rpm"],
                "tpm": tok_limit,
                "rpd": req_window["rpd"],
                "tpd": None,
            },
            "observed_limits": {
                "rpm": req_window["rpm"],
                "tpm": tok_limit,
                "rpd": req_window["rpd"],
                "tpd": None,
                "reset_requests": reset_req,
                "reset_tokens": reset_tok,
            },
            "latency_ms": round(resp.total_ms, 2),
            "tokens_in": resp.tokens_in,
            "tokens_out": resp.tokens_out,
            "response_preview": resp.content[:80],
            "raw_headers": raw_headers,
            "unverified_fields": [
                "rpm",
                "tpd",
                "multi_tenant_queue_saturation",
                "sustained_burst_429_backoff",
            ],
            "limitations": [
                "x-ratelimit-limit-requests on this account maps to RPD (reset_requests ~1m26.4s = 86400s/1000); per-minute RPM is not emitted in headers.",
                "Total daily token cap (TPD) is not emitted in headers and remains unverified.",
                "Stand-in simulator in results.csv simulates llama-3.1-8b-instant (30 RPM / 6000 TPM), whereas this live account serves qwen/qwen3.8-27b (8000 TPM / 1000 RPD) and does not expose llama-3.1-8b-instant.",
            ],
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        logger.info(
            "Probe succeeded: provider=%s model=%s latency=%.2fms",
            provider_type,
            resp.model_id,
            resp.total_ms,
        )
        return result

    except GroqProviderError as e:
        logger.error("Live probe failed (%s): %s", e.category, e)
        result = {
            "status": f"FAILED_{e.category.upper()}",
            "provider_type": provider_type,
            "endpoint_url": endpoint_url,
            "auth_mode": auth_mode,
            "respan_configured": bool(respan_key),
            "verified_at_utc": verified_at,
            "model_configured": DEFAULT_GROQ_MODEL,
            "model_returned": None,
            "available_models": available_models,
            "probe_successful": False,
            "error_category": e.category,
            "error_message": str(e),
            "documented_limits": {
                "note": f"Probe failed with {e.category} ({e.status_code}). Limits unverified.",
                "rpm": None,
                "tpm": None,
                "rpd": None,
                "tpd": None,
            },
            "observed_limits": {
                "rpm": None,
                "tpm": None,
                "rpd": None,
                "tpd": None,
                "reset_requests": None,
                "reset_tokens": None,
            },
            "unverified_fields": ["rpm", "tpm", "rpd", "tpd"],
            "raw_headers": dict(e.headers or {}),
            "limitations": [f"Live probe failed ({e.category})."],
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return result


if __name__ == "__main__":
    out = asyncio.run(run_probe())
    print(json.dumps(out, indent=2))
