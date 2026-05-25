from __future__ import annotations

from app.activity import (
    accepted_work_for_account,
    account_accepted_summary,
    activity_to_dict,
    empty_accepted_summary,
    safe_accepted_work_for_account,
    safe_account_accepted_summary,
)
from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty
from app.models import Proof


class BrokenSession:
    def execute(self, *args, **kwargs):
        raise RuntimeError("database unavailable")


def test_activity_helper_fallbacks_keep_account_schema() -> None:
    assert empty_accepted_summary() == {
        "accepted_awards": 0,
        "accepted_mrwk": "0",
        "latest_ledger_sequence": None,
        "latest_submission_url": None,
        "latest_proof_hash": None,
        "latest_proof_url": None,
    }
    assert (
        safe_account_accepted_summary(BrokenSession(), "github:alice") == empty_accepted_summary()
    )
    assert safe_accepted_work_for_account(BrokenSession(), "github:alice") == []


def test_activity_helpers_skip_malformed_public_proofs(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Extract accepted activity helpers",
            reward_mrwk="40",
            acceptance="Malformed proof payloads should not break activity helpers.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/328",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        proof_row = session.get(Proof, proof.hash)
        assert proof_row is not None
        proof_row.public_json = "{"

        activity = activity_to_dict(session)
        summary = account_accepted_summary(session, "github:alice")
        accepted_work = accepted_work_for_account(session, "github:alice")

    assert activity == {
        "totals": {"accepted_awards": 0, "accepted_mrwk": "0", "contributors": 0},
        "query": "",
        "contributors": [],
        "recent": [],
    }
    assert summary == empty_accepted_summary()
    assert accepted_work == []
