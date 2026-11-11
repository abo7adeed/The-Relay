"""Pipeline configuration, empirical parameter hypotheses, and system settings."""

from pydantic_settings import BaseSettings
from typing import Optional


class PipelineConfig(BaseSettings):
    """Configurable system parameters and hypotheses (Pydantic BaseSettings)."""

    # Model and Provider Settings
    groq_api_key: Optional[str] = None
    default_model: str = "llama-3.1-8b-instant"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    standin_base_url: str = "http://127.0.0.1:8000/v1"

    # Gateway and Queuing Hypotheses
    queue_max_size: int = 100
    client_ttl_s: float = 10.0            # Initial hypothesis: 10s client abandonment deadline
    concurrency_limit: int = 5            # Upstream in-flight concurrency boundary

    # Retry and Backoff Hypotheses
    retry_max_attempts: int = 2
    retry_budget_pct: float = 15.0        # Initial hypothesis: max 15% system retries
    retry_backoff_base_s: float = 1.0
    retry_backoff_max_s: float = 8.0

    # Dynamic Degradation Hypotheses
    quota_degrade_threshold_pct: float = 85.0  # Shed k from 4 -> 2 when > 85% TPM used

    # Non-Negotiable Safety Invariant
    enforce_verification_always: bool = True   # MUST REMAIN TRUE AT ALL TIMES

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

