from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.config import get_settings
from app.treasury_executor import execute_due_treasury_proposals

DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_BATCH_LIMIT = 25
MIN_INTERVAL_SECONDS = 15
MAX_BATCH_LIMIT = 200
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"", "0", "false", "no", "off"}


@dataclass(frozen=True)
class ExecutorConfig:
    enabled: bool
    interval_seconds: int
    batch_limit: int


def _enabled_from_env(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError("MERGEWORK_TREASURY_EXECUTOR_ENABLED must be true or false")


def _positive_int_from_env(
    env: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int | None = None
) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return value


def executor_config_from_env(env: Mapping[str, str] | None = None) -> ExecutorConfig:
    source = os.environ if env is None else env
    return ExecutorConfig(
        enabled=_enabled_from_env(source.get("MERGEWORK_TREASURY_EXECUTOR_ENABLED")),
        interval_seconds=_positive_int_from_env(
            source,
            "MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS",
            DEFAULT_INTERVAL_SECONDS,
            minimum=MIN_INTERVAL_SECONDS,
        ),
        batch_limit=_positive_int_from_env(
            source,
            "MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT",
            DEFAULT_BATCH_LIMIT,
            minimum=1,
            maximum=MAX_BATCH_LIMIT,
        ),
    )


def run_once(config: ExecutorConfig) -> dict[str, object]:
    settings = get_settings()
    return execute_due_treasury_proposals(
        settings.database_url,
        github_issue_token=settings.github_issue_token,
        public_base_url=settings.public_base_url,
        executed_by="treasury-executor",
        limit=config.batch_limit,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute due MergeWork treasury proposals.")
    parser.add_argument("--once", action="store_true", help="Run one enabled pass and exit.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = executor_config_from_env()
    if not config.enabled:
        logging.info("treasury executor disabled by MERGEWORK_TREASURY_EXECUTOR_ENABLED")
        if args.once:
            return 0
        while True:
            time.sleep(config.interval_seconds)
    while True:
        try:
            report = run_once(config)
            logging.info("treasury executor report %s", json.dumps(report, sort_keys=True))
        except Exception:
            logging.exception("treasury executor pass failed")
        if args.once:
            return 0
        time.sleep(config.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
