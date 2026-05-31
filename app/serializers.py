from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.config import get_settings
from app.ledger.reconciliation import AcceptedPayoutCheck
from app.ledger.service import format_mrwk, get_balance
from app.models import Bounty, LedgerEntry, Proof, TreasuryProposal, Wallet, WalletTransfer

PendingBountyProposals = tuple[list[dict[str, Any]], dict[str, Any] | None]


def bounty_to_dict(
    bounty: Bounty,
    session: Session | None = None,
    pending_proposals: PendingBountyProposals | None = None,
) -> dict[str, Any]:
    """Serialize a bounty row for public API and page consumers."""
    awards_remaining = max(0, bounty.max_awards - bounty.awards_paid)
    if bounty.status != "open":
        awards_remaining = 0
    available_microunits = bounty.reward_microunits * awards_remaining
    pending_payouts: list[dict[str, Any]] = []
    pending_close: dict[str, Any] | None = None
    if pending_proposals is not None:
        pending_payouts, pending_close = pending_proposals
    elif session is not None:
        pending_payouts, pending_close = _pending_bounty_proposals(session, bounty.id)
    effective_awards_remaining = _effective_awards_remaining(
        awards_remaining, pending_payouts, pending_close
    )
    effective_available_microunits = bounty.reward_microunits * effective_awards_remaining
    availability_state = _availability_state(
        status=bounty.status,
        awards_remaining=awards_remaining,
        effective_awards_remaining=effective_awards_remaining,
        pending_payouts=pending_payouts,
        pending_close=pending_close,
    )
    return {
        "id": bounty.id,
        "repo": bounty.repo,
        "issue_number": bounty.issue_number,
        "issue_url": bounty.issue_url,
        "title": bounty.title,
        "reward_mrwk": format_mrwk(bounty.reward_microunits),
        "available_mrwk": format_mrwk(available_microunits),
        "reserved_mrwk": format_mrwk(bounty.reserved_microunits),
        "max_awards": bounty.max_awards,
        "awards_paid": bounty.awards_paid,
        "awards_remaining": awards_remaining,
        "effective_available_mrwk": format_mrwk(effective_available_microunits),
        "effective_awards_remaining": effective_awards_remaining,
        "pending_payout_awards": len(pending_payouts),
        "pending_payout_proposals": pending_payouts,
        "pending_close_proposal": pending_close,
        "availability_state": availability_state,
        "availability_note": _availability_note(
            status=bounty.status,
            awards_remaining=awards_remaining,
            effective_awards_remaining=effective_awards_remaining,
            pending_payouts=pending_payouts,
            pending_close=pending_close,
        ),
        "status": bounty.status,
        "acceptance": bounty.acceptance,
        "created_at": bounty.created_at.isoformat(),
    }


def bounties_to_dict(
    bounties: Sequence[Bounty], session: Session | None = None
) -> list[dict[str, Any]]:
    """Serialize bounty rows, preloading pending proposals once for list views."""
    if session is None or not bounties:
        return [bounty_to_dict(bounty) for bounty in bounties]

    pending_by_bounty = _pending_bounty_proposals_by_bounty_id(session)
    return [
        bounty_to_dict(
            bounty,
            pending_proposals=pending_by_bounty.get(bounty.id, ([], None)),
        )
        for bounty in bounties
    ]


def bounty_awards_to_dict(session: Session, bounty_id: int) -> list[dict[str, Any]]:
    """Return accepted award proof rows for a bounty."""
    proofs = session.scalars(
        select(Proof)
        .where(Proof.bounty_id == bounty_id, Proof.kind == "bounty_payment")
        .order_by(Proof.ledger_sequence.desc())
    ).all()
    awards: list[dict[str, Any]] = []
    for proof in proofs:
        data = _proof_payload(proof)
        if data is None:
            continue
        proof_hash = str(proof.hash)
        awards.append(
            {
                "proof_hash": proof_hash,
                "proof_url": f"/proofs/{proof_hash}",
                "ledger_sequence": proof.ledger_sequence,
                "ledger_url": f"/ledger/{proof.ledger_sequence}",
                "account": data.get("to_account"),
                "amount_mrwk": data.get("amount_mrwk"),
                "submission_url": data.get("submission_url"),
                "accepted_by": data.get("accepted_by"),
                "created_at": proof.created_at.isoformat(),
            }
        )
    return awards


