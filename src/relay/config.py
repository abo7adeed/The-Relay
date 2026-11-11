"""Pipeline configuration, empirical parameter hypotheses, and validated system settings."""

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class PipelineConfig(BaseSettings):
    """Configurable system parameters and hypotheses (Pydantic BaseSettings)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Model and Provider Settings
    groq_api_key: Optional[str] = None
    default_model: str = "llama-3.1-8b-instant"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    standin_base_url: str = "http://127.0.0.1:8000/v1"

    # Respan gateway credentials (chat completions are routed through the gateway)
    respan_api_key: Optional[str] = None
    respan_base_url: str = "https://gateway.respan.ai/v1"

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

    @field_validator("queue_max_size", "concurrency_limit", "retry_max_attempts")
    @classmethod
    def _positive_int(cls, value: int, info) -> int:
        if value < 1:
            raise ValueError(f"{info.field_name} must be >= 1")
        return value

    @field_validator("client_ttl_s", "retry_backoff_base_s", "retry_backoff_max_s")
    @classmethod
    def _positive_float(cls, value: float, info) -> float:
        if value <= 0:
            raise ValueError(f"{info.field_name} must be > 0")
        return value

    @field_validator("retry_budget_pct", "quota_degrade_threshold_pct")
    @classmethod
    def _percentage(cls, value: float, info) -> float:
        if not 0.0 <= value <= 100.0:
            raise ValueError(f"{info.field_name} must be between 0 and 100")
        return value

    @field_validator("enforce_verification_always")
    @classmethod
    def _verification_cannot_be_disabled(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError(
                "enforce_verification_always is a non-negotiable safety invariant "
                "and cannot be disabled."
            )
        return value

    @model_validator(mode="after")
    def _backoff_bounds(self) -> "PipelineConfig":
        if self.retry_backoff_max_s < self.retry_backoff_base_s:
            raise ValueError("retry_backoff_max_s must be >= retry_backoff_base_s")
        return self
