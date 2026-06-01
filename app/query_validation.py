from __future__ import annotations

from fastapi import HTTPException, Request

from app.control_chars import contains_control_character


def reject_control_char_query_param(request: Request, name: str) -> None:
    """Reject raw control characters before FastAPI coerces query values."""
    for value in request.query_params.getlist(name):
        if contains_control_character(value):
            raise HTTPException(
                status_code=400,
                detail=f"{name} must not contain control characters",
            )