def bounty_list_summary(bounties: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate visible bounty rows into capacity totals."""
    open_awards = sum(int(bounty["awards_remaining"]) for bounty in bounties)
    open_pool_microunits = sum(
        int(Decimal(str(bounty["reward_mrwk"])) * Decimal(1_000_000))
        * int(bounty["awards_remaining"])
        for bounty in bounties
    )
    effective_open_awards = sum(
        int(bounty.get("effective_awards_remaining", bounty["awards_remaining"]))
        for bounty in bounties
    )
    effective_open_pool_microunits = sum(
        int(Decimal(str(bounty["reward_mrwk"])) * Decimal(1_000_000))
        * int(bounty.get("effective_awards_remaining", bounty["awards_remaining"]))
        for bounty in bounties
    )
    return {
        "bounties_shown": len(bounties),
        "open_awards": open_awards,
        "open_pool_mrwk": format_mrwk(open_pool_microunits),
        "effective_open_awards": effective_open_awards,
        "effective_open_pool_mrwk": format_mrwk(effective_open_pool_microunits),
    }


def _proposal_payload(proposal: TreasuryProposal) -> dict[str, Any] | None:
    try:
        payload = json.loads(proposal.payload_json)
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _proposal_bounty_id(payload: dict[str, Any]) -> int | None:
    try:
        return int(payload["bounty_id"])
    except (KeyError, TypeError, ValueError):
        return None


def _proposal_summary(
    proposal: TreasuryProposal, payload: dict[str, Any], fields: tuple[str, ...]
) -> dict[str, Any]:
    summary = {
        "proposal_id": proposal.id,
        "proposed_by": proposal.proposed_by,
        "proposed_at": proposal.proposed_at.isoformat(),
        "executes_after": proposal.executes_after.isoformat(),
    }
    for field in fields:
        value = payload.get(field)
        summary[field] = str(value) if value is not None else None
    return summary


def _pending_bounty_proposals(
    session: Session, bounty_id: int
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    proposals = session.scalars(
        select(TreasuryProposal)
        .where(
            TreasuryProposal.status == "pending",
            TreasuryProposal.action.in_(("pay_bounty", "close_bounty")),
            _payload_bounty_id_filter(bounty_id),
        )
        .order_by(TreasuryProposal.id.asc())
    ).all()
    pending_payouts: list[dict[str, Any]] = []
    pending_close: dict[str, Any] | None = None
    for proposal in proposals:
        payload = _proposal_payload(proposal)
        if payload is None or _proposal_bounty_id(payload) != bounty_id:
            continue
        if proposal.action == "pay_bounty":
            pending_payouts.append(
                _proposal_summary(
                    proposal,
                    payload,
                    ("to_account", "submission_url", "accepted_by"),
                )
            )
        elif proposal.action == "close_bounty" and pending_close is None:
            pending_close = _proposal_summary(proposal, payload, ("closed_by", "reference"))
    return pending_payouts, pending_close


def _payload_bounty_id_filter(bounty_id: int) -> ColumnElement[bool]:
    """Narrow pending-proposal scans for canonical JSON payloads."""
    marker = f'"bounty_id":{bounty_id}'
    return or_(
        TreasuryProposal.payload_json.contains(f"{marker},"),
        TreasuryProposal.payload_json.contains(f"{marker}}}"),
    )


def _pending_bounty_proposals_by_bounty_id(
    session: Session,
) -> dict[int, PendingBountyProposals]:
    proposals = session.scalars(
        select(TreasuryProposal)
        .where(
            TreasuryProposal.status == "pending",
            TreasuryProposal.action.in_(("pay_bounty", "close_bounty")),
        )
        .order_by(TreasuryProposal.id.asc())
    ).all()
    pending_by_bounty: dict[int, PendingBountyProposals] = {}
    for proposal in proposals:
        payload = _proposal_payload(proposal)
        if payload is None:
            continue
        bounty_id = _proposal_bounty_id(payload)
        if bounty_id is None:
            continue
        pending_payouts, pending_close = pending_by_bounty.setdefault(bounty_id, ([], None))
        if proposal.action == "pay_bounty":
            pending_payouts.append(
                _proposal_summary(
                    proposal,
                    payload,
                    ("to_account", "submission_url", "accepted_by"),
                )
            )
        elif proposal.action == "close_bounty" and pending_close is None:
            pending_by_bounty[bounty_id] = (
                pending_payouts,
                _proposal_summary(proposal, payload, ("closed_by", "reference")),
            )
    return pending_by_bounty


def _effective_awards_remaining(
    awards_remaining: int,
    pending_payouts: list[dict[str, Any]],
    pending_close: dict[str, Any] | None,
) -> int:
    if pending_close is not None:
        return 0
    return max(0, awards_remaining - len(pending_payouts))


def _availability_state(
    *,
    status: str,
    awards_remaining: int,
    effective_awards_remaining: int,
    pending_payouts: list[dict[str, Any]],
    pending_close: dict[str, Any] | None,
) -> str:
    if status != "open":
        return status
    if pending_close is not None:
        return "pending_close"
    if not pending_payouts:
        return "open" if effective_awards_remaining > 0 else "full"
    if awards_remaining > 0 and effective_awards_remaining <= 0:
        return "pending_payouts_full"
    return "pending_payouts_partial"


def _plural_awards(count: int) -> str:
    return f"{count} award{'s' if count != 1 else ''}"


def _availability_note(
    *,
    status: str,
    awards_remaining: int,
    effective_awards_remaining: int,
    pending_payouts: list[dict[str, Any]],
    pending_close: dict[str, Any] | None,
) -> str:
    if status != "open":
        return f"This bounty is {status}; no awards are available for new submissions."
    if pending_close is not None:
        return "A pending close proposal would make this bounty unavailable if executed."
    if pending_payouts:
        pending_count = len(pending_payouts)
        return (
            f"{_plural_awards(pending_count)} covered by pending payout "
            f"proposal{'s' if pending_count != 1 else ''}; "
            f"{_plural_awards(effective_awards_remaining)} effectively available."
        )
    if awards_remaining <= 0:
        return "No awards remain available for new submissions."
    return f"{_plural_awards(effective_awards_remaining)} effectively available."


def payout_reconciliation_to_dict(check: AcceptedPayoutCheck) -> dict[str, Any]:
    """Serialize a payout reconciliation check and its evidence."""
    return {
        "status": check.status,
        "bounty_id": check.bounty_id,
        "bounty_issue": check.bounty_issue,
        "submission_id": check.submission_id,
        "submitter_account": check.submitter_account,
        "submission_url": check.submission_url,
        "evidence_count": len(check.evidence),
        "evidence": [
            {
                "proof_hash": evidence.proof_hash,
                "proof_url": f"/proofs/{evidence.proof_hash}",
                "ledger_sequence": evidence.ledger_sequence,
                "ledger_type": evidence.ledger_type,
                "reference": evidence.reference,
                "to_account": evidence.to_account,
                "amount_mrwk": evidence.amount_mrwk,
                "matches_submission": evidence.matches_submission,
            }
            for evidence in check.evidence
        ],
    }


def ledger_to_dict(entry: LedgerEntry, proof_hash: str | None = None) -> dict[str, Any]:
    """Serialize a ledger entry with an optional public proof hash."""
    return {
        "sequence": entry.sequence,
        "type": entry.entry_type,
        "from": entry.from_account,
        "to": entry.to_account,
        "amount_mrwk": format_mrwk(entry.amount_microunits),
        "reference": entry.reference,
        "previous_hash": entry.previous_hash,
        "entry_hash": entry.entry_hash,
        "proof_hash": proof_hash,
        "created_at": entry.created_at.isoformat(),
    }


def _bounty_detail_url(bounty_id: int | None) -> str | None:
    return f"/bounties/{bounty_id}" if bounty_id is not None else None


def _public_page_url(path: str | None, public_base_url: str | None = None) -> str | None:
    if path is None:
        return None
    base_url = public_base_url if public_base_url is not None else get_settings().public_base_url
    return f"{base_url.rstrip('/')}{path}"


def _proof_payload(proof: Proof) -> dict[str, Any] | None:
    try:
        data = json.loads(proof.public_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("kind") != "bounty_payment":
        return None
    return data


def _activity_row(entry: LedgerEntry, proof: Proof) -> dict[str, Any] | None:
    data = _proof_payload(proof)
    if data is None:
        return None
    submission_url = str(data.get("submission_url") or entry.reference)
    repo = data.get("repo")
    issue_number = data.get("issue_number")
    issue_url = None
    if isinstance(repo, str) and isinstance(issue_number, int):
        issue_url = f"https://github.com/{repo}/issues/{issue_number}"
    return {
        "ledger_sequence": entry.sequence,
        "account": entry.to_account,
        "amount_mrwk": format_mrwk(entry.amount_microunits),
        "amount_microunits": entry.amount_microunits,
        "submission_url": submission_url,
        "bounty_repo": repo,
        "bounty_issue_number": issue_number,
        "bounty_issue_url": issue_url,
        "proof_hash": proof.hash,
        "proof_url": f"/proofs/{proof.hash}",
        "bounty_id": proof.bounty_id,
        "bounty_url": _bounty_detail_url(proof.bounty_id),
        "created_at": entry.created_at.isoformat(),
    }


def _activity_search_query(query: str | None) -> str:
    return (query or "").strip().lower()


def _activity_hash_issue_query(query: str) -> str | None:
    if not query.startswith("#"):
        return None
    issue_number = query[1:]
    return issue_number if issue_number.isdigit() else None


def _activity_row_matches(row: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    searchable_values = (
        row["account"],
        row["amount_mrwk"],
        row["submission_url"],
        row["proof_hash"],
        row["bounty_id"],
        row["bounty_repo"],
        row["bounty_issue_url"],
        row["bounty_issue_number"],
    )
    if any(query in str(value or "").lower() for value in searchable_values):
        return True
    issue_number = _activity_hash_issue_query(query)
    if issue_number is None:
        return False
    return issue_number in {
        str(row["bounty_id"] or ""),
        str(row["bounty_issue_number"] or ""),
    }


def activity_to_dict(session: Session, query: str | None = None) -> dict[str, Any]:
    """Build the public activity feed and contributor totals."""
    search_query = _activity_search_query(query)
    rows = session.execute(
        select(LedgerEntry, Proof)
        .join(Proof, Proof.ledger_sequence == LedgerEntry.sequence)
        .where(LedgerEntry.entry_type == "bounty_payment", Proof.kind == "bounty_payment")
        .order_by(LedgerEntry.sequence.desc())
    ).all()
    recent: list[dict[str, Any]] = []
    seen_sequences: set[int] = set()
    for entry, proof in rows:
        if entry.sequence in seen_sequences:
            continue
        row = _activity_row(entry, proof)
        if row is None or row["account"] is None:
            continue
        if not _activity_row_matches(row, search_query):
            continue
        seen_sequences.add(entry.sequence)
        recent.append(row)

    by_account: dict[str, dict[str, Any]] = {}
    for row in recent:
        account = str(row["account"])
        contributor = by_account.setdefault(
            account,
            {
                "account": account,
                "accepted_awards": 0,
                "accepted_microunits": 0,
                "accepted_mrwk": "0",
                "latest_submission_url": row["submission_url"],
                "latest_bounty_repo": row["bounty_repo"],
                "latest_bounty_issue_number": row["bounty_issue_number"],
                "latest_bounty_issue_url": row["bounty_issue_url"],
                "latest_proof_hash": row["proof_hash"],
                "latest_proof_url": row["proof_url"],
            },
        )
        contributor["accepted_awards"] += 1
        contributor["accepted_microunits"] += int(row["amount_microunits"])
        contributor["accepted_mrwk"] = format_mrwk(contributor["accepted_microunits"])

    contributors = sorted(
        by_account.values(),
        key=lambda item: (-int(item["accepted_microunits"]), str(item["account"])),
    )
    for contributor in contributors:
        del contributor["accepted_microunits"]

    total_microunits = sum(int(row["amount_microunits"]) for row in recent)
    for row in recent:
        del row["amount_microunits"]

    return {
        "totals": {
            "accepted_awards": len(recent),
            "accepted_mrwk": format_mrwk(total_microunits),
            "contributors": len(contributors),
        },
        "query": search_query,
        "contributors": contributors,
        "recent": recent[:100],
    }


def account_accepted_summary(session: Session, account: str) -> dict[str, Any]:
    """Summarize accepted work paid to one ledger account."""
    rows = session.execute(
        select(LedgerEntry, Proof)
        .join(Proof, Proof.ledger_sequence == LedgerEntry.sequence)
        .where(
            LedgerEntry.entry_type == "bounty_payment",
            LedgerEntry.to_account == account,
            Proof.kind == "bounty_payment",
        )
        .order_by(LedgerEntry.sequence.desc())
    ).all()

    accepted: list[dict[str, Any]] = []
    total_microunits = 0
    for entry, proof in rows:
        row = _activity_row(entry, proof)
        if row is None:
            continue
        total_microunits += int(row["amount_microunits"])
        accepted.append(row)

    latest = accepted[0] if accepted else None
    public_base_url = get_settings().public_base_url if latest else None
    return {
        "accepted_awards": len(accepted),
        "accepted_mrwk": format_mrwk(total_microunits),
        "latest_ledger_sequence": latest["ledger_sequence"] if latest else None,
        "latest_submission_url": latest["submission_url"] if latest else None,
        "latest_proof_hash": latest["proof_hash"] if latest else None,
        "latest_proof_url": latest["proof_url"] if latest else None,
        "latest_proof_public_url": (
            _public_page_url(str(latest["proof_url"]), public_base_url) if latest else None
        ),
    }


def accepted_work_for_account(session: Session, account: str) -> list[dict[str, Any]]:
    """Return accepted work proof rows for one ledger account."""
    rows = session.execute(
        select(Proof, LedgerEntry)
        .join(LedgerEntry, LedgerEntry.sequence == Proof.ledger_sequence)
        .where(
            Proof.kind == "bounty_payment",
            LedgerEntry.entry_type == "bounty_payment",
            LedgerEntry.to_account == account,
        )
        .order_by(LedgerEntry.sequence.desc())
    ).all()
    accepted_work: list[dict[str, Any]] = []
    public_base_url = get_settings().public_base_url if rows else None
    for proof, entry in rows:
        data = _proof_payload(proof)
        if data is None:
            continue
        repo = data.get("repo")
        issue_number = data.get("issue_number")
        issue_url = None
        if isinstance(repo, str) and isinstance(issue_number, int):
            issue_url = f"https://github.com/{repo}/issues/{issue_number}"
        ledger_url = f"/ledger/{entry.sequence}"
        proof_url = f"/proofs/{proof.hash}"
        bounty_url = _bounty_detail_url(proof.bounty_id)
        accepted_work.append(
            {
                "ledger_sequence": entry.sequence,
                "ledger_url": ledger_url,
                "ledger_public_url": _public_page_url(ledger_url, public_base_url),
                "proof_hash": proof.hash,
                "proof_url": proof_url,
                "proof_public_url": _public_page_url(proof_url, public_base_url),
                "amount_mrwk": format_mrwk(entry.amount_microunits),
                "submission_url": data.get("submission_url"),
                "issue_url": issue_url,
                "repo": repo,
                "issue_number": issue_number,
                "bounty_id": proof.bounty_id,
                "bounty_url": bounty_url,
                "bounty_public_url": _public_page_url(bounty_url, public_base_url),
                "accepted_by": data.get("accepted_by"),
                "created_at": entry.created_at.isoformat(),
            }
        )
    return accepted_work


def empty_accepted_summary() -> dict[str, Any]:
    """Return the stable empty shape for accepted-work summaries."""
    return {
        "accepted_awards": 0,
        "accepted_mrwk": "0",
        "latest_ledger_sequence": None,
        "latest_submission_url": None,
        "latest_proof_hash": None,
        "latest_proof_url": None,
        "latest_proof_public_url": None,
    }


def safe_account_accepted_summary(session: Session, account: str) -> dict[str, Any]:
    """Return an accepted-work summary, falling back to the empty shape."""
    try:
        return account_accepted_summary(session, account)
    except Exception:
        return empty_accepted_summary()


def safe_accepted_work_for_account(session: Session, account: str) -> list[dict[str, Any]]:
    """Return accepted-work rows, falling back to an empty list."""
    try:
        return accepted_work_for_account(session, account)
    except Exception:
        return []


def _wallet_timestamp(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.isoformat()


def wallet_to_dict(session: Session, wallet: Wallet) -> dict[str, Any]:
    """Serialize a registered wallet and its current ledger balance."""
    return {
        "address": wallet.address,
        "public_key_hex": wallet.public_key_hex,
        "label": wallet.label,
        "github_login": wallet.github_login,
        "balance_mrwk": format_mrwk(get_balance(session, wallet.address)),
        "nonce": wallet.nonce,
        "next_nonce": wallet.nonce + 1,
        "created_at": _wallet_timestamp(wallet.created_at),
    }


def wallet_transfer_to_dict(transfer: WalletTransfer) -> dict[str, Any]:
    """Serialize a signed wallet transfer record."""
    return {
        "hash": transfer.hash,
        "type": "wallet_transfer",
        "ledger_sequence": transfer.ledger_sequence,
        "from_address": transfer.from_address,
        "to_address": transfer.to_address,
        "amount_mrwk": format_mrwk(transfer.amount_microunits),
        "nonce": transfer.nonce,
        "memo": transfer.memo,
        "created_at": _wallet_timestamp(transfer.created_at),
    }
