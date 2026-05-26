from __future__ import annotations

from app.mcp_work_proof import generic_work_proof_guidance_json, work_proof_submission_requirements


def test_work_proof_submission_requirements_choose_open_bounty_for_closed_state() -> None:
    requirements = work_proof_submission_requirements(
        bounty_id=7,
        issue_number=377,
        can_submit=False,
    )

    assert requirements["reference_formats"] == ["Bounty #377", "Refs #377"]
    assert requirements["attempt_endpoint"] == "/api/v1/bounties/7/attempts"
    assert requirements["next_actions"][0] == {
        "id": "choose_open_bounty",
        "required": True,
        "text": "Do not open or claim new work for this bounty unless a maintainer reopens it.",
    }
    assert "price claims" in requirements["public_metadata_must_avoid"]


def test_generic_work_proof_guidance_reuses_shared_submission_requirements() -> None:
    guidance = generic_work_proof_guidance_json()

    assert guidance["status"] == "generic_guidance"
    assert guidance["submission_requirements"]["reference_formats"] == [
        "Bounty #<issue_number>",
        "Refs #<issue_number>",
    ]
    assert guidance["submission_requirements"]["next_actions"][0]["id"] == "select_bounty"
    assert "private keys" in guidance["safety_rules"][0]
