from __future__ import annotations

import json
import subprocess

from scripts import submission_quality_gate
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


def test_submission_quality_gate_cli_returns_failure_exit(capsys, tmp_path) -> None:
    fixture = {
        "submission_text": "Summary: missing reference\n\nValidation: pytest passed",
        "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
        "pull_requests": [],
    }
    input_path = tmp_path / "submission.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8")

    assert main(["--input", str(input_path), "--format", "json"]) == 1

    assert json.loads(capsys.readouterr().out)["status"] == "fail"


def test_submission_quality_gate_live_mode_warns_when_github_unavailable(
    monkeypatch, capsys, tmp_path
) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["gh", "pr", "list"], timeout=30)

    monkeypatch.setattr(submission_quality_gate.subprocess, "run", fake_run)
    text_path = tmp_path / "draft.md"
    text_path.write_text(
        "Summary: work\n\nRefs #319\n\nValidation: pytest passed",
        encoding="utf-8",
    )

    assert main(["--text-file", str(text_path), "--format", "json"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "warn"
    assert "load_warning" in output


def test_submission_quality_gate_live_bounties_use_api_award_capacity(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        if args[:3] == ["gh", "issue", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps([{"number": 319, "title": "MRWK bounty: gate", "state": "OPEN"}]),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(submission_quality_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        submission_quality_gate,
        "_load_api_bounties",
        lambda repo, api_host: {
            319: {
                "number": 319,
                "state": "OPEN",
                "awards_remaining": 0,
            }
        },
    )

    data = submission_quality_gate._load_live_context(
        "ramimbo/mergework",
        "Summary: work\n\nRefs #319\n\nValidation: pytest passed",
        "https://api.example.test",
    )
    result = evaluate_submission(data)

    assert result["status"] == "fail"
    assert {
        "name": "bounty_payable",
        "status": "fail",
        "message": "referenced bounty #319 is closed or exhausted",
    } in result["checks"]


def test_submission_quality_gate_warns_when_live_payability_is_unverified(
    monkeypatch, capsys, tmp_path
) -> None:
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        if args[:3] == ["gh", "issue", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps([{"number": 319, "title": "MRWK bounty: gate", "state": "OPEN"}]),
                stderr="",
            )
        raise AssertionError(args)

    def fake_load_api_bounties(repo, api_host):
        raise RuntimeError("MergeWork API bounty data unavailable: offline")

    monkeypatch.setattr(submission_quality_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(submission_quality_gate, "_load_api_bounties", fake_load_api_bounties)
    text_path = tmp_path / "draft.md"
    text_path.write_text(
        "Summary: work\n\nRefs #319\n\nValidation: pytest passed",
        encoding="utf-8",
    )

    assert main(["--text-file", str(text_path), "--format", "json"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "warn"
    assert "load_warning" in output
    assert {
        "name": "bounty_payable",
        "status": "warn",
        "message": "referenced bounty #319 payability could not be verified",
    } in output["checks"]

    assert main(["--text-file", str(text_path), "--format", "text"]) == 0
    text_output = capsys.readouterr().out
    assert "Warning: MergeWork API bounty data unavailable: offline" in text_output
    assert "referenced bounty #319 payability could not be verified" in text_output
