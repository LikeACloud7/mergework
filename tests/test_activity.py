from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import add_ledger_entry, create_bounty, ensure_genesis, pay_bounty
from app.main import create_app


def test_activity_api_summarizes_proof_backed_bounty_payments(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        first_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=10,
            issue_url="https://github.com/ramimbo/mergework/issues/10",
            title="First activity bounty",
            reward_mrwk="25",
            max_awards=2,
            acceptance="Activity should count accepted bounty payments.",
        )
        second_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=11,
            issue_url="https://github.com/ramimbo/mergework/issues/11",
            title="Second activity bounty",
            reward_mrwk="40",
            acceptance="Activity should keep wallet and GitHub accounts separate.",
        )
        first_proof = pay_bounty(
            session,
            bounty_id=first_bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/10",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        second_proof = pay_bounty(
            session,
            bounty_id=first_bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/issues/10#issuecomment-1",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        wallet_proof = pay_bounty(
            session,
            bounty_id=second_bounty.id,
            to_account="mrwk1abc",
            submission_url="https://github.com/ramimbo/mergework/pull/11",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        add_ledger_entry(
            session,
            entry_type="bounty_payment",
            from_account="reserve:bounty:999",
            to_account="github:alice",
            amount_microunits=999_000000,
            reference="https://github.com/ramimbo/mergework/pull/unproved",
        )
        add_ledger_entry(
            session,
            entry_type="github_claim",
            from_account="github:alice",
            to_account="mrwk1abc",
            amount_microunits=25_000000,
            reference="claim",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/api/v1/activity")

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"] == {
        "accepted_awards": 3,
        "accepted_mrwk": "90",
        "contributors": 2,
    }
    assert payload["contributors"][0] == {
        "account": "github:alice",
        "accepted_awards": 2,
        "accepted_mrwk": "50",
        "latest_submission_url": "https://github.com/ramimbo/mergework/issues/10#issuecomment-1",
        "latest_proof_hash": second_proof.hash,
        "latest_proof_url": f"/proofs/{second_proof.hash}",
    }
    assert payload["contributors"][1]["account"] == "mrwk1abc"
    assert payload["contributors"][1]["accepted_mrwk"] == "40"
    assert payload["contributors"][1]["latest_proof_hash"] == wallet_proof.hash
    assert [row["proof_hash"] for row in payload["recent"]] == [
        wallet_proof.hash,
        second_proof.hash,
        first_proof.hash,
    ]
    assert payload["recent"][0]["bounty_issue_url"] == (
        "https://github.com/ramimbo/mergework/issues/11"
    )
    assert payload["recent"][0]["bounty_repo"] == "ramimbo/mergework"
    assert payload["recent"][0]["bounty_issue_number"] == 11
    assert all("unproved" not in row["submission_url"] for row in payload["recent"])


def test_activity_page_renders_empty_and_paid_states(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    empty = client.get("/activity")

    assert empty.status_code == 200
    assert "Accepted work activity" in empty.text
    assert "No accepted bounty payments yet." in empty.text

    with session_scope(sqlite_url) as session:
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=12,
            issue_url="https://github.com/ramimbo/mergework/issues/12",
            title="Activity page bounty",
            reward_mrwk="75",
            acceptance="Activity page should link accepted work proofs.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/12",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    paid = client.get("/activity")

    assert paid.status_code == 200
    assert "github:bob" in paid.text
    assert "75 MRWK" in paid.text
    assert 'href="https://github.com/ramimbo/mergework/issues/12"' in paid.text
    assert 'href="https://github.com/ramimbo/mergework/pull/12"' in paid.text
    assert f'href="/proofs/{proof.hash}"' in paid.text
    assert "/accounts/github:bob" in paid.text
