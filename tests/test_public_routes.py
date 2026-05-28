from __future__ import annotations

from app.public_routes import public_bounties_context


def test_public_bounties_context_normalizes_filter_state() -> None:
    bounties = [
        {
            "id": 1,
            "status": "open",
            "awards_remaining": 2,
            "reward_mrwk": "25",
        }
    ]

    context = public_bounties_context(bounties, status=" OPEN ", q=" proof ", sort=" Reward ")

    assert context == {
        "bounties": bounties,
        "summary": {
            "bounties_shown": 1,
            "open_awards": 2,
            "open_pool_mrwk": "50",
        },
        "selected_status": "open",
        "query_text": "proof",
        "selected_sort": "reward",
        "sort_options": {
            "newest": "Newest first",
            "reward": "Highest per-award reward",
            "available": "Most MRWK available",
            "awards": "Most award slots",
        },
        "selected_limit": None,
        "limit_options": (10, 25, 50, 100, 200),
        "api_results_url": "/api/v1/bounties?status=open&q=proof&sort=reward",
    }


def test_public_bounties_context_preserves_limited_json_results_url() -> None:
    context = public_bounties_context([], status=None, q="issue #580", sort="newest", limit=25)

    assert context["api_results_url"] == "/api/v1/bounties?q=issue+%23580&limit=25"
