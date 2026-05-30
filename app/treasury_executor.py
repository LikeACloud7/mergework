from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.github_issue_finalization import finalize_created_bounty_issue
from app.ledger.service import LedgerError
from app.models import TreasuryProposal, utc_now
from app.treasury import (
    execute_treasury_proposal,
    proposal_to_dict,
    record_proposal_result_field,
)

Finalizer = Callable[..., dict[str, Any]]


def _db_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def due_treasury_proposal_ids(db_url: str, *, limit: int = 25) -> list[int]:
    now = _db_utc(utc_now())
    with session_scope(db_url) as session:
        return [
            int(proposal_id)
            for proposal_id in session.scalars(
                select(TreasuryProposal.id)
                .where(
                    TreasuryProposal.status == "pending",
                    TreasuryProposal.executes_after <= now,
                )
                .order_by(TreasuryProposal.executes_after.asc(), TreasuryProposal.id.asc())
                .limit(limit)
            ).all()
        ]


def execute_treasury_proposal_with_finalization(
    db_url: str,
    *,
    proposal_id: int,
    executed_by: str,
    github_issue_token: str,
    public_base_url: str,
    finalizer: Finalizer | None = None,
) -> dict[str, Any]:
    with session_scope(db_url) as session:
        proposal = execute_treasury_proposal(
            session, proposal_id=proposal_id, executed_by=executed_by
        )
        response = proposal_to_dict(proposal)
    if response["action"] != "create_bounty":
        return response
    bounty = response.get("result", {}).get("bounty")
    if not isinstance(bounty, dict):
        return response
    issue_finalizer = finalizer or finalize_created_bounty_issue
    try:
        finalization = issue_finalizer(
            github_token=github_issue_token,
            public_base_url=public_base_url,
            bounty=bounty,
        )
    except Exception as exc:
        finalization = {
            "status": "failed",
            "reason": f"github issue finalization failed: {type(exc).__name__}",
        }
    with session_scope(db_url) as session:
        proposal = record_proposal_result_field(
            session,
            proposal_id=proposal_id,
            field="github_issue_finalization",
            value=finalization,
        )
        return proposal_to_dict(proposal)


def execute_due_treasury_proposals(
    db_url: str,
    *,
    github_issue_token: str,
    public_base_url: str,
    executed_by: str = "treasury-executor",
    limit: int = 25,
    finalizer: Finalizer | None = None,
) -> dict[str, Any]:
    proposal_ids = due_treasury_proposal_ids(db_url, limit=limit)
    results: list[dict[str, Any]] = []
    executed = 0
    failed = 0
    for proposal_id in proposal_ids:
        action = "unknown"
        with session_scope(db_url) as session:
            proposal = session.get(TreasuryProposal, proposal_id)
            if proposal is not None:
                action = proposal.action
        try:
            response = execute_treasury_proposal_with_finalization(
                db_url,
                proposal_id=proposal_id,
                executed_by=executed_by,
                github_issue_token=github_issue_token,
                public_base_url=public_base_url,
                finalizer=finalizer,
            )
        except LedgerError as exc:
            failed += 1
            results.append(
                {
                    "proposal_id": proposal_id,
                    "action": action,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            continue
        executed += 1
        item = {
            "proposal_id": int(response["id"]),
            "action": str(response["action"]),
            "status": str(response["status"]),
            "result": response["result"],
        }
        finalization = response.get("result", {}).get("github_issue_finalization")
        if finalization is not None:
            item["github_issue_finalization"] = finalization
        results.append(item)
    return {
        "type": "treasury_executor_run",
        "attempted": len(proposal_ids),
        "executed": executed,
        "failed": failed,
        "results": results,
    }
