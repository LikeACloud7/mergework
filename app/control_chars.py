"""Shared raw control-character validation helpers."""

from __future__ import annotations

import re

CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def contains_control_character(value: str) -> bool:
    return CONTROL_CHAR_RE.search(value) is not None
