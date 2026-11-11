"""Regression tests for configuration validation and safety invariants."""

import pytest
from pydantic import ValidationError

from relay.config import PipelineConfig


def _config(**overrides):
    return PipelineConfig(_env_file=None, **overrides)


def test_default_config_is_valid():
    cfg = _config()
    assert cfg.enforce_verification_always is True
    assert cfg.queue_max_size >= 1
    assert cfg.concurrency_limit >= 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"queue_max_size": 0},
        {"concurrency_limit": 0},
        {"retry_max_attempts": 0},
        {"client_ttl_s": 0},
        {"retry_backoff_base_s": 0},
        {"retry_backoff_max_s": 0},
        {"retry_budget_pct": -1},
        {"retry_budget_pct": 101},
        {"quota_degrade_threshold_pct": 101},
        {"retry_backoff_base_s": 5, "retry_backoff_max_s": 4},
    ],
)
def test_invalid_configuration_is_rejected(overrides):
    with pytest.raises(ValidationError):
        _config(**overrides)


def test_verification_invariant_cannot_be_disabled():
    with pytest.raises(ValidationError, match="cannot be disabled"):
        _config(enforce_verification_always=False)
