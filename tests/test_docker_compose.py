from __future__ import annotations

from pathlib import Path


def test_docker_compose_defines_treasury_executor_service() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "  treasury-executor:" in compose
    assert "python -m scripts.treasury_executor" in compose
    assert "MERGEWORK_TREASURY_EXECUTOR_ENABLED" not in compose
    assert "      - .env" in compose
    assert "      - /srv/mergework/data:/srv/mergework/data" in compose
    assert "restart: unless-stopped" in compose
