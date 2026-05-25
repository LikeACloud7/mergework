from __future__ import annotations

from app.activity import empty_accepted_summary, safe_accepted_work_for_account


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
    assert safe_accepted_work_for_account(BrokenSession(), "github:alice") == []
