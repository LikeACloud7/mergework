from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

GITHUB_LABEL = "mrwk:bounty"
USER_AGENT = "MergeWork"
API_VERSION = "2022-11-28"


def _github_request(url: str, github_token: str, payload: dict[str, Any]) -> Request:
    return Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode(),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        },
    )


def _read_json(response: Any) -> dict[str, Any]:
    body = response.read()
    if not body:
        return {}
    data = json.loads(body.decode())
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _post_json(
    *,
    opener: Callable[..., Any],
    url: str,
    github_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    request = _github_request(url, github_token, payload)
    with opener(request, timeout=10) as response:
        return _read_json(response)


def _bounty_issue_target(bounty: dict[str, object]) -> tuple[str, int, int] | None:
    try:
        bounty_id = int(str(bounty.get("id", "")))
        issue_number = int(str(bounty.get("issue_number", "")))
    except (TypeError, ValueError):
        return None
    repo = str(bounty.get("repo", "")).strip().lower()
    repo_parts = repo.split("/")
    if bounty_id <= 0 or issue_number <= 0 or len(repo_parts) != 2 or not all(repo_parts):
        return None
    return repo, issue_number, bounty_id


def finalize_created_bounty_issue(
    *,
    github_token: str,
    public_base_url: str,
    bounty: dict[str, object],
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    clean_token = github_token.strip()
    if not clean_token:
        return {"status": "skipped", "reason": "github issue token not configured"}
    target = _bounty_issue_target(bounty)
    if target is None:
        return {"status": "failed", "reason": "bounty issue target missing or invalid"}
    repo, issue_number, bounty_id = target
    owner_part, name_part = repo.split("/", 1)
    owner = quote(owner_part, safe="")
    name = quote(name_part, safe="")
    issue_api_base = f"https://api.github.com/repos/{owner}/{name}/issues/{issue_number}"
    bounty_url = f"{public_base_url.rstrip('/')}/bounties/{bounty_id}"
    comment = (
        f"Reserved on MergeWork: {bounty_url}\n\n"
        "Claims are now open for accepted work that matches this bounty's criteria."
    )
    try:
        _post_json(
            opener=opener,
            url=f"{issue_api_base}/labels",
            github_token=clean_token,
            payload={"labels": [GITHUB_LABEL]},
        )
        comment_response = _post_json(
            opener=opener,
            url=f"{issue_api_base}/comments",
            github_token=clean_token,
            payload={"body": comment},
        )
    except HTTPError as exc:
        return {"status": "failed", "reason": f"github issue update failed: HTTP {exc.code}"}
    except (OSError, ValueError) as exc:
        return {"status": "failed", "reason": f"github issue update failed: {type(exc).__name__}"}
    return {
        "status": "updated",
        "label": GITHUB_LABEL,
        "bounty_url": bounty_url,
        "comment_url": comment_response.get("html_url"),
    }
