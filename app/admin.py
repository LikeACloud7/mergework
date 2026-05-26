from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import WebhookEvent

WEBHOOK_OUTCOME_SCAN_ORDER = {
    "missing_submitter": 0,
    "bounty_not_found": 1,
    "exhausted_bounty": 2,
    "duplicate_delivery": 3,
    "delivery_payload_mismatch": 4,
    "already_paid": 5,
    "paid": 6,
}


def normalize_webhook_status_filter(status: str | None) -> str | None:
    if status is None:
        return None
    normalized = status.strip().lower()
    return normalized or None


def list_webhook_events(
    session: Session, status: str | None = None, limit: int = 50
) -> list[WebhookEvent]:
    normalized_status = normalize_webhook_status_filter(status)
    query = select(WebhookEvent)
    if normalized_status is not None:
        query = query.where(func.lower(WebhookEvent.processed_status) == normalized_status)
    return list(
        session.scalars(
            query.order_by(WebhookEvent.created_at.desc(), WebhookEvent.delivery_id.desc()).limit(
                limit
            )
        ).all()
    )


def webhook_event_to_dict(event: WebhookEvent) -> dict[str, Any]:
    return {
        "delivery_id": event.delivery_id,
        "event_type": event.event_type,
        "processed_status": event.processed_status,
        "payload_hash": event.payload_hash,
        "created_at": event.created_at.isoformat(),
    }


def webhook_events_to_dict(events: list[WebhookEvent]) -> list[dict[str, Any]]:
    return [webhook_event_to_dict(event) for event in events]


def webhook_status_summary(session: Session) -> list[dict[str, Any]]:
    status_expr = func.lower(WebhookEvent.processed_status)
    count_expr = func.count(WebhookEvent.delivery_id)
    rows = session.execute(
        select(status_expr, count_expr)
        .group_by(status_expr)
        .order_by(count_expr.desc(), status_expr.asc())
    ).all()
    summary = [
        {"processed_status": str(status), "count": int(count)} for status, count in rows if status
    ]
    return sorted(
        summary,
        key=lambda item: (
            WEBHOOK_OUTCOME_SCAN_ORDER.get(str(item["processed_status"]), 100),
            -int(item["count"]),
            str(item["processed_status"]),
        ),
    )
