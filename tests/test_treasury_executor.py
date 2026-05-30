from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, get_balance, register_wallet
from app.models import Bounty, TreasuryProposal, utc_now
from app.treasury import canonical_json, proposal_result, propose_treasury_action
from app.treasury_executor import execute_due_treasury_proposals


def _create_bounty_payload(issue_number: int, reward_mrwk: str = "25") -> dict[str, object]:
    return {
        "repo": "ramimbo/mergework",
        "issue_number": issue_number,
        "issue_url": f"https://github.com/ramimbo/mergework/issues/{issue_number}",
        "title": f"Executor test bounty {issue_number}",
        "reward_mrwk": reward_mrwk,
        "max_awards": 1,
        "acceptance": "Accepted executor test work.",
    }


def _make_due(sqlite_url: str, proposal_id: int) -> None:
    with session_scope(sqlite_url) as session:
        proposal = session.get(TreasuryProposal, proposal_id)
        assert proposal is not None
        proposal.executes_after = utc_now() - timedelta(seconds=1)


def _init_db(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)


def test_executor_executes_due_create_bounty_and_finalizes_issue(sqlite_url: str) -> None:
    _init_db(sqlite_url)
    finalizer_calls: list[dict[str, object]] = []

    def fake_finalizer(
        *,
        github_token: str,
        public_base_url: str,
        bounty: dict[str, object],
    ) -> dict[str, object]:
        finalizer_calls.append(
            {
                "github_token": github_token,
                "public_base_url": public_base_url,
                "bounty": bounty,
            }
        )
        return {
            "status": "updated",
            "label": "mrwk:bounty",
            "comment_url": "https://github.com/ramimbo/mergework/issues/90#issuecomment-1",
        }

    with session_scope(sqlite_url) as session:
        due = propose_treasury_action(
            session,
            action="create_bounty",
            payload=_create_bounty_payload(90),
            proposed_by="maintainer",
        )
        future = propose_treasury_action(
            session,
            action="create_bounty",
            payload=_create_bounty_payload(91),
            proposed_by="maintainer",
        )
        due_id = int(due.id)
        future_id = int(future.id)
    _make_due(sqlite_url, due_id)

    report = execute_due_treasury_proposals(
        sqlite_url,
        github_issue_token="github-issue-token",
        public_base_url="https://mrwk.example",
        executed_by="treasury-executor",
        finalizer=fake_finalizer,
    )

    assert report["attempted"] == 1
    assert report["executed"] == 1
    assert report["failed"] == 0
    assert report["results"][0]["proposal_id"] == due_id
    assert report["results"][0]["status"] == "executed"
    assert report["results"][0]["github_issue_finalization"]["status"] == "updated"
    assert len(finalizer_calls) == 1
    assert finalizer_calls[0]["github_token"] == "github-issue-token"
    assert finalizer_calls[0]["public_base_url"] == "https://mrwk.example"

    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(Bounty.id))) == 1
        due_proposal = session.get(TreasuryProposal, due_id)
        future_proposal = session.get(TreasuryProposal, future_id)
        assert due_proposal is not None
        assert future_proposal is not None
        assert due_proposal.status == "executed"
        assert due_proposal.executed_by == "treasury-executor"
        assert proposal_result(due_proposal)["github_issue_finalization"]["status"] == "updated"
        assert future_proposal.status == "pending"

    second_report = execute_due_treasury_proposals(
        sqlite_url,
        github_issue_token="github-issue-token",
        public_base_url="https://mrwk.example",
        executed_by="treasury-executor",
        finalizer=fake_finalizer,
    )
    assert second_report["attempted"] == 0


def test_executor_executes_due_manual_payout(sqlite_url: str) -> None:
    _init_db(sqlite_url)
    with session_scope(sqlite_url) as session:
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=92,
            issue_url="https://github.com/ramimbo/mergework/issues/92",
            title="Executor payout bounty",
            reward_mrwk="15",
            acceptance="Contributor comments with proof.",
        )
        wallet = register_wallet(session, public_key_hex="22" * 32, label="Contributor")
        proposal = propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": bounty.id,
                "to_account": wallet.address,
                "submission_url": "https://github.com/ramimbo/mergework/issues/92#issuecomment-1",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )
        proposal_id = int(proposal.id)
        wallet_address = wallet.address
    _make_due(sqlite_url, proposal_id)

    report = execute_due_treasury_proposals(
        sqlite_url,
        github_issue_token="",
        public_base_url="https://mrwk.example",
        executed_by="treasury-executor",
    )

    assert report["attempted"] == 1
    assert report["executed"] == 1
    assert report["failed"] == 0
    assert report["results"][0]["action"] == "pay_bounty"
    assert report["results"][0]["result"]["payout"]["proof_url"].startswith("/proofs/")
    with session_scope(sqlite_url) as session:
        assert get_balance(session, wallet_address) == 15_000_000


def test_executor_continues_after_failed_due_proposal(sqlite_url: str) -> None:
    _init_db(sqlite_url)
    with session_scope(sqlite_url) as session:
        bad = propose_treasury_action(
            session,
            action="create_bounty",
            payload=_create_bounty_payload(93),
            proposed_by="maintainer",
        )
        good = propose_treasury_action(
            session,
            action="create_bounty",
            payload=_create_bounty_payload(94),
            proposed_by="maintainer",
        )
        bad_id = int(bad.id)
        good_id = int(good.id)
    _make_due(sqlite_url, bad_id)
    _make_due(sqlite_url, good_id)
    with session_scope(sqlite_url) as session:
        bad_proposal = session.get(TreasuryProposal, bad_id)
        assert bad_proposal is not None
        tampered_payload = dict(_create_bounty_payload(93))
        tampered_payload["reward_mrwk"] = "500"
        bad_proposal.payload_json = canonical_json(tampered_payload)

    report = execute_due_treasury_proposals(
        sqlite_url,
        github_issue_token="",
        public_base_url="https://mrwk.example",
        executed_by="treasury-executor",
    )

    assert report["attempted"] == 2
    assert report["executed"] == 1
    assert report["failed"] == 1
    assert report["results"][0] == {
        "proposal_id": bad_id,
        "action": "create_bounty",
        "status": "failed",
        "error": "proposal payload hash mismatch",
    }
    assert report["results"][1]["proposal_id"] == good_id
    assert report["results"][1]["status"] == "executed"
    with session_scope(sqlite_url) as session:
        bad_proposal = session.get(TreasuryProposal, bad_id)
        good_proposal = session.get(TreasuryProposal, good_id)
        assert bad_proposal is not None
        assert good_proposal is not None
        assert bad_proposal.status == "pending"
        assert good_proposal.status == "executed"
