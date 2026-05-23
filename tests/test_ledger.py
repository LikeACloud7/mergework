from __future__ import annotations

import pytest

from app.db import create_schema, session_scope
from app.ledger.service import (
    GENESIS_SUPPLY_MICRO,
    TREASURY_ACCOUNT,
    LedgerError,
    create_bounty,
    ensure_genesis,
    get_balance,
    pay_bounty,
    verify_hash_chain,
    verify_supply_conservation,
)
from app.models import LedgerEntry


def test_genesis_creates_fixed_supply_once(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        first = ensure_genesis(session)
        second = ensure_genesis(session)

        assert first.sequence == 1
        assert second.sequence == 1
        assert get_balance(session, TREASURY_ACCOUNT) == GENESIS_SUPPLY_MICRO
        assert verify_hash_chain(session) is True
        assert verify_supply_conservation(session) is True


def test_bounty_reserve_and_payout_conserve_supply(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=7,
            issue_url="https://github.com/ramimbo/mergework/issues/7",
            title="Write ledger tests",
            reward_mrwk="125.5",
            acceptance="Merged PR with tests",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/8",
            accepted_by="maintainer",
            verifier_result={"merged": True, "ci": "passed"},
        )

        assert get_balance(session, "github:alice") == 125_500_000
        assert get_balance(session, TREASURY_ACCOUNT) == GENESIS_SUPPLY_MICRO - 125_500_000
        assert proof.hash
        assert verify_hash_chain(session) is True
        assert verify_supply_conservation(session) is True


def test_payout_is_idempotent_for_same_bounty(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=9,
            issue_url="https://github.com/ramimbo/mergework/issues/9",
            title="Fix docs",
            reward_mrwk="50",
            acceptance="Accepted label",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/10",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        with pytest.raises(LedgerError, match="already paid"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:bob",
                submission_url="https://github.com/ramimbo/mergework/pull/10",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
            )


def test_hash_chain_detects_tampering(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        entry = session.get(LedgerEntry, 1)
        assert entry is not None
        entry.amount_microunits = 1

        assert verify_hash_chain(session) is False
