from __future__ import annotations

import pytest

from scripts.treasury_executor import executor_config_from_env


def test_executor_config_defaults_to_disabled() -> None:
    config = executor_config_from_env({})

    assert config.enabled is False
    assert config.interval_seconds == 300
    assert config.batch_limit == 25


def test_executor_config_reads_explicit_production_settings() -> None:
    config = executor_config_from_env(
        {
            "MERGEWORK_TREASURY_EXECUTOR_ENABLED": "true",
            "MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS": "60",
            "MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT": "5",
        }
    )

    assert config.enabled is True
    assert config.interval_seconds == 60
    assert config.batch_limit == 5


def test_executor_config_rejects_invalid_numbers() -> None:
    with pytest.raises(ValueError, match="MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS"):
        executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS": "0"})

    with pytest.raises(ValueError, match="MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT"):
        executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT": "0"})
