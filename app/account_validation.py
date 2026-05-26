from __future__ import annotations

import re

from fastapi import HTTPException

from app.ledger.service import TREASURY_ACCOUNT
from app.path_params import SQLITE_INTEGER_MAX
from app.wallets import WalletError, normalize_wallet_address

GITHUB_LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")


def normalized_account(account: str) -> str:
    if not account or not account.strip():
        raise HTTPException(status_code=400, detail="account must not be empty")
    if re.search(r"[\x00-\x1f\x7f]", account):
        raise HTTPException(status_code=400, detail="account must not contain control characters")
    clean = account.strip()
    lower = clean.lower()
    if lower == TREASURY_ACCOUNT:
        return TREASURY_ACCOUNT
    if lower.startswith("treasury:"):
        raise HTTPException(status_code=400, detail="treasury account must be treasury:mrwk")
    if lower.startswith("reserve:"):
        reserve_prefix = "reserve:bounty:"
        if not lower.startswith(reserve_prefix):
            raise HTTPException(
                status_code=400, detail="reserve account must use reserve:bounty:<id>"
            )
        bounty_id = lower.removeprefix(reserve_prefix)
        try:
            normalized_bounty_id = int(bounty_id) if bounty_id.isdigit() else 0
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="reserve bounty id is too large") from exc
        if normalized_bounty_id <= 0:
            raise HTTPException(status_code=400, detail="reserve bounty id must be positive")
        if normalized_bounty_id > SQLITE_INTEGER_MAX:
            raise HTTPException(status_code=400, detail="reserve bounty id is too large")
        return f"{reserve_prefix}{normalized_bounty_id}"
    if lower.startswith("mrwk1"):
        return normalized_wallet_address(clean)
    if lower.startswith("github:"):
        login = clean.split(":", 1)[1].lower()
        if not GITHUB_LOGIN_RE.fullmatch(login):
            raise HTTPException(status_code=400, detail="github login must be valid")
        return f"github:{login}"
    return clean


def github_login_from_account(account: str) -> str | None:
    if not account.startswith("github:"):
        return None
    login = account.removeprefix("github:")
    if not GITHUB_LOGIN_RE.fullmatch(login):
        return None
    return login


def normalized_wallet_address(address: str) -> str:
    try:
        return normalize_wallet_address(address)
    except WalletError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
