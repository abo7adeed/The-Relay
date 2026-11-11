"""Minimal live probing script for Groq Cloud free tier verification."""

import os
import sys
sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("src"))
import json
import asyncio
import logging
from relay.config import PipelineConfig
from relay.models.groq_provider import GroqProviderError, DEFAULT_GROQ_MODEL
from relay.models.respan_gateway import build_gateway_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("probe_groq")


async def run_probe(output_file: str = "data/observed_limits.json") -> dict:
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    cfg = PipelineConfig()
    api_key = (os.getenv("RESPAN_API_KEY") or cfg.respan_api_key or "").strip()

    if not api_key:
        logger.warning("RESPAN_API_KEY environment variable is NOT set.")
        result = {
            "status": "NOT_VERIFIED_MISSING_CREDENTIALS",
            "model_configured": DEFAULT_GROQ_MODEL,
            "model_returned": None,
            "probe_successful": False,
            "error_category": "MISSING_CREDENTIALS",
            "error_message": "RESPAN_API_KEY environment variable is not set. Respan gateway verification blocked.",
            "documented_limits": {
                "rpm": 30,
                "tpm": 6000,
                "rpd": 14400,
                "tpd": 500000,
            },
            "observed_limits": {
                "rpm": None,
                "tpm": None,
                "rpd": None,
                "tpd": None,
                "recovery_window_seconds": None,
            },
            "latency_ms": None,
            "tokens_in": None,
            "tokens_out": None,
            "raw_headers": {},
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print("\n" + "=" * 60)
        print("GROQ PROBE RESULT: NOT VERIFIED")
        print("Blocker: RESPAN_API_KEY is not set.")
        print(f"Recorded status to {output_file}")
        print("=" * 60 + "\n")
        return result

    logger.info("RESPAN_API_KEY found. Executing minimal live probe on %s via the Respan gateway...", DEFAULT_GROQ_MODEL)
    provider = build_gateway_provider(model_id=DEFAULT_GROQ_MODEL, api_key=api_key)

    try:
        resp = await provider.generate(
            messages=[
                {"role": "system", "content": "You are a benchmark probe."},
                {"role": "user", "content": "Respond with the word 'READY' and nothing else."},
            ],
            temperature=0.0,
            max_tokens=10,
        )

        headers = resp.raw_headers
        rpd_observed = int(headers["x-ratelimit-limit-requests"]) if "x-ratelimit-limit-requests" in headers else None
        tpm_observed = int(headers["x-ratelimit-limit-tokens"]) if "x-ratelimit-limit-tokens" in headers else None

        result = {
            "status": "VERIFIED_ONLINE",
            "model_configured": DEFAULT_GROQ_MODEL,
            "model_returned": resp.model_id,
            "probe_successful": True,
            "error_category": None,
            "error_message": None,
            "documented_limits": {
                "note": "Account limits inspected via live /openai/v1/models and rate-limit headers; llama-3.1-8b-instant returned 404 model_not_found on this account.",
                "tpm": 8000,
                "rpd": 1000,
            },
            "observed_limits": {
                "rpm": None,
                "tpm": tpm_observed,
                "rpd": rpd_observed,
                "tpd": None,
                "reset_requests": headers.get("x-ratelimit-reset-requests"),
                "reset_tokens": headers.get("x-ratelimit-reset-tokens"),
            },
            "latency_ms": round(resp.total_ms, 2),
            "tokens_in": resp.tokens_in,
            "tokens_out": resp.tokens_out,
            "raw_headers": headers,
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)

        print("\n" + "=" * 60)
        print("GROQ PROBE RESULT: VERIFIED ONLINE")
        print(f"Model: {resp.model_id}")
        print(f"Latency: {resp.total_ms:.2f} ms")
        print(f"Tokens in/out: {resp.tokens_in} / {resp.tokens_out}")
        print(f"Observed RPD: {rpd_observed}, TPM: {tpm_observed}")
        print(f"Recorded status to {output_file}")
        print("=" * 60 + "\n")
        return result

    except GroqProviderError as e:
        logger.error("Groq probe failed: %s", e)
        result = {
            "status": "PROBE_FAILED",
            "model_configured": DEFAULT_GROQ_MODEL,
            "model_returned": None,
            "probe_successful": False,
            "error_category": f"HTTP_{e.status_code}" if e.status_code else "PROVIDER_ERROR",
            "error_message": str(e),
            "documented_limits": {
                "rpm": 30,
                "tpm": 6000,
                "rpd": 14400,
                "tpd": 500000,
            },
            "observed_limits": {
                "rpm": None,
                "tpm": None,
                "rpd": None,
                "tpd": None,
                "recovery_window_seconds": e.retry_after,
            },
            "latency_ms": None,
            "tokens_in": None,
            "tokens_out": None,
            "raw_headers": e.headers,
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return result


if __name__ == "__main__":
    asyncio.run(run_probe())
