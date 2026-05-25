from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from difflib import SequenceMatcher
from typing import Any

BOUNTY_REF_RE = re.compile(r"(?:bounty|refs?|fixes|closes)\s+#(\d+)", re.IGNORECASE)
EVIDENCE_RE = re.compile(
    r"\b(pytest|ruff|mypy|validation|verified|test evidence|checks? passed)\b",
    re.IGNORECASE,
)
SUMMARY_RE = re.compile(r"\b(summary|what changed|changes?)\b", re.IGNORECASE)


def _check(name: str, status: str, message: str) -> dict[str, str]:
    return {"name": name, "status": status, "message": message}


def _bounty_refs(text: str) -> list[int]:
    return sorted({int(match) for match in BOUNTY_REF_RE.findall(text)})


def _bounty_is_payable(raw: dict[str, Any]) -> bool:
    if str(raw.get("state") or "").lower() not in {"", "open"}:
        return False
    remaining = raw.get("awards_remaining", raw.get("awardsRemaining"))
    if remaining is None:
        return True
    try:
        return int(remaining) > 0
    except (TypeError, ValueError):
        return False


def _title_from_submission(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip(" -:\t")
        if not clean:
            continue
        if SUMMARY_RE.search(clean) and len(clean.split()) <= 4:
            continue
        if BOUNTY_REF_RE.search(clean) or EVIDENCE_RE.search(clean):
            continue
        return " ".join(clean.lower().split())
    return ""


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left.lower(), right.lower()).ratio()


def _has_evidence(text: str) -> bool:
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        if SUMMARY_RE.search(clean) and ":" in clean:
            continue
        if EVIDENCE_RE.search(clean):
            return True
    return False


def _matching_pr_bounty_refs(pr: dict[str, Any]) -> list[int]:
    text = "\n".join(str(pr.get(key) or "") for key in ("title", "body"))
    return _bounty_refs(text)


def _similar_open_prs(
    pull_requests: list[dict[str, Any]], bounty_ref: int | None, submission_title: str
) -> list[dict[str, Any]]:
    if bounty_ref is None or not submission_title:
        return []
    matches: list[dict[str, Any]] = []
    for pr in pull_requests:
        if str(pr.get("state") or "OPEN").lower() not in {"open", "opened"}:
            continue
        if bounty_ref not in _matching_pr_bounty_refs(pr):
            continue
        title = str(pr.get("title") or "")
        if _similarity(submission_title, title) < 0.78:
            continue
        matches.append(
            {
                "number": pr.get("number"),
                "title": title,
                "url": pr.get("url"),
            }
        )
    return matches


def evaluate_submission(data: dict[str, Any]) -> dict[str, Any]:
    text = str(data.get("submission_text") or "")
    bounties = {
        int(item["number"]): item
        for item in data.get("bounties", [])
        if isinstance(item, dict) and isinstance(item.get("number"), int)
    }
    pull_requests = [item for item in data.get("pull_requests", []) if isinstance(item, dict)]
    checks: list[dict[str, str]] = []
    refs = _bounty_refs(text)
    bounty_ref = refs[0] if refs else None
    if bounty_ref is None:
        checks.append(
            _check(
                "bounty_reference",
                "fail",
                "submission text must include Bounty #<issue> or Refs #<issue>",
            )
        )
    else:
        checks.append(_check("bounty_reference", "pass", f"found bounty reference #{bounty_ref}"))
        bounty = bounties.get(bounty_ref)
        if bounty is None:
            checks.append(
                _check(
                    "bounty_payable",
                    "warn",
                    f"referenced bounty #{bounty_ref} was not available in input",
                )
            )
        elif _bounty_is_payable(bounty):
            checks.append(
                _check("bounty_payable", "pass", f"referenced bounty #{bounty_ref} is open")
            )
        else:
            checks.append(
                _check(
                    "bounty_payable",
                    "fail",
                    f"referenced bounty #{bounty_ref} is closed or exhausted",
                )
            )

    if SUMMARY_RE.search(text):
        checks.append(_check("summary_present", "pass", "summary text found"))
    else:
        checks.append(_check("summary_present", "warn", "include a concise summary of the work"))

    if _has_evidence(text):
        checks.append(_check("evidence_present", "pass", "test or validation evidence found"))
    else:
        checks.append(
            _check(
                "evidence_present",
                "warn",
                "include concrete test or validation evidence before submission",
            )
        )

    similar = _similar_open_prs(pull_requests, bounty_ref, _title_from_submission(text))
    if similar:
        checks.append(
            _check(
                "similar_open_pr",
                "warn",
                "similar open PRs already reference this bounty",
            )
        )
    else:
        checks.append(_check("similar_open_pr", "pass", "no similar open PRs found"))

    if any(check["status"] == "fail" for check in checks):
        status = "fail"
    elif any(check["status"] == "warn" for check in checks):
        status = "warn"
    else:
        status = "pass"
    return {
        "status": status,
        "bounty_reference": bounty_ref,
        "checks": checks,
        "similar_open_prs": similar,
    }


def _run_gh_json(args: list[str]) -> Any:
    completed = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return json.loads(completed.stdout)


def _load_live_context(repo: str, submission_text: str) -> dict[str, Any]:
    try:
        prs = _run_gh_json(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--limit",
                "100",
                "--json",
                "number,title,url,body,state",
            ]
        )
        issues = _run_gh_json(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "all",
                "--limit",
                "200",
                "--json",
                "number,title,state",
            ]
        )
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as exc:
        return {
            "submission_text": submission_text,
            "bounties": [],
            "pull_requests": [],
            "load_warning": f"live GitHub data unavailable: {exc}",
        }
    bounties = [
        {
            "number": issue["number"],
            "title": issue.get("title"),
            "state": issue.get("state"),
            "awards_remaining": 1 if issue.get("state") == "OPEN" else 0,
        }
        for issue in issues
        if "bounty" in str(issue.get("title", "")).lower()
    ]
    return {"submission_text": submission_text, "bounties": bounties, "pull_requests": prs}


def _load_input(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("quality gate input must be a JSON object")
    return data


def format_text(result: dict[str, Any]) -> str:
    lines = [f"Submission quality gate: {result['status'].upper()}"]
    if result.get("bounty_reference") is not None:
        lines.append(f"Bounty reference: #{result['bounty_reference']}")
    for check in result["checks"]:
        lines.append(f"- {check['status'].upper()} {check['name']}: {check['message']}")
    if result["similar_open_prs"]:
        lines.append("Similar open PRs:")
        for pr in result["similar_open_prs"]:
            lines.append(f"- #{pr['number']}: {pr['title']} {pr.get('url') or ''}".rstrip())
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a MergeWork bounty submission draft.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Read gate input from a JSON fixture file.")
    source.add_argument("--text-file", help="Read submission text and live context with gh.")
    parser.add_argument("--repo", default="ramimbo/mergework")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args(argv)

    if args.input:
        data = _load_input(args.input)
    else:
        with open(args.text_file, encoding="utf-8") as handle:
            data = _load_live_context(args.repo, handle.read())
    result = evaluate_submission(data)
    if data.get("load_warning"):
        result["load_warning"] = data["load_warning"]

    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_text(result))
    return 1 if result["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
