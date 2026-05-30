"""Regression test for PR #510: only 'issues' and 'pull_request' events route
to the label handler.  Previously 'check_suite', 'push', and 'label' were also
routed, which caused unhandled-key errors in the label handler.

This test ensures those events are recorded as 'ignored' while the two
supported event types continue to work.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, get_balance
from app.models import WebhookEvent
from app.webhooks.github import handle_github_webhook

SECRET = "test-secret"


def _sig(body: bytes) -> str:
    return f"sha256={hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()}"


def _wrap(event_type: str, payload: dict, delivery_id: str) -> tuple[dict, bytes]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": event_type,
        "X-Hub-Signature-256": _sig(body),
    }
    return headers, body


# -- events that must be ignored (PR #510 regression) -----------------------


def test_check_suite_event_is_ignored(sqlite_url: str) -> None:
    """check_suite must NOT be routed to the label handler."""
    create_schema(sqlite_url)
    payload = {
        "action": "completed",
        "check_suite": {"id": 1},
        "repository": {"full_name": "ramimbo/mergework"},
        "sender": {"login": "bot"},
    }
    headers, body = _wrap("check_suite", payload, "delivery-check-suite")

    result = handle_github_webhook(sqlite_url, headers, body, SECRET)

    assert result == {"status": "ignored"}
    with session_scope(sqlite_url) as session:
        event = session.get(WebhookEvent, "delivery-check-suite")
        assert event is not None
        assert event.processed_status == "ignored"


def test_push_event_is_ignored(sqlite_url: str) -> None:
    """push must NOT be routed to the label handler."""
    create_schema(sqlite_url)
    payload = {
        "ref": "refs/heads/main",
        "repository": {"full_name": "ramimbo/mergework"},
        "sender": {"login": "dev"},
    }
    headers, body = _wrap("push", payload, "delivery-push")

    result = handle_github_webhook(sqlite_url, headers, body, SECRET)

    assert result == {"status": "ignored"}
    with session_scope(sqlite_url) as session:
        event = session.get(WebhookEvent, "delivery-push")
        assert event is not None
        assert event.processed_status == "ignored"


def test_label_event_is_ignored(sqlite_url: str) -> None:
    """label events must NOT be routed to the label handler."""
    create_schema(sqlite_url)
    payload = {
        "action": "created",
        "label": {"name": "bug"},
        "repository": {"full_name": "ramimbo/mergework"},
        "sender": {"login": "maintainer"},
    }
    headers, body = _wrap("label", payload, "delivery-label")

    result = handle_github_webhook(sqlite_url, headers, body, SECRET)

    assert result == {"status": "ignored"}
    with session_scope(sqlite_url) as session:
        event = session.get(WebhookEvent, "delivery-label")
        assert event is not None
        assert event.processed_status == "ignored"


# -- events that must still route to the label handler ----------------------


def test_issues_event_routes_to_label_handler(sqlite_url: str) -> None:
    """'issues' events must still be processed by the label handler."""
    create_schema(sqlite_url)
    payload = {
        "action": "labeled",
        "label": {"name": "mrwk:accepted"},
        "issue": {
            "number": 100,
            "html_url": "https://github.com/ramimbo/mergework/issues/100",
            "user": {"login": "contributor"},
        },
        "repository": {"full_name": "ramimbo/mergework"},
        "sender": {"login": "maintainer"},
    }
    headers, body = _wrap("issues", payload, "delivery-issues-route")

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=100,
            issue_url="https://github.com/ramimbo/mergework/issues/100",
            title="Routing test bounty",
            reward_mrwk="50",
            acceptance="Maintainer applies mrwk:accepted",
        )

    result = handle_github_webhook(sqlite_url, headers, body, SECRET)

    assert result["status"] == "paid"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:contributor") == 50_000_000


def test_pull_request_event_routes_to_label_handler(sqlite_url: str) -> None:
    """'pull_request' events must still be processed by the label handler."""
    create_schema(sqlite_url)
    payload = {
        "action": "labeled",
        "label": {"name": "mrwk:accepted"},
        "pull_request": {
            "number": 10,
            "html_url": "https://github.com/ramimbo/mergework/pull/10",
            "body": "Closes #101",
            "user": {"login": "contributor"},
        },
        "repository": {"full_name": "ramimbo/mergework"},
        "sender": {"login": "maintainer"},
    }
    headers, body = _wrap("pull_request", payload, "delivery-pr-route")

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=101,
            issue_url="https://github.com/ramimbo/mergework/issues/101",
            title="PR routing test bounty",
            reward_mrwk="75",
            acceptance="Accepted PR closes the bounty.",
        )

    result = handle_github_webhook(sqlite_url, headers, body, SECRET)

    assert result["status"] == "paid"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:contributor") == 75_000_000
