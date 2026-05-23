from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from app.db import session_scope
from app.ledger.service import (
    LedgerError,
    find_bounty_by_issue,
    linked_wallet_for_github,
    pay_bounty,
)
from app.models import WebhookEvent

ACCEPTED_LABEL = "mrwk:accepted"


def verify_github_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header, f"sha256={expected}")


def _payload_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _record_event(
    database_url: str,
    delivery_id: str,
    event_type: str,
    payload_hash: str,
    status: str,
) -> None:
    with session_scope(database_url) as session:
        session.add(
            WebhookEvent(
                delivery_id=delivery_id,
                event_type=event_type,
                payload_hash=payload_hash,
                processed_status=status,
            )
        )


def _handle_accepted_issue_label(
    database_url: str,
    payload: dict[str, Any],
    event_type: str,
    delivery_id: str,
    payload_hash: str,
) -> dict[str, Any]:
    issue = payload.get("issue") or payload.get("pull_request") or {}
    repo = (payload.get("repository") or {}).get("full_name")
    issue_number = issue.get("number")
    label = (payload.get("label") or {}).get("name", "")
    if payload.get("action") != "labeled" or label.lower() != ACCEPTED_LABEL:
        _record_event(database_url, delivery_id, event_type, payload_hash, "ignored")
        return {"status": "ignored"}
    if not repo or not isinstance(issue_number, int):
        _record_event(database_url, delivery_id, event_type, payload_hash, "missing_issue")
        return {"status": "missing_issue"}

    submitter = ((issue.get("user") or {}).get("login") or "unknown").strip()
    accepted_by = ((payload.get("sender") or {}).get("login") or "maintainer").strip()
    with session_scope(database_url) as session:
        bounty = find_bounty_by_issue(session, repo, issue_number)
        if bounty is None:
            session.add(
                WebhookEvent(
                    delivery_id=delivery_id,
                    event_type=event_type,
                    payload_hash=payload_hash,
                    processed_status="bounty_not_found",
                )
            )
            return {"status": "bounty_not_found"}
        try:
            linked_wallet = linked_wallet_for_github(session, submitter)
            to_account = (
                linked_wallet.address if linked_wallet is not None else f"github:{submitter}"
            )
            proof = pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account=to_account,
                submission_url=issue.get("html_url", bounty.issue_url),
                accepted_by=accepted_by,
                verifier_result={
                    "event": event_type,
                    "label": ACCEPTED_LABEL,
                    "delivery_id": delivery_id,
                },
            )
        except LedgerError as exc:
            session.add(
                WebhookEvent(
                    delivery_id=delivery_id,
                    event_type=event_type,
                    payload_hash=payload_hash,
                    processed_status=str(exc).replace(" ", "_"),
                )
            )
            return {"status": str(exc).replace(" ", "_")}
        session.add(
            WebhookEvent(
                delivery_id=delivery_id,
                event_type=event_type,
                payload_hash=payload_hash,
                processed_status="paid",
            )
        )
        return {"status": "paid", "proof_hash": proof.hash}


def handle_github_webhook(
    database_url: str,
    headers: dict[str, str],
    body: bytes,
    webhook_secret: str,
) -> dict[str, Any]:
    signature = headers.get("X-Hub-Signature-256")
    if not verify_github_signature(body, signature, webhook_secret):
        return {"status": "unauthorized"}

    delivery_id = headers.get("X-GitHub-Delivery", "")
    event_type = headers.get("X-GitHub-Event", "")
    if not delivery_id:
        return {"status": "missing_delivery"}
    hashed = _payload_hash(body)
    with session_scope(database_url) as session:
        existing = session.get(WebhookEvent, delivery_id)
        if existing is not None:
            return {"status": "duplicate", "processed_status": existing.processed_status}

    payload = json.loads(body.decode())
    if event_type in {"issues", "pull_request", "label", "check_suite", "push"}:
        return _handle_accepted_issue_label(database_url, payload, event_type, delivery_id, hashed)

    _record_event(database_url, delivery_id, event_type, hashed, "ignored")
    return {"status": "ignored"}
