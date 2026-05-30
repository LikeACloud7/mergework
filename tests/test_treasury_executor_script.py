from __future__ import annotations

import pytest

import scripts.treasury_executor as executor_script
from scripts.treasury_executor import ExecutorConfig, executor_config_from_env


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


def test_executor_config_accepts_documented_enable_flag() -> None:
    config = executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_ENABLED": "1"})

    assert config.enabled is True


def test_executor_config_accepts_bounds() -> None:
    config = executor_config_from_env(
        {
            "MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS": "15",
            "MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT": "200",
        }
    )

    assert config.interval_seconds == 15
    assert config.batch_limit == 200


def test_executor_config_rejects_invalid_numbers() -> None:
    with pytest.raises(ValueError, match="MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS"):
        executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS": "0"})

    with pytest.raises(ValueError, match="MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT"):
        executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT": "0"})

    with pytest.raises(ValueError, match="MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT"):
        executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT": "201"})

    with pytest.raises(ValueError, match="MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS"):
        executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS": "notanumber"})


def test_executor_once_returns_failure_when_pass_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_once(config: ExecutorConfig) -> dict[str, object]:
        raise RuntimeError("executor failed")

    monkeypatch.setattr(
        executor_script,
        "executor_config_from_env",
        lambda: ExecutorConfig(enabled=True, interval_seconds=15, batch_limit=1),
    )
    monkeypatch.setattr(executor_script, "run_once", fake_run_once)

    assert executor_script.main(["--once"]) == 1
