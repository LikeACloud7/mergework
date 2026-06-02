from __future__ import annotations

from typing import Any, Literal

SubmissionAvailability = Literal["open", "full", "closed", "unknown"]


def work_proof_submission_requirements(
    *,
    bounty_id: int | None,
    issue_number: int | None,
    availability: SubmissionAvailability,
    title: str | None = None,
    acceptance: str | None = None,
) -> dict[str, Any]:
    issue_ref = str(issue_number) if issue_number is not None else "<issue_number>"
    bounty_ref = str(bounty_id) if bounty_id is not None else "<bounty_id>"
    if _is_issue_submission(title=title, acceptance=acceptance):
        submission_mode = "issue"
        submission_url_kind = "github_issue"
        expected_artifact = "new proposed-work GitHub issue URL"
        attempt_endpoint_applicability = "not_required_for_issue_submission"
        reference_formats = [
            f"Bounty #{issue_ref}",
            f"Refs #{issue_ref}",
            f"Linked bounty: #{issue_ref}",
        ]
        claim_command = f"/claim #{issue_ref}"
        evidence_required = [
            "new proposed-work GitHub issue URL",
            "problem, evidence, proposed work, expected value, and acceptance notes",
            "duplicate search and out-of-scope notes",
        ]
        mode_actions = [
            {
                "id": "open_proposed_work_issue",
                "required": True,
                "text": "Open a concrete proposed-work issue before claiming this bounty.",
            },
            {
                "id": "link_bounty_issue",
                "required": True,
                "text": f"Link bounty #{issue_ref} from the proposed-work issue or bounty thread.",
            },
        ]
    else:
        submission_mode = "pr_or_evidence"
        submission_url_kind = "github_pr_or_public_evidence_url"
        expected_artifact = "focused PR, issue, report, or evidence URL"
        attempt_endpoint_applicability = "recommended_before_submission"
        reference_formats = [f"Bounty #{issue_ref}", f"Refs #{issue_ref}"]
        claim_command = "/claim"
        evidence_required = [
            "focused PR, issue, report, or evidence URL",
            "short verification summary",
            "tests, command output, screenshots, or reproduction steps when relevant",
        ]
        mode_actions = [
            {
                "id": "keep_scope_focused",
                "required": True,
                "text": "Keep changes directly tied to one bounty issue.",
            },
            {
                "id": "include_bounty_reference",
                "required": True,
                "text": f"Include Bounty #{issue_ref} or Refs #{issue_ref} in the submission.",
            },
        ]

    if availability == "open":
        first_action = {
            "id": "confirm_award_slot",
            "required": True,
            "text": "Confirm this bounty is open and has at least one award slot remaining.",
        }
    elif availability == "full":
        first_action = {
            "id": "watch_for_award_slot",
            "required": True,
            "text": (
                "This bounty is open but has no award slots remaining; check for new "
                "capacity before submitting new work."
            ),
        }
    elif availability == "closed":
        first_action = {
            "id": "choose_open_bounty",
            "required": True,
            "text": "Do not open or claim new work for this bounty unless a maintainer reopens it.",
        }
    else:
        first_action = {
            "id": "select_bounty",
            "required": True,
            "text": "Select a concrete open bounty before submitting work proof.",
        }

    return {
        "submission_mode": submission_mode,
        "submission_url_kind": submission_url_kind,
        "expected_artifact": expected_artifact,
        "attempt_endpoint_applicability": attempt_endpoint_applicability,
        "reference_formats": reference_formats,
        "claim_command": claim_command,
        "attempt_endpoint": f"/api/v1/bounties/{bounty_ref}/attempts",
        "evidence_required": evidence_required,
        "acceptance_trigger": "maintainer_mrwk_accepted_label_or_admin_payout",
        "public_metadata_must_avoid": [
            "private keys",
            "seed material",
            "secrets",
            "deployment credentials",
            "private vulnerability details",
            "price claims",
        ],
        "next_actions": [
            first_action,
            {
                "id": "check_duplicate_scope",
                "required": True,
                "text": "Confirm no active claim or duplicate PR already covers the same scope.",
            },
            *mode_actions,
            {
                "id": "include_review_evidence",
                "required": True,
                "text": "Include reviewable validation evidence before claiming.",
            },
            {
                "id": "wait_for_maintainer_acceptance",
                "required": True,
                "text": (
                    "Payment requires mrwk:accepted or an admin payout; merge or CI "
                    "alone is not acceptance."
                ),
            },
        ],
    }


def _is_issue_submission(*, title: str | None, acceptance: str | None) -> bool:
    text = f"{title or ''}\n{acceptance or ''}".casefold()
    return (
        "accepted work is a new proposed-work issue" in text
        or "proposed-work issue url as submission_url" in text
    )
