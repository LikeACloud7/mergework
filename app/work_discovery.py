from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Bounty, TreasuryProposal
from app.serializers import bounties_to_dict, public_utc_timestamp
from app.treasury import proposal_payload

DEFAULT_WORK_DISCOVERY_LIMIT = 50
MAX_WORK_DISCOVERY_LIMIT = 100

STATE_DEFINITIONS = {
    "live_bounty": "Public bounty row is open and has positive effective_awards_remaining.",
    "pending_create": "Public treasury proposal exists but the bounty row is not live yet.",
    "pending_payout": "Accepted work has a pending pay_bounty proposal, not proof-backed payment.",
    "closed_or_exhausted": "Bounty is closed, paid, or has no effective award capacity.",
    "proposed_work": (
        "GitHub proposed-work issue is intake only until a create_bounty proposal executes."
    ),
    "board_or_index": "Index issues help discovery but are not claimable bounty work.",
}


NON_CLAIMABLE_ISSUE_STATES = [
    {
        "availability_state": "proposed_work",
        "note": STATE_DEFINITIONS["proposed_work"],
    },
    {
        "availability_state": "board_or_index",
        "repo": "ramimbo/mergework",
        "issue_number": 785,
        "issue_url": "https://github.com/ramimbo/mergework/issues/785",
        "title": "MRWK bounty board",
        "note": STATE_DEFINITIONS["board_or_index"],
    },
]


def _bounty_source_urls(row: dict[str, Any]) -> dict[str, str]:
    bounty_id = int(row["id"])
    return {
        "bounty": f"/api/v1/bounties/{bounty_id}",
        "attempts": f"/api/v1/bounties/{bounty_id}/attempts",
        "github_issue": str(row["issue_url"]),
    }


def _bounty_work_item(row: dict[str, Any], availability_state: str) -> dict[str, Any]:
    return {
        "availability_state": availability_state,
        "bounty_id": int(row["id"]),
        "issue_number": int(row["issue_number"]),
        "title": str(row["title"]),
        "issue_url": str(row["issue_url"]),
        "reward_mrwk": str(row["reward_mrwk"]),
        "max_awards": int(row["max_awards"]),
        "effective_awards_remaining": int(row["effective_awards_remaining"]),
        "bounty_availability_state": str(row["availability_state"]),
        "pending_payout_awards": int(row["pending_payout_awards"]),
        "source_urls": _bounty_source_urls(row),
    }


def _not_claimable_state(row: dict[str, Any]) -> str:
    if int(row["pending_payout_awards"]) > 0 and int(row["effective_awards_remaining"]) <= 0:
        return "pending_payout"
    return "closed_or_exhausted"


def _pending_create_item(proposal: TreasuryProposal) -> dict[str, Any]:
    payload = proposal_payload(proposal)
    return {
        "availability_state": "pending_create",
        "proposal_id": int(proposal.id),
        "issue_number": int(payload["issue_number"]),
        "title": str(payload["title"]),
        "issue_url": str(payload["issue_url"]),
        "reward_mrwk": str(payload["reward_mrwk"]),
        "max_awards": int(payload["max_awards"]),
        "effective_awards_remaining": 0,
        "executes_after": public_utc_timestamp(proposal.executes_after),
        "source_urls": {
            "proposal": f"/api/v1/treasury/proposals/{proposal.id}",
            "github_issue": str(payload["issue_url"]),
        },
    }


def work_discovery_to_dict(
    session: Session,
    *,
    limit: int = DEFAULT_WORK_DISCOVERY_LIMIT,
) -> dict[str, Any]:
    """Return public read-only work discovery grouped by claimability."""
    capped_limit = max(1, min(limit, MAX_WORK_DISCOVERY_LIMIT))
    open_bounties = session.scalars(
        select(Bounty).where(Bounty.status == "open").order_by(Bounty.id.desc()).limit(capped_limit)
    ).all()
    terminal_bounties = session.scalars(
        select(Bounty).where(Bounty.status != "open").order_by(Bounty.id.desc()).limit(capped_limit)
    ).all()
    open_rows = bounties_to_dict(open_bounties, session=session)
    terminal_rows = bounties_to_dict(terminal_bounties, session=session)
    claimable_now: list[dict[str, Any]] = []
    not_claimable: list[dict[str, Any]] = []
    for row in open_rows:
        if int(row["effective_awards_remaining"]) > 0:
            claimable_now.append(_bounty_work_item(row, "live_bounty"))
        else:
            not_claimable.append(_bounty_work_item(row, _not_claimable_state(row)))
    for row in terminal_rows:
        not_claimable.append(_bounty_work_item(row, _not_claimable_state(row)))

    pending_create_proposals = session.scalars(
        select(TreasuryProposal)
        .where(TreasuryProposal.status == "pending", TreasuryProposal.action == "create_bounty")
        .order_by(TreasuryProposal.executes_after.asc(), TreasuryProposal.id.asc())
        .limit(capped_limit)
    ).all()
    opening_soon = [_pending_create_item(proposal) for proposal in pending_create_proposals]

    return {
        "type": "work_discovery",
        "summary": {
            "claimable_now_count": len(claimable_now),
            "opening_soon_count": len(opening_soon),
            "not_claimable_count": len(not_claimable[:capped_limit]),
            "limit": capped_limit,
        },
        "state_definitions": STATE_DEFINITIONS,
        "claimable_now": claimable_now[:capped_limit],
        "opening_soon": opening_soon,
        "not_claimable": not_claimable[:capped_limit],
        "non_claimable_issue_states": NON_CLAIMABLE_ISSUE_STATES,
    }
