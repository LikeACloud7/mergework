from __future__ import annotations

import json

from scripts.submission_quality_gate import evaluate_submission, main


def test_submission_quality_gate_passes_open_bounty_with_evidence(capsys, tmp_path) -> None:
    fixture = {
        "submission_text": """
        Summary:
        Add a focused pre-submission gate for agents.

        Refs #319

        Validation:
        - python -m pytest tests/test_submission_quality_gate.py -q -> 5 passed.
        """,
        "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 2}],
        "pull_requests": [],
    }

    result = evaluate_submission(fixture)

    assert result["status"] == "pass"
    assert result["bounty_reference"] == 319
    assert {check["name"]: check["status"] for check in result["checks"]} == {
        "bounty_reference": "pass",
        "bounty_payable": "pass",
        "summary_present": "pass",
        "evidence_present": "pass",
        "similar_open_pr": "pass",
    }

    input_path = tmp_path / "submission.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8")
    assert main(["--input", str(input_path), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


def test_submission_quality_gate_fails_missing_reference() -> None:
    result = evaluate_submission(
        {
            "submission_text": "Summary: add validation\n\nValidation: pytest passed",
            "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [],
        }
    )

    assert result["status"] == "fail"
    assert result["checks"][0] == {
        "name": "bounty_reference",
        "status": "fail",
        "message": "submission text must include Bounty #<issue> or Refs #<issue>",
    }


def test_submission_quality_gate_fails_closed_or_exhausted_bounty() -> None:
    result = evaluate_submission(
        {
            "submission_text": (
                "Summary: add validation\n\nBounty #319\n\nValidation: pytest passed"
            ),
            "bounties": [{"number": 319, "state": "CLOSED", "awards_remaining": 0}],
            "pull_requests": [],
        }
    )

    assert result["status"] == "fail"
    assert {
        "name": "bounty_payable",
        "status": "fail",
        "message": "referenced bounty #319 is closed or exhausted",
    } in result["checks"]


def test_submission_quality_gate_warns_for_missing_evidence() -> None:
    result = evaluate_submission(
        {
            "submission_text": "Summary: add validation\n\nRefs #319",
            "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [],
        }
    )

    assert result["status"] == "warn"
    assert {
        "name": "evidence_present",
        "status": "warn",
        "message": "include concrete test or validation evidence before submission",
    } in result["checks"]


def test_submission_quality_gate_warns_for_similar_open_pr() -> None:
    result = evaluate_submission(
        {
            "submission_text": """
            Summary: Add agent submission quality gate.
            Refs #319
            Validation: pytest passed.
            """,
            "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [
                {
                    "number": 12,
                    "title": "Add agent submission quality gate",
                    "body": "Refs #319",
                    "state": "OPEN",
                    "url": "https://github.com/ramimbo/mergework/pull/12",
                }
            ],
        }
    )

    assert result["status"] == "warn"
    assert result["similar_open_prs"] == [
        {
            "number": 12,
            "title": "Add agent submission quality gate",
            "url": "https://github.com/ramimbo/mergework/pull/12",
        }
    ]
