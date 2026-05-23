from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import create_schema, session_scope
from app.ledger.service import (
    GENESIS_SUPPLY_MICRO,
    TREASURY_ACCOUNT,
    create_bounty,
    ensure_genesis,
    format_mrwk,
    get_balance,
)
from app.models import Account, Bounty, LedgerEntry, Proof
from app.webhooks.github import handle_github_webhook

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def bounty_to_dict(bounty: Bounty) -> dict[str, Any]:
    return {
        "id": bounty.id,
        "repo": bounty.repo,
        "issue_number": bounty.issue_number,
        "issue_url": bounty.issue_url,
        "title": bounty.title,
        "reward_mrwk": format_mrwk(bounty.reward_microunits),
        "reserved_mrwk": format_mrwk(bounty.reserved_microunits),
        "status": bounty.status,
        "acceptance": bounty.acceptance,
        "created_at": bounty.created_at.isoformat(),
    }


def ledger_to_dict(entry: LedgerEntry) -> dict[str, Any]:
    return {
        "sequence": entry.sequence,
        "type": entry.entry_type,
        "from": entry.from_account,
        "to": entry.to_account,
        "amount_mrwk": format_mrwk(entry.amount_microunits),
        "reference": entry.reference,
        "previous_hash": entry.previous_hash,
        "entry_hash": entry.entry_hash,
        "created_at": entry.created_at.isoformat(),
    }


def _oauth_configured(settings: Settings) -> bool:
    return bool(
        settings.github_oauth_client_id
        and settings.github_oauth_client_secret
        and settings.cookie_secret
        and settings.admin_logins
    )


def _signed_value(value: str, secret: str) -> str:
    timestamp = str(int(time.time()))
    body = f"{value}|{timestamp}"
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}|{signature}"


