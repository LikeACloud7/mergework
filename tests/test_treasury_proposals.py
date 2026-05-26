from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth import signed_value
from app.db import session_scope
from app.ledger.service import (
    TREASURY_ACCOUNT,
    canonical_json,
    create_bounty,
    ensure_genesis,
    get_balance,
    pay_bounty,
    register_wallet,
    verify_hash_chain,
    verify_supply_conservation,
)
from app.main import create_app
from app.models import Bounty, TreasuryChallenge, TreasuryProposal, utc_now

ADMIN_HEADERS = {"x-mergework-admin-token": "admin-token-for-tests"}


def _client(sqlite_url: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    return TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )


def _bounty_payload(issue_number: int = 77, reward_mrwk: str = "25") -> dict[str, object]:
    return {
        "repo": "ramimbo/mergework",
        "issue_number": issue_number,
        "issue_url": f"https://github.com/ramimbo/mergework/issues/{issue_number}",
        "title": f"Governance proposal test {issue_number}",
        "reward_mrwk": reward_mrwk,
        "max_awards": 1,
        "acceptance": "Maintainer applies mrwk:accepted.",
    }


def _make_executable(sqlite_url: str, proposal_id: int) -> None:
    with session_scope(sqlite_url) as session:
        proposal = session.get(TreasuryProposal, proposal_id)
        assert proposal is not None
        proposal.executes_after = utc_now() - timedelta(seconds=1)


def test_admin_bounty_creation_creates_public_delayed_proposal(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)

    response = client.post("/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "treasury_proposal"
    assert body["action"] == "create_bounty"
    assert body["status"] == "pending"
    assert body["payload"]["repo"] == "ramimbo/mergework"
    assert body["payload"]["reward_mrwk"] == "25"
    assert body["executes_after"] > body["proposed_at"]
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(Bounty.id))) == 0
        assert get_balance(session, TREASURY_ACCOUNT) == 100_000_000_000_000

    listed = client.get("/api/v1/treasury/proposals")
    detail = client.get(f"/api/v1/treasury/proposals/{body['id']}")

    assert listed.status_code == 200
    assert [proposal["id"] for proposal in listed.json()] == [body["id"]]
    assert detail.status_code == 200
    assert detail.json()["payload_hash"] == body["payload_hash"]


def test_direct_proposal_creation_requires_admin_token(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    payload = {"action": "create_bounty", "payload": _bounty_payload(issue_number=78)}

    unauthenticated = client.post("/api/v1/treasury/proposals", json=payload)
    authorized = client.post("/api/v1/treasury/proposals", headers=ADMIN_HEADERS, json=payload)

    assert unauthenticated.status_code == 401
    assert authorized.status_code == 200
    body = authorized.json()
    assert body["action"] == "create_bounty"
    assert body["status"] == "pending"
    assert body["payload"]["issue_number"] == 78


def test_proposal_execution_requires_admin_delay_and_is_idempotent(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    proposal = client.post("/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload()).json()

    unauthenticated = client.post(f"/api/v1/treasury/proposals/{proposal['id']}/execute")
    too_early = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )
    _make_executable(sqlite_url, proposal["id"])
    executed = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )
    duplicate = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )

    assert unauthenticated.status_code == 401
    assert too_early.status_code == 400
    assert too_early.json()["detail"] == "proposal delay has not elapsed"
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed"
    assert executed.json()["result"]["bounty"]["issue_number"] == 77
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "proposal already executed"


def test_proposal_execution_rejects_payload_hash_mismatch(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    proposal = client.post("/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload()).json()
    tampered_payload = dict(proposal["payload"])
    tampered_payload["reward_mrwk"] = "500"
    with session_scope(sqlite_url) as session:
        stored = session.get(TreasuryProposal, proposal["id"])
        assert stored is not None
        stored.payload_json = canonical_json(tampered_payload)
        stored.executes_after = utc_now() - timedelta(seconds=1)

    response = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "proposal payload hash mismatch"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(Bounty.id))) == 0


