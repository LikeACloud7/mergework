from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.json_payloads import optional_int, optional_str, required_int, required_str


def _detail(exc_info: pytest.ExceptionInfo[HTTPException]) -> str:
    return str(exc_info.value.detail)


def test_json_payload_string_helpers_preserve_required_and_type_errors() -> None:
    assert required_str({"title": "Bounty"}, "title") == "Bounty"
    assert optional_str({}, "source", "github") == "github"
    assert optional_str({"source": None}, "source", "github") == "github"

    with pytest.raises(HTTPException) as missing:
        required_str({}, "title")
    with pytest.raises(HTTPException) as typed:
        optional_str({"source": 1}, "source")

    assert _detail(missing) == "title is required"
    assert _detail(typed) == "source must be a string"


def test_json_payload_integer_helpers_preserve_string_and_control_char_errors() -> None:
    assert required_int({"nonce": " 42 "}, "nonce") == 42
    assert optional_int({}, "ttl_seconds", 3600) == 3600

    with pytest.raises(HTTPException) as boolean:
        required_int({"nonce": True}, "nonce")
    with pytest.raises(HTTPException) as decimal:
        required_int({"nonce": "1.5"}, "nonce")
    with pytest.raises(HTTPException) as controlled:
        optional_int({"ttl_seconds": "\x851"}, "ttl_seconds", 3600)

    assert _detail(boolean) == "nonce must be an integer"
    assert _detail(decimal) == "nonce must be an integer"
    assert _detail(controlled) == "ttl_seconds must not contain control characters"
