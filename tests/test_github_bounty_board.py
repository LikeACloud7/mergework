from __future__ import annotations

import json
from typing import Any
from urllib.request import Request

from app.db import create_schema, session_scope
from app.github_bounty_board import (
    BOUNTY_BOARD_BLOCK_END,
    BOUNTY_BOARD_BLOCK_START,
    refresh_bounty_board_issue,
    render_bounty_board,
    update_bounty_board_issue,
)
from app.ledger.service import create_bounty, ensure_genesis
from app.treasury import propose_treasury_action


class _FakeResponse:
    def __init__(self, body: Any) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._body).encode()


def test_render_bounty_board_separates_claimable_pending_and_unavailable() -> None:
    body = render_bounty_board(
        open_bounties=[
            {
                "id": 1,
                "issue_number": 10,
                "issue_url": "https://github.com/ramimbo/mergework/issues/10",
                "title": "MRWK bounty: 75 MRWK - useful verification",
                "reward_mrwk": "75",
                "effective_awards_remaining": 3,
                "availability_note": "3 awards effectively available.",
                "availability_state": "open",
            },
            {
                "id": 2,
                "issue_number": 11,
                "issue_url": "https://github.com/ramimbo/mergework/issues/11",
                "title": "MRWK bounty: 40 MRWK - full review round",
                "reward_mrwk": "40",
                "effective_awards_remaining": 0,
                "availability_note": "No awards remain available for new submissions.",
                "availability_state": "full",
            },
        ],
        treasury_status={
            "pending_create_bounties": [
                {
                    "proposal_id": 44,
                    "issue_number": 12,
                    "issue_url": "https://github.com/ramimbo/mergework/issues/12",
                    "title": "MRWK bounty: 150 MRWK - focused fixes",
                    "reward_mrwk": "150",
                    "max_awards": 2,
                    "reserve_mrwk": "300",
                    "executes_after": "2026-06-02T14:48:17Z",
                }
            ],
            "available_create_reserve_mrwk": "2550",
            "next_projected_capacity_release_at": "2026-06-02T07:42:42Z",
        },
        checked_at="2026-06-02T05:38:44Z",
    )

    assert body.startswith(BOUNTY_BOARD_BLOCK_START)
    assert body.rstrip().endswith(BOUNTY_BOARD_BLOCK_END)
    assert "This board issue itself is not a bounty." in body
    assert "| [#10](https://github.com/ramimbo/mergework/issues/10) | useful verification |" in body
    assert "| [#11](https://github.com/ramimbo/mergework/issues/11) | full review round |" in body
    assert "## Claimable Now" in body
    assert "## Open But Not Currently Claimable" in body
    assert "## Opening Soon" in body
    assert "These are pending `create_bounty` proposals. They are not claimable" in body
    assert "| [#12](https://github.com/ramimbo/mergework/issues/12) | focused fixes |" in body
    assert "Available create reserve: 2550 MRWK." in body
    assert "Next projected capacity release: 2026-06-02T07:42:42Z." in body


def test_update_bounty_board_issue_replaces_marker_block() -> None:
    requests: list[Request] = []
    existing_body = "\n".join(
        [
            "Intro stays.",
            "",
            BOUNTY_BOARD_BLOCK_START,
            "old board",
            BOUNTY_BOARD_BLOCK_END,
            "",
            "Footer stays.",
        ]
    )

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        if request.get_method() == "GET":
            return _FakeResponse({"body": existing_body})
        return _FakeResponse({})

    result = update_bounty_board_issue(
        github_token="github-token",
        repo="ramimbo/mergework",
        issue_number=785,
        board_body=f"{BOUNTY_BOARD_BLOCK_START}\nnew board\n{BOUNTY_BOARD_BLOCK_END}",
        opener=fake_opener,
    )

    assert result == {"status": "updated", "issue_number": 785}
    assert [request.get_method() for request in requests] == ["GET", "PATCH"]
    payload = json.loads((requests[1].data or b"").decode())
    assert payload["body"] == "\n".join(
        [
            "Intro stays.",
            "",
            BOUNTY_BOARD_BLOCK_START,
            "new board",
            BOUNTY_BOARD_BLOCK_END,
            "",
            "Footer stays.",
        ]
    )


