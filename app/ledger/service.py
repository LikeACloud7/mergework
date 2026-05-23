from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, Bounty, LedgerEntry, Proof, Submission, utc_now

TREASURY_ACCOUNT = "treasury:mrwk"
MICRO_UNITS = 1_000_000
GENESIS_SUPPLY_MICRO = 100_000_000 * MICRO_UNITS


class LedgerError(RuntimeError):
    pass


def parse_mrwk_amount(amount: str | int | Decimal) -> int:
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise LedgerError("invalid MRWK amount") from exc
    if value <= 0:
        raise LedgerError("amount must be positive")
    microunits = int(value * MICRO_UNITS)
    if Decimal(microunits) / MICRO_UNITS != value:
        raise LedgerError("MRWK supports at most 6 decimal places")
    return microunits


def format_mrwk(microunits: int) -> str:
    whole, frac = divmod(microunits, MICRO_UNITS)
    if frac == 0:
        return str(whole)
    return f"{whole}.{frac:06d}".rstrip("0")


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def reserve_account_for_bounty(bounty_id: int) -> str:
    return f"reserve:bounty:{bounty_id}"


def _canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds") + "Z"


def _entry_payload(entry: LedgerEntry) -> dict[str, Any]:
    return {
        "sequence": entry.sequence,
        "entry_type": entry.entry_type,
        "from_account": entry.from_account,
        "to_account": entry.to_account,
        "amount_microunits": entry.amount_microunits,
        "reference": entry.reference,
        "previous_hash": entry.previous_hash,
        "created_at": _canonical_timestamp(entry.created_at),
    }


def compute_entry_hash(entry: LedgerEntry) -> str:
    return hashlib.sha256(canonical_json(_entry_payload(entry)).encode()).hexdigest()


def ensure_account(session: Session, account_id: str) -> Account:
    account = session.get(Account, account_id)
    if account is not None:
        return account
    github_login = account_id.removeprefix("github:") if account_id.startswith("github:") else None
    account = Account(id=account_id, github_login=github_login, display_name=github_login)
    session.add(account)
    session.flush()
    return account


def _next_sequence(session: Session) -> int:
    current = session.scalar(select(func.max(LedgerEntry.sequence)))
    return int(current or 0) + 1


def _previous_hash(session: Session) -> str:
    entry = session.scalar(select(LedgerEntry).order_by(LedgerEntry.sequence.desc()).limit(1))
    return entry.entry_hash if entry else "0" * 64


def add_ledger_entry(
    session: Session,
    *,
    entry_type: str,
    from_account: str | None,
    to_account: str | None,
    amount_microunits: int,
    reference: str,
) -> LedgerEntry:
    if amount_microunits < 0:
        raise LedgerError("ledger amount cannot be negative")
    if from_account:
        ensure_account(session, from_account)
    if to_account:
        ensure_account(session, to_account)
    entry = LedgerEntry(
        sequence=_next_sequence(session),
        entry_type=entry_type,
        from_account=from_account,
        to_account=to_account,
        amount_microunits=amount_microunits,
        reference=reference,
        previous_hash=_previous_hash(session),
        entry_hash="",
        created_at=utc_now(),
    )
    entry.entry_hash = compute_entry_hash(entry)
    session.add(entry)
    session.flush()
    return entry


def ensure_genesis(session: Session) -> LedgerEntry:
    existing = session.get(LedgerEntry, 1)
    if existing is not None:
        return existing
    ensure_account(session, TREASURY_ACCOUNT)
    return add_ledger_entry(
        session,
        entry_type="genesis",
        from_account=None,
        to_account=TREASURY_ACCOUNT,
        amount_microunits=GENESIS_SUPPLY_MICRO,
        reference="mergework-genesis",
    )