def _verified_value(token: str | None, secret: str, max_age_seconds: int) -> str | None:
    if not token or not secret:
        return None
    try:
        value, timestamp, signature = token.rsplit("|", 2)
        age = int(time.time()) - int(timestamp)
    except ValueError:
        return None
    if age < 0 or age > max_age_seconds:
        return None
    expected = hmac.new(
        secret.encode(), f"{value}|{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return value


def create_app(database_url: str | None = None, webhook_secret: str | None = None) -> FastAPI:
    settings = get_settings()
    db_url = database_url or settings.database_url
    secret = webhook_secret if webhook_secret is not None else settings.github_webhook_secret
    create_schema(db_url)
    with session_scope(db_url) as session:
        ensure_genesis(session)

    app = FastAPI(title="MergeWork", version="0.1.0")
    app.state.database_url = db_url
    app.state.webhook_secret = secret
    app.state.settings = settings
    static_dir = BASE_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def admin_login_from_request(request: Request) -> str | None:
        token = request.headers.get("x-mergework-admin-token", "")
        if settings.admin_token and hmac.compare_digest(token, settings.admin_token):
            return "api-token"
        login = _verified_value(request.cookies.get("mrwk_admin"), settings.cookie_secret, 86_400)
        if login and login.lower() in settings.admin_logins:
            return login.lower()
        return None

    def require_admin(request: Request) -> str:
        login = admin_login_from_request(request)
        if login is None:
            raise HTTPException(status_code=401, detail="admin authentication required")
        return login

    @app.get("/health")
    def health() -> dict[str, Any]:
        with session_scope(db_url) as session:
            height = session.scalar(select(func.max(LedgerEntry.sequence))) or 0
        return {"ok": True, "service": "mergework", "ticker": "MRWK", "ledger_height": height}

    @app.get("/api/v1/status")
    def api_status() -> dict[str, Any]:
        with session_scope(db_url) as session:
            height = session.scalar(select(func.max(LedgerEntry.sequence))) or 0
            active = session.scalar(
                select(func.count()).select_from(Bounty).where(Bounty.status == "open")
            )
            treasury = get_balance(session, TREASURY_ACCOUNT)
        return {
            "name": "MergeWork",
            "ticker": "MRWK",
            "genesis_supply_mrwk": format_mrwk(GENESIS_SUPPLY_MICRO),
            "ledger_height": height,
            "active_bounties": active or 0,
            "treasury_balance_mrwk": format_mrwk(treasury),
            "future_path": "public snapshots, bridges, or onchain claims if the network grows",
        }

    @app.get("/api/v1/bounties")
    def api_bounties() -> list[dict[str, Any]]:
        with session_scope(db_url) as session:
            bounties = session.scalars(select(Bounty).order_by(Bounty.id.desc())).all()
            return [bounty_to_dict(bounty) for bounty in bounties]

    @app.post("/api/v1/bounties")
    async def api_create_bounty(
        request: Request, admin_login: str = Depends(require_admin)
    ) -> dict[str, Any]:
        data = await request.json()
        with session_scope(db_url) as session:
            bounty = create_bounty(
                session,
                repo=data["repo"],
                issue_number=int(data["issue_number"]),
                issue_url=data["issue_url"],
                title=data["title"],
                reward_mrwk=str(data["reward_mrwk"]),
                acceptance=data["acceptance"],
            )
            result = bounty_to_dict(bounty)
            result["created_by"] = admin_login
            return result

    @app.get("/api/v1/bounties/{bounty_id}")
    def api_bounty(bounty_id: int) -> dict[str, Any]:
        with session_scope(db_url) as session:
            bounty = session.get(Bounty, bounty_id)
            if bounty is None:
                raise HTTPException(status_code=404, detail="bounty not found")
            return bounty_to_dict(bounty)

    @app.get("/api/v1/accounts/{account:path}")
    def api_account(account: str) -> dict[str, Any]:
        with session_scope(db_url) as session:
            account_row = session.get(Account, account)
            return {
                "account": account,
                "exists": account_row is not None,
                "balance_mrwk": format_mrwk(get_balance(session, account)),
            }

    @app.get("/api/v1/ledger")
    def api_ledger(limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        with session_scope(db_url) as session:
            entries = session.scalars(
                select(LedgerEntry).order_by(LedgerEntry.sequence.desc()).limit(limit)
            ).all()
            return [ledger_to_dict(entry) for entry in entries]

    @app.get("/api/v1/proofs/{proof_hash}")
    def api_proof(proof_hash: str) -> dict[str, Any]:
        with session_scope(db_url) as session:
            proof = session.get(Proof, proof_hash)
            if proof is None:
                raise HTTPException(status_code=404, detail="proof not found")
            data = json.loads(proof.public_json)
            if not isinstance(data, dict):
                raise HTTPException(status_code=500, detail="invalid proof payload")
            return data

    @app.post("/webhooks/github")
    async def github_webhook(request: Request) -> JSONResponse:
        body = await request.body()
        headers = {key: value for key, value in request.headers.items()}
        normalized = {
            "X-GitHub-Delivery": headers.get("x-github-delivery", ""),
            "X-GitHub-Event": headers.get("x-github-event", ""),
            "X-Hub-Signature-256": headers.get("x-hub-signature-256", ""),
        }
        result = handle_github_webhook(db_url, normalized, body, secret)
        code = 401 if result["status"] == "unauthorized" else 200
        return JSONResponse(result, status_code=code)

    @app.post("/mcp")
    async def mcp(request: Request) -> dict[str, Any]:
        payload = await request.json()
        response_id = payload.get("id")
        method = payload.get("method")
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": response_id,
                "result": {
                    "tools": [
                        {"name": "list_bounties", "description": "List open MRWK bounties"},
                        {"name": "get_bounty", "description": "Get a bounty by id"},
                        {"name": "get_balance", "description": "Get an account balance"},
                        {"name": "get_ledger_entry", "description": "Get a ledger entry"},
                        {
                            "name": "submit_work_proof",
                            "description": "Return submission instructions",
                        },
                    ]
                },
            }
        if method != "tools/call":
            return {
                "jsonrpc": "2.0",
                "id": response_id,
                "error": {"code": -32601, "message": "unknown method"},
            }
        params = payload.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        if not isinstance(name, str):
            return {
                "jsonrpc": "2.0",
                "id": response_id,
                "error": {"code": -32602, "message": "tool name is required"},
            }
        text = _call_mcp_tool(db_url, name, args)
        return {
            "jsonrpc": "2.0",
            "id": response_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }

    @app.get("/", response_class=HTMLResponse)
    def hub(request: Request) -> HTMLResponse:
        status_data = api_status()
        return templates.TemplateResponse(
            request,
            "hub.html",
            {
                "status": status_data,
                "public_base_url": settings.public_base_url,
            },
        )

    @app.get("/bounties", response_class=HTMLResponse)
    def bounties_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "bounties.html", {"bounties": api_bounties()})

    @app.get("/bounties/{bounty_id}", response_class=HTMLResponse)
    def bounty_page(request: Request, bounty_id: int) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "bounty_detail.html", {"bounty": api_bounty(bounty_id)}
        )

    @app.get("/ledger", response_class=HTMLResponse)
    def ledger_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "ledger.html", {"entries": api_ledger()})

    @app.get("/accounts/{account:path}", response_class=HTMLResponse)
    def account_page(request: Request, account: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "account.html", {"account": api_account(account)}
        )

    @app.get("/proofs/{proof_hash}", response_class=HTMLResponse)
    def proof_page(request: Request, proof_hash: str) -> HTMLResponse:
        return templates.TemplateResponse(request, "proof.html", {"proof": api_proof(proof_hash)})

    @app.get("/docs", response_class=HTMLResponse)
    def docs_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "docs.html")

    @app.get("/admin/login")
    def admin_login() -> RedirectResponse:
        if not _oauth_configured(settings):
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        state = _signed_value(secrets.token_urlsafe(24), settings.cookie_secret)
        query = urlencode(
            {
                "client_id": settings.github_oauth_client_id,
                "redirect_uri": f"{settings.public_base_url}/admin/callback",
                "scope": "read:user",
                "state": state,
            }
        )
        response = RedirectResponse(
            f"https://github.com/login/oauth/authorize?{query}", status_code=302
        )
        response.set_cookie(
            "mrwk_oauth_state", state, httponly=True, secure=True, samesite="lax", max_age=600
        )
        return response

    @app.get("/admin/callback")
    async def admin_callback(request: Request, code: str, state: str) -> RedirectResponse:
        if not _oauth_configured(settings):
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        cookie_state = request.cookies.get("mrwk_oauth_state")
        if not cookie_state or not hmac.compare_digest(cookie_state, state):
            raise HTTPException(status_code=401, detail="invalid OAuth state")
        if _verified_value(state, settings.cookie_secret, 600) is None:
            raise HTTPException(status_code=401, detail="expired OAuth state")
        async with httpx.AsyncClient(timeout=10) as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.github_oauth_client_id,
                    "client_secret": settings.github_oauth_client_secret,
                    "code": code,
                    "redirect_uri": f"{settings.public_base_url}/admin/callback",
                },
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not access_token:
                raise HTTPException(status_code=401, detail="GitHub OAuth token exchange failed")
            user_response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                },
            )
            user_response.raise_for_status()
            login = str(user_response.json().get("login", "")).lower()
        if login not in settings.admin_logins:
            raise HTTPException(
                status_code=403, detail="GitHub login is not a MergeWork maintainer"
            )
        response = RedirectResponse("/admin", status_code=302)
        response.set_cookie(
            "mrwk_admin",
            _signed_value(login, settings.cookie_secret),
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=86_400,
        )
        response.delete_cookie("mrwk_oauth_state")
        return response

    @app.post("/admin/logout")
    def admin_logout() -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("mrwk_admin")
        return response

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request) -> Any:
        login = admin_login_from_request(request)
        if login is None:
            if _oauth_configured(settings):
                return RedirectResponse("/admin/login", status_code=302)
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        return templates.TemplateResponse(request, "admin.html", {"login": login})

    @app.post("/admin/bounties")
    def admin_create_bounty(
        request: Request,
        repo: str = Form(...),
        issue_number: int = Form(...),
        issue_url: str = Form(...),
        title: str = Form(...),
        reward_mrwk: str = Form(...),
        acceptance: str = Form(...),
        admin_login: str = Depends(require_admin),
    ) -> RedirectResponse:
        del request, admin_login
        with session_scope(db_url) as session:
            bounty = create_bounty(
                session,
                repo=repo,
                issue_number=issue_number,
                issue_url=issue_url,
                title=title,
                reward_mrwk=reward_mrwk,
                acceptance=acceptance,
            )
            bounty_id = bounty.id
        return RedirectResponse(f"/bounties/{bounty_id}", status_code=303)

    return app


def _call_mcp_tool(database_url: str, name: str, args: dict[str, Any]) -> str:
    with session_scope(database_url) as session:
        if name == "list_bounties":
            bounties = session.scalars(
                select(Bounty).where(Bounty.status == "open").order_by(Bounty.id.desc()).limit(25)
            ).all()
            return json.dumps([bounty_to_dict(bounty) for bounty in bounties])
        if name == "get_bounty":
            bounty = session.get(Bounty, int(args["id"]))
            if bounty is None:
                return "bounty not found"
            return json.dumps(bounty_to_dict(bounty))
        if name == "get_balance":
            account = str(args["account"])
            return f"{account}: {format_mrwk(get_balance(session, account))} MRWK"
        if name == "get_ledger_entry":
            entry = session.get(LedgerEntry, int(args["sequence"]))
            if entry is None:
                return "ledger entry not found"
            return json.dumps(ledger_to_dict(entry))
        if name == "submit_work_proof":
            return (
                "Open a focused PR or issue, reference the MRWK bounty, include test evidence, "
                "and wait for a maintainer to apply mrwk:accepted."
            )
    return "unknown tool"


app = create_app()
