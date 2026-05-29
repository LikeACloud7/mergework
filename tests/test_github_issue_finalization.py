from __future__ import annotations

import json
from typing import Any
from urllib.request import Request

from app.github_issue_finalization import finalize_created_bounty_issue


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._body).encode()


def test_finalize_created_bounty_issue_adds_label_and_claims_open_comment() -> None:
    requests: list[Request] = []

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        if request.full_url.endswith("/comments"):
            return _FakeResponse(
                {"html_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-1"}
            )
        return _FakeResponse({})

    result = finalize_created_bounty_issue(
        github_token="github-token",
        public_base_url="https://mrwk.example/",
        bounty={
            "id": 123,
            "repo": "ramimbo/mergework",
            "issue_number": 77,
        },
        opener=fake_opener,
    )

    assert result == {
        "status": "updated",
        "label": "mrwk:bounty",
        "bounty_url": "https://mrwk.example/bounties/123",
        "comment_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-1",
    }
    assert [request.get_method() for request in requests] == ["POST", "POST"]
    assert requests[0].full_url == (
        "https://api.github.com/repos/ramimbo/mergework/issues/77/labels"
    )
    assert requests[0].headers["Authorization"] == "Bearer github-token"
    assert json.loads((requests[0].data or b"").decode()) == {"labels": ["mrwk:bounty"]}
    assert requests[1].full_url == (
        "https://api.github.com/repos/ramimbo/mergework/issues/77/comments"
    )
    comment_body = json.loads((requests[1].data or b"").decode())["body"]
    assert "Reserved on MergeWork: https://mrwk.example/bounties/123" in comment_body
    assert "Claims are now open" in comment_body


def test_finalize_created_bounty_issue_skips_without_token() -> None:
    calls = 0

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        nonlocal calls
        calls += 1
        return _FakeResponse({})

    result = finalize_created_bounty_issue(
        github_token="",
        public_base_url="https://mrwk.example",
        bounty={"id": 123, "repo": "ramimbo/mergework", "issue_number": 77},
        opener=fake_opener,
    )

    assert result == {"status": "skipped", "reason": "github issue token not configured"}
    assert calls == 0
