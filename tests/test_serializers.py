from __future__ import annotations

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty, register_wallet
from app.models import Bounty
from app.serializers import (
    accepted_work_for_account,
    account_accepted_summary,
    bounty_list_summary,
    bounty_to_dict,
    wallet_to_dict,
)


def test_bounty_serializers_preserve_public_capacity_fields(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Refactor public serializers",
            reward_mrwk="25",
            max_awards=4,
            acceptance="Public capacity fields stay stable after extraction.",
        )

        bounty_data = bounty_to_dict(bounty)

    assert bounty_data["reward_mrwk"] == "25"
    assert bounty_data["available_mrwk"] == "100"
    assert bounty_data["reserved_mrwk"] == "100"
    assert bounty_data["awards_remaining"] == 4
    assert bounty_list_summary([bounty_data]) == {
        "bounties_shown": 1,
        "open_awards": 4,
        "open_pool_mrwk": "100",
    }


def test_account_and_wallet_serializers_preserve_public_shapes(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Refactor public serializers",
            reward_mrwk="40",
            acceptance="Accepted work summaries stay stable after extraction.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:tatelyman",
            submission_url="https://github.com/ramimbo/mergework/pull/320",
            accepted_by="ramimbo",
            verifier_result={"label": "mrwk:accepted"},
        )
        wallet = register_wallet(session, public_key_hex="1" * 64, label="Serializer wallet")
        session.flush()
        bounty_row = session.get(Bounty, bounty.id)
        assert bounty_row is not None

        summary = account_accepted_summary(session, "github:tatelyman")
        accepted_work = accepted_work_for_account(session, "github:tatelyman")
        wallet_data = wallet_to_dict(session, wallet)

    assert summary["accepted_awards"] == 1
    assert summary["accepted_mrwk"] == "40"
    assert summary["latest_submission_url"] == "https://github.com/ramimbo/mergework/pull/320"
    assert accepted_work[0]["issue_url"] == "https://github.com/ramimbo/mergework/issues/320"
    assert accepted_work[0]["amount_mrwk"] == "40"
    assert wallet_data["label"] == "Serializer wallet"
    assert wallet_data["balance_mrwk"] == "0"
    assert wallet_data["next_nonce"] == 1
