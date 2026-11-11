"""Abstract LLM Provider interface, data transfer objects, and telemetry structures."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncGenerator, Dict, List, Optional, Any


@dataclass
class ProviderLimits:
    """Explicitly partitioned limits: documented, observed, simulated."""
    model_id: str
    rpm_documented: int = 30
    tpm_documented: int = 6_000
    rpd_documented: int = 14_400
    tpd_documented: int = 500_000

    rpm_observed: Optional[int] = None
    tpm_observed: Optional[int] = None
    rpd_observed: Optional[int] = None
    tpd_observed: Optional[int] = None
    observed_concurrency: Optional[int] = None

    rpm_simulated: Optional[int] = None
    tpm_simulated: Optional[int] = None


@dataclass
class StreamChunk:
    delta_content: str
    finish_reason: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None


@dataclass
class ModelResponse:
    content: str
    tokens_in: int
    tokens_out: int
    model_id: str
    first_token_ms: float
    total_ms: float
    raw_headers: Dict[str, str] = field(default_factory=dict)
    finish_reason: str = "stop"


class LLMProvider(ABC):
    """Abstract interface for all model backends (Real Groq Cloud or Stand-in)."""

    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> ModelResponse:
        """Execute a non-streaming completion."""
        pass

    @abstractmethod
    async def generate_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Execute an asynchronous streaming completion."""
        pass

    @abstractmethod
    def get_limits(self) -> ProviderLimits:
        """Return the current documented, observed, and simulated limits."""
        pass

