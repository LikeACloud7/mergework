"""Shared bounty list filtering helpers."""

from __future__ import annotations

from typing import Any

from app.control_chars import contains_control_character

BOUNTY_AVAILABILITY_FILTER_LABELS = {
    "all": "All availability",
    "effectively_open": "Effectively open",
}
BOUNTY_AVAILABILITY_FILTER_OPTIONS = tuple(BOUNTY_AVAILABILITY_FILTER_LABELS)
BOUNTY_AVAILABILITY_FILTER_ERROR = (
    f"availability must be one of: {', '.join(BOUNTY_AVAILABILITY_FILTER_OPTIONS)}"
)


def normalize_bounty_availability_filter(availability: str | None) -> str:
    raw_availability = availability or ""
    if contains_control_character(raw_availability):
        raise ValueError("availability must not contain control characters")
    normalized_availability = raw_availability.strip().lower()
    if not normalized_availability:
        return "all"
    if normalized_availability not in BOUNTY_AVAILABILITY_FILTER_OPTIONS:
        raise ValueError(BOUNTY_AVAILABILITY_FILTER_ERROR)
    return normalized_availability


def filter_bounties_by_availability(
    bounties: list[dict[str, Any]], availability: str | None
) -> list[dict[str, Any]]:
    normalized_availability = normalize_bounty_availability_filter(availability)
    if normalized_availability == "all":
        return bounties
    return [
        bounty
        for bounty in bounties
        if int(bounty.get("effective_awards_remaining", bounty["awards_remaining"])) > 0
    ]
