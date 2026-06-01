from __future__ import annotations

import re

from fastapi import HTTPException, Request

from app.control_chars import contains_control_character

CANONICAL_INTEGER_QUERY_RE = re.compile(r"^[0-9]+$")


def reject_control_char_query_param(request: Request, name: str) -> None:
    """Reject raw control characters before FastAPI coerces query values."""
    for value in request.query_params.getlist(name):
        if contains_control_character(value):
            raise HTTPException(
                status_code=400,
                detail=f"{name} must not contain control characters",
            )


def reject_noncanonical_int_query_param(request: Request, name: str) -> None:
    """Reject non-canonical integer query spellings before FastAPI coerces them."""
    for value in request.query_params.getlist(name):
        if contains_control_character(value):
            raise HTTPException(
                status_code=400,
                detail=f"{name} must not contain control characters",
            )
        if not CANONICAL_INTEGER_QUERY_RE.fullmatch(value):
            raise HTTPException(
                status_code=400,
                detail=f"{name} must be a canonical positive integer",
            )
