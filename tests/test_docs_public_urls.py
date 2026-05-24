from __future__ import annotations

from pathlib import Path

from scripts.docs_smoke import REQUIRED


def test_readme_lists_live_ltclab_urls() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "https://ltclab.site" in readme
    assert "https://mrwk.ltclab.site" in readme
    assert "https://api.mrwk.ltclab.site" in readme
    assert "https://mcp.mrwk.ltclab.site" in readme
    assert "https://github.com/ramimbo/mergework/discussions/16" in readme
    assert "docs/paid-bounties.md" in readme
    assert "docs/api-examples.md" in readme


def test_api_examples_document_internal_bounty_ids() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "https://api.mrwk.ltclab.site" in examples
    assert "https://mcp.mrwk.ltclab.site" in examples
    assert "/api/v1/bounties/<bounty_id>" in examples
    assert '"name":"get_bounty"' in examples
    assert '"arguments":{"id":11}' in examples
    assert '"id":4' in examples
    assert "not the GitHub issue" in examples
    assert "public_key_hex" in examples


def test_api_examples_document_wallet_response_shape() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "/api/v1/wallets/<wallet_address>" in examples
    assert '"address": "mrwk1fb1437aec45b46ec640f44b2e2aced55dc23556e"' in examples
    assert (
        '"public_key_hex": "d88d3edf935ba932ee2737ee5500c795f21caeb4a2fdeacb55a4ff63c52c9d51"'
        in examples
    )
    assert '"label": null' in examples
    assert '"github_login": "prettyboyvic"' in examples
    assert '"balance_mrwk": "50"' in examples
    assert '"nonce": 2' in examples
    assert '"next_nonce": 3' in examples
    assert '"created_at": "2026-05-24T17:50:56.118158"' in examples
    assert "read-only wallet lookup" in examples


def test_agent_guide_explains_internal_bounty_ids() -> None:
    guide = Path("docs/agent-guide.md").read_text(encoding="utf-8")

    assert "/api/v1/bounties/<bounty_id>" in guide
    assert "not the GitHub issue number" in guide


def test_docs_smoke_covers_public_api_examples() -> None:
    assert "docs/api-examples.md" in REQUIRED


def test_contributing_names_docs_smoke_for_public_docs_changes() -> None:
    contributing = Path("CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "python scripts/docs_smoke.py" in contributing
    assert "docs, templates, examples, or onboarding" in contributing


def test_agent_guide_documents_activity_endpoint() -> None:
    guide = Path("docs/agent-guide.md").read_text(encoding="utf-8")

    assert "GET /api/v1/activity" in guide
    assert "accepted-work activity" in guide