def test_update_bounty_board_issue_noops_when_current() -> None:
    requests: list[Request] = []
    board_body = f"{BOUNTY_BOARD_BLOCK_START}\ncurrent\n{BOUNTY_BOARD_BLOCK_END}"

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        return _FakeResponse({"body": board_body})

    result = update_bounty_board_issue(
        github_token="github-token",
        repo="ramimbo/mergework",
        issue_number=785,
        board_body=board_body,
        opener=fake_opener,
    )

    assert result == {"status": "already_current", "issue_number": 785}
    assert [request.get_method() for request in requests] == ["GET"]


def test_update_bounty_board_issue_noops_when_only_state_timestamp_changed() -> None:
    requests: list[Request] = []
    existing_body = "\n".join(
        [
            BOUNTY_BOARD_BLOCK_START,
            "Displayed state updated: 2026-06-02T05:38:44Z",
            "",
            "same live rows",
            BOUNTY_BOARD_BLOCK_END,
        ]
    )
    board_body = "\n".join(
        [
            BOUNTY_BOARD_BLOCK_START,
            "Displayed state updated: 2026-06-02T05:39:44Z",
            "",
            "same live rows",
            BOUNTY_BOARD_BLOCK_END,
        ]
    )

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        return _FakeResponse({"body": existing_body})

    result = update_bounty_board_issue(
        github_token="github-token",
        repo="ramimbo/mergework",
        issue_number=785,
        board_body=board_body,
        opener=fake_opener,
    )

    assert result == {"status": "already_current", "issue_number": 785}
    assert [request.get_method() for request in requests] == ["GET"]


def test_update_bounty_board_issue_skips_without_token_or_issue() -> None:
    calls = 0

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        nonlocal calls
        calls += 1
        return _FakeResponse({})

    assert update_bounty_board_issue(
        github_token="",
        repo="ramimbo/mergework",
        issue_number=785,
        board_body=f"{BOUNTY_BOARD_BLOCK_START}\nboard\n{BOUNTY_BOARD_BLOCK_END}",
        opener=fake_opener,
    ) == {"status": "skipped", "reason": "github issue token not configured"}
    assert update_bounty_board_issue(
        github_token="github-token",
        repo="ramimbo/mergework",
        issue_number=None,
        board_body=f"{BOUNTY_BOARD_BLOCK_START}\nboard\n{BOUNTY_BOARD_BLOCK_END}",
        opener=fake_opener,
    ) == {"status": "skipped", "reason": "bounty board issue number not configured"}
    assert calls == 0


def test_refresh_bounty_board_issue_uses_open_bounties_and_pending_creates(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    requests: list[Request] = []

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=10,
            issue_url="https://github.com/ramimbo/mergework/issues/10",
            title="MRWK bounty: 75 MRWK - useful verification",
            reward_mrwk="75",
            max_awards=2,
            acceptance="Useful public verification reports.",
        )
        propose_treasury_action(
            session,
            action="create_bounty",
            payload={
                "repo": "ramimbo/mergework",
                "issue_number": 11,
                "issue_url": "https://github.com/ramimbo/mergework/issues/11",
                "title": "MRWK bounty: 150 MRWK - focused fixes",
                "reward_mrwk": "150",
                "max_awards": 1,
                "acceptance": "Focused fixes with tests.",
            },
            proposed_by="maintainer",
        )

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        if request.get_method() == "GET":
            return _FakeResponse({"body": "Board intro."})
        return _FakeResponse({})

    result = refresh_bounty_board_issue(
        sqlite_url,
        github_token="github-token",
        public_base_url="https://mrwk.example",
        issue_number=785,
        opener=fake_opener,
    )

    assert result == {"status": "updated", "issue_number": 785}
    patch_payload = json.loads((requests[1].data or b"").decode())
    updated_body = patch_payload["body"]
    assert "Board intro." in updated_body
    assert "[#10](https://github.com/ramimbo/mergework/issues/10)" in updated_body
    assert "useful verification" in updated_body
    assert "[#11](https://github.com/ramimbo/mergework/issues/11)" in updated_body
    assert "focused fixes" in updated_body
