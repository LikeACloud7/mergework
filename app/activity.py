from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger.service import format_mrwk
from app.models import LedgerEntry, Proof


def _bounty_detail_url(bounty_id: int | None) -> str | None:
    return f"/bounties/{bounty_id}" if bounty_id is not None else None


def _activity_row(entry: LedgerEntry, proof: Proof) -> dict[str, Any] | None:
    data = json.loads(proof.public_json)
    if not isinstance(data, dict) or data.get("kind") != "bounty_payment":
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
    return any(query in str(value or "").lower() for value in searchable_values)


def activity_to_dict(session: Session, query: str | None = None) -> dict[str, Any]:
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
    return {
        "accepted_awards": len(accepted),
        "accepted_mrwk": format_mrwk(total_microunits),
        "latest_ledger_sequence": latest["ledger_sequence"] if latest else None,
        "latest_submission_url": latest["submission_url"] if latest else None,
        "latest_proof_hash": latest["proof_hash"] if latest else None,
        "latest_proof_url": latest["proof_url"] if latest else None,
    }


def accepted_work_for_account(session: Session, account: str) -> list[dict[str, Any]]:
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
    for proof, entry in rows:
        data = json.loads(proof.public_json)
        if not isinstance(data, dict) or data.get("kind") != "bounty_payment":
            continue
        repo = data.get("repo")
        issue_number = data.get("issue_number")
        issue_url = None
        if isinstance(repo, str) and isinstance(issue_number, int):
            issue_url = f"https://github.com/{repo}/issues/{issue_number}"
        accepted_work.append(
            {
                "ledger_sequence": entry.sequence,
                "ledger_url": f"/ledger/{entry.sequence}",
                "proof_hash": proof.hash,
                "proof_url": f"/proofs/{proof.hash}",
                "amount_mrwk": format_mrwk(entry.amount_microunits),
                "submission_url": data.get("submission_url"),
                "issue_url": issue_url,
                "repo": repo,
                "issue_number": issue_number,
                "bounty_id": proof.bounty_id,
                "bounty_url": _bounty_detail_url(proof.bounty_id),
                "accepted_by": data.get("accepted_by"),
                "created_at": entry.created_at.isoformat(),
            }
        )
    return accepted_work


def empty_accepted_summary() -> dict[str, Any]:
    return {
        "accepted_awards": 0,
        "accepted_mrwk": "0",
        "latest_ledger_sequence": None,
        "latest_submission_url": None,
        "latest_proof_hash": None,
        "latest_proof_url": None,
    }


def safe_account_accepted_summary(session: Session, account: str) -> dict[str, Any]:
    try:
        return account_accepted_summary(session, account)
    except Exception:
        return empty_accepted_summary()


def safe_accepted_work_for_account(session: Session, account: str) -> list[dict[str, Any]]:
    try:
        return accepted_work_for_account(session, account)
    except Exception:
        return []