def create_bounty(
    session: Session,
    *,
    repo: str,
    issue_number: int,
    issue_url: str,
    title: str,
    reward_mrwk: str,
    acceptance: str,
) -> Bounty:
    ensure_genesis(session)
    reward = parse_mrwk_amount(reward_mrwk)
    if get_balance(session, TREASURY_ACCOUNT) < reward:
        raise LedgerError("treasury balance too low")
    bounty = Bounty(
        repo=repo,
        issue_number=issue_number,
        issue_url=issue_url,
        title=title,
        reward_microunits=reward,
        reserved_microunits=reward,
        status="open",
        acceptance=acceptance,
    )
    session.add(bounty)
    session.flush()
    add_ledger_entry(
        session,
        entry_type="bounty_reserve",
        from_account=TREASURY_ACCOUNT,
        to_account=reserve_account_for_bounty(bounty.id),
        amount_microunits=reward,
        reference=issue_url,
    )
    return bounty


def find_bounty_by_issue(session: Session, repo: str, issue_number: int) -> Bounty | None:
    return session.scalar(
        select(Bounty).where(Bounty.repo == repo, Bounty.issue_number == issue_number).limit(1)
    )


def get_balance(session: Session, account_id: str) -> int:
    credits = session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_microunits), 0)).where(
            LedgerEntry.to_account == account_id
        )
    )
    debits = session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_microunits), 0)).where(
            LedgerEntry.from_account == account_id
        )
    )
    return int(credits or 0) - int(debits or 0)


def pay_bounty(
    session: Session,
    *,
    bounty_id: int,
    to_account: str,
    submission_url: str,
    accepted_by: str,
    verifier_result: dict[str, Any],
) -> Proof:
    ensure_genesis(session)
    bounty = session.get(Bounty, bounty_id)
    if bounty is None:
        raise LedgerError("bounty not found")
    if bounty.status == "paid":
        raise LedgerError("bounty already paid")
    reserve_account = reserve_account_for_bounty(bounty.id)
    if get_balance(session, reserve_account) < bounty.reward_microunits:
        raise LedgerError("bounty reserve balance too low")

    submission = Submission(
        bounty_id=bounty.id,
        submitter_account=to_account,
        url=submission_url,
        status="accepted",
        verifier_result=canonical_json(verifier_result),
    )
    session.add(submission)
    session.flush()
    ledger_entry = add_ledger_entry(
        session,
        entry_type="bounty_payment",
        from_account=reserve_account,
        to_account=to_account,
        amount_microunits=bounty.reward_microunits,
        reference=submission_url,
    )
    bounty.status = "paid"
    proof_payload = {
        "kind": "bounty_payment",
        "bounty_id": bounty.id,
        "repo": bounty.repo,
        "issue_number": bounty.issue_number,
        "submission_url": submission_url,
        "accepted_by": accepted_by,
        "to_account": to_account,
        "amount_mrwk": format_mrwk(bounty.reward_microunits),
        "ledger_sequence": ledger_entry.sequence,
        "ledger_hash": ledger_entry.entry_hash,
        "verifier_result": verifier_result,
    }
    proof_hash = hashlib.sha256(canonical_json(proof_payload).encode()).hexdigest()
    proof = Proof(
        hash=proof_hash,
        ledger_sequence=ledger_entry.sequence,
        bounty_id=bounty.id,
        submission_id=submission.id,
        kind="bounty_payment",
        public_json=canonical_json(proof_payload),
    )
    session.add(proof)
    session.flush()
    return proof


def verify_supply_conservation(session: Session) -> bool:
    credits = session.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_microunits), 0)))
    debits = session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_microunits), 0)).where(
            LedgerEntry.from_account.is_not(None)
        )
    )
    return int(credits or 0) - int(debits or 0) == GENESIS_SUPPLY_MICRO


def verify_hash_chain(session: Session) -> bool:
    entries = list(session.scalars(select(LedgerEntry).order_by(LedgerEntry.sequence)).all())
    previous = "0" * 64
    expected_sequence = 1
    for entry in entries:
        if entry.sequence != expected_sequence:
            return False
        if entry.previous_hash != previous:
            return False
        if entry.entry_hash != compute_entry_hash(entry):
            return False
        previous = entry.entry_hash
        expected_sequence += 1
    return True
