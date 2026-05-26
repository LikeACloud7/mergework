from __future__ import annotations

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty
from app.status import health_status, system_status


def test_health_status_reports_current_ledger_height(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        assert health_status(session) == {
            "ok": True,
            "service": "mergework",
            "ticker": "MRWK",
            "ledger_height": 0,
        }

        ensure_genesis(session)

        assert health_status(session)["ledger_height"] == 1


def test_system_status_counts_only_open_bounties(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=401,
            issue_url="https://github.com/ramimbo/mergework/issues/401",
            title="Open status bounty",
            reward_mrwk="25",
            acceptance="Status helpers should report open rows.",
        )
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=402,
            issue_url="https://github.com/ramimbo/mergework/issues/402",
            title="Paid status bounty",
            reward_mrwk="25",
            acceptance="Paid bounty should not count as active.",
        )
        pay_bounty(
            session,
            bounty_id=paid_bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/402",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        status = system_status(session)

    assert status["name"] == "MergeWork"
    assert status["ticker"] == "MRWK"
    assert status["genesis_supply_mrwk"] == "100000000"
    assert status["ledger_height"] == 4
    assert status["active_bounties"] == 1
    assert status["treasury_balance_mrwk"] == "99999950"
    assert status["future_path"] == "public snapshots, bridges, and onchain claims"