def test_create_bounty_execution_enforces_epoch_reserve_cap(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=1,
            issue_url="https://github.com/ramimbo/mergework/issues/1",
            title="Existing large reserve",
            reward_mrwk="9000",
            acceptance="Accepted work.",
        )
    proposal = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json=_bounty_payload(issue_number=2, reward_mrwk="1001"),
    ).json()
    _make_executable(sqlite_url, proposal["id"])

    response = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "treasury epoch reserve cap exceeded"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(Bounty.id))) == 1


def test_manual_payout_creates_proposal_then_executes_after_delay(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=12,
            issue_url="https://github.com/ramimbo/mergework/issues/12",
            title="Manual payout proposal",
            reward_mrwk="15",
            acceptance="Contributor comments with proof.",
        )
        wallet = register_wallet(session, public_key_hex="11" * 32, label="Contributor")
        bounty_id = bounty.id
        wallet_address = wallet.address

    proposal = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": wallet_address,
            "submission_url": "https://github.com/ramimbo/mergework/issues/12#issuecomment-1",
            "accepted_by": "maintainer",
        },
    )

    assert proposal.status_code == 200
    assert proposal.json()["action"] == "pay_bounty"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, wallet_address) == 0

    _make_executable(sqlite_url, proposal.json()["id"])
    executed = client.post(
        f"/api/v1/treasury/proposals/{proposal.json()['id']}/execute", headers=ADMIN_HEADERS
    )

    assert executed.status_code == 200
    assert executed.json()["result"]["payout"]["to_account"] == wallet_address
    assert executed.json()["result"]["payout"]["proof_url"].startswith("/proofs/")
    with session_scope(sqlite_url) as session:
        assert get_balance(session, wallet_address) == 15_000_000
        assert verify_hash_chain(session) is True
        assert verify_supply_conservation(session) is True


def test_challenges_require_accepted_work_and_can_block_invalid_proposals(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        earned_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=20,
            issue_url="https://github.com/ramimbo/mergework/issues/20",
            title="Earned work seed",
            reward_mrwk="1",
            acceptance="Accepted proof.",
        )
        pay_bounty(
            session,
            bounty_id=earned_bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/20",
            accepted_by="maintainer",
            verifier_result={"source": "test"},
        )
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=21,
            issue_url="https://github.com/ramimbo/mergework/issues/21",
            title="Existing bounty",
            reward_mrwk="1",
            acceptance="Accepted proof.",
        )
    proposal = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json=_bounty_payload(issue_number=21, reward_mrwk="2"),
    ).json()

    unauthenticated = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/challenges",
        json={"challenge_type": "subjective_note", "reason": "Needs more explanation."},
    )
    client.cookies.set("mrwk_user", signed_value("bob", "test-cookie-secret"))
    unearned = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/challenges",
        json={"challenge_type": "subjective_note", "reason": "Needs more explanation."},
    )
    client.cookies.set("mrwk_user", signed_value("alice", "test-cookie-secret"))
    subjective = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/challenges",
        json={"challenge_type": "subjective_note", "reason": "Is this a good move?"},
    )
    blocking = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/challenges",
        json={"challenge_type": "duplicate_bounty", "reason": "Issue already has a bounty."},
    )
    _make_executable(sqlite_url, proposal["id"])
    execute = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )

    assert unauthenticated.status_code == 401
    assert unearned.status_code == 403
    assert unearned.json()["detail"] == "accepted MRWK work required to challenge proposals"
    assert subjective.status_code == 200
    assert subjective.json()["status"] == "noted"
    assert blocking.status_code == 200
    assert blocking.json()["status"] == "accepted_blocking"
    assert execute.status_code == 400
    assert execute.json()["detail"] == "proposal has blocking challenge"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryChallenge.id))) == 2
