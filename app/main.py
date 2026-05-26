from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select

from app.accounts import normalized_account, normalized_wallet_address, register_account_routes
from app.activity import register_activity_routes
from app.admin import (
    admin_page_context,
    create_admin_bounty_from_form,
)
from app.bounty_api import register_bounty_api_routes
from app.bounty_attempts import (
    list_bounty_attempts,
    register_bounty_attempt_routes,
)
from app.config import Settings, get_settings
from app.db import create_schema, session_scope
from app.hub import is_ltc_lab_host, ltc_lab_context, mergework_hub_context
from app.ledger.service import (
    LedgerError,
    ensure_genesis,
    format_mrwk,
    get_balance,
    link_wallet_to_github,
    public_url_or_none,
    register_wallet,
    submit_github_claim,
    submit_wallet_transfer,
)
from app.ledger_views import ledger_entry_to_dict, recent_ledger_entries
from app.mcp import handle_mcp_request
from app.mcp_work_proof import (
    generic_work_proof_guidance_json,
    work_proof_guidance,
    work_proof_guidance_json,
)
from app.me import me_page_context
from app.models import (
    Bounty,
    Proof,
    Wallet,
)
from app.path_params import (
    SQLITE_INTEGER_MAX,
    issue_number_search_value,
    positive_bounty_id,
    positive_ledger_sequence,
    proof_hash_from_path,
)
from app.public_routes import register_public_routes
from app.serializers import (
    bounty_awards_to_dict,
    bounty_list_summary,
    bounty_to_dict,
    ledger_to_dict,
    wallet_to_dict,
    wallet_transfer_to_dict,
)
from app.status import health_status, system_status
from app.webhooks.github import handle_github_webhook

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["safe_public_url"] = public_url_or_none

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
API_DOCS_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "connect-src 'self'; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
    "object-src 'none'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "worker-src 'self' blob:"
)
API_DOCS_PATHS = {"/api/docs", "/api/redoc"}


def _request_was_forwarded_https(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",", 1)[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _preserve_forwarded_https_redirect(request: Request, response: Response) -> None:
    if response.status_code not in {307, 308} or not _request_was_forwarded_https(request):
        return
    location = response.headers.get("location")
    if not location:
        return
    parsed = urlsplit(location)
    if parsed.scheme != "http" or parsed.netloc != request.url.netloc:
        return
    response.headers["location"] = urlunsplit(
        ("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment)
    )


def _oauth_configured(settings: Settings) -> bool:
    return bool(
        settings.github_oauth_client_id
        and settings.github_oauth_client_secret
        and settings.cookie_secret
    )


def _safe_next_path(next_path: str | None) -> str:
    decoded_next_path = unquote(next_path) if next_path else ""
    if (
        not next_path
        or not next_path.startswith("/")
        or next_path.startswith("//")
        or len(next_path) > 2048
        or "\\" in next_path
        or decoded_next_path.startswith("//")
        or len(decoded_next_path) > 2048
        or "\\" in decoded_next_path
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in next_path)
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in decoded_next_path)
    ):
        return "/me"
    return next_path


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


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid json body") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="json body must be an object")
    return data


def _required_str(data: dict[str, Any], field: str) -> str:
    if field not in data or data[field] is None:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    value = data[field]
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    return value


def _optional_str(data: dict[str, Any], field: str, default: str = "") -> str:
    value = data.get(field, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    return value


def _parse_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        clean = value.strip()
        if clean and clean.lstrip("+-").isdigit():
            try:
                return int(clean)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{field} must be an integer") from exc
    raise HTTPException(status_code=400, detail=f"{field} must be an integer")


def _required_int(data: dict[str, Any], field: str) -> int:
    value = data.get(field)
    if value is None:
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    return _parse_int(value, field)


def _optional_int(data: dict[str, Any], field: str, default: int) -> int:
    value = data.get(field, default)
    if value is None:
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    return _parse_int(value, field)


def _csrf_token(action: str, login: str, secret: str) -> str:
    return _signed_value(f"{action}:{login}", secret)


def _verify_csrf_token(
    token: str | None, *, action: str, login: str, secret: str, max_age_seconds: int = 3_600
) -> bool:
    expected = f"{action}:{login}"
    return _verified_value(token, secret, max_age_seconds) == expected


def create_app(database_url: str | None = None, webhook_secret: str | None = None) -> FastAPI:
    settings = get_settings()
    db_url = database_url or settings.database_url
    secret = webhook_secret if webhook_secret is not None else settings.github_webhook_secret
    create_schema(db_url)
    with session_scope(db_url) as session:
        ensure_genesis(session)

    app = FastAPI(
        title="MergeWork",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )
    app.state.database_url = db_url
    app.state.webhook_secret = secret
    app.state.settings = settings

    def post_only_route() -> None:
        raise HTTPException(
            status_code=405,
            detail="Method Not Allowed",
            headers={"Allow": "POST"},
        )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next: Any) -> Any:
        original_method = request.scope["method"]
        if original_method == "HEAD":
            request.scope["method"] = "GET"
        try:
            response = await call_next(request)
        finally:
            request.scope["method"] = original_method
        if original_method == "HEAD":
            headers = dict(response.headers)
            headers["content-length"] = "0"
            response = Response(
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
            )
        if request.url.path in API_DOCS_PATHS:
            response.headers["Content-Security-Policy"] = API_DOCS_CSP
        _preserve_forwarded_https_redirect(request, response)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

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

    def github_login_from_request(request: Request) -> str | None:
        login = _verified_value(request.cookies.get("mrwk_user"), settings.cookie_secret, 604_800)
        return login.lower() if login else None

    def require_github_login(request: Request) -> str:
        login = github_login_from_request(request)
        if login is None:
            raise HTTPException(status_code=401, detail="github login required")
        return login

    def require_admin(request: Request) -> str:
        login = admin_login_from_request(request)
        if login is None:
            raise HTTPException(status_code=401, detail="admin authentication required")
        return login

    def require_admin_token(request: Request) -> str:
        token = request.headers.get("x-mergework-admin-token", "")
        if settings.admin_token and hmac.compare_digest(token, settings.admin_token):
            return "api-token"
        raise HTTPException(status_code=401, detail="admin token required")

    @app.get("/health")
    def health() -> dict[str, Any]:
        with session_scope(db_url) as session:
            return health_status(session)

    @app.get("/api/v1/status")
    def api_status() -> dict[str, Any]:
        with session_scope(db_url) as session:
            return system_status(session)

    _bounty_api = register_bounty_api_routes(
        app,
        db_url=db_url,
        require_admin_token=require_admin_token,
        json_object=_json_object,
        required_str=_required_str,
        optional_str=_optional_str,
        optional_int=_optional_int,
        required_int=_required_int,
        settings=settings,
    )
    list_bounties_by_status = _bounty_api["list_bounties_by_status"]
    api_bounty = _bounty_api["get_bounty_detail"]

    register_bounty_attempt_routes(
        app,
        db_url=db_url,
        require_github_login=require_github_login,
        json_object=_json_object,
        required_str=_required_str,
        optional_int=_optional_int,
        normalized_account=normalized_account,
        positive_bounty_id=positive_bounty_id,
        sqlite_integer_max=SQLITE_INTEGER_MAX,
    )

    register_account_routes(app, db_url=db_url, templates=templates)

    @app.get("/api/v1/auth/me")
    def api_auth_me(request: Request) -> dict[str, Any]:
        login = github_login_from_request(request)
        return {"authenticated": login is not None, "github_login": login}

    @app.post("/api/v1/wallets/register")
    async def api_register_wallet(request: Request) -> dict[str, Any]:
        data = await _json_object(request)
        with session_scope(db_url) as session:
            try:
                wallet = register_wallet(
                    session,
                    public_key_hex=_required_str(data, "public_key_hex"),
                    label=_optional_str(data, "label") if data.get("label") is not None else None,
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return wallet_to_dict(session, wallet)

    @app.get("/api/v1/wallets/register", include_in_schema=False)
    def api_register_wallet_get() -> None:
        post_only_route()

    @app.get("/api/v1/wallets/link-github", include_in_schema=False)
    def api_link_wallet_github_get() -> None:
        post_only_route()

    @app.get("/api/v1/wallets/{address}")
    def api_wallet(address: str) -> dict[str, Any]:
        address = normalized_wallet_address(address)
        with session_scope(db_url) as session:
            wallet = session.get(Wallet, address)
            if wallet is None:
                raise HTTPException(status_code=404, detail="wallet not found")
            return wallet_to_dict(session, wallet)

    @app.post("/api/v1/wallets/link-github")
    async def api_link_wallet_github(
        request: Request, github_login: str = Depends(require_github_login)
    ) -> dict[str, Any]:
        data = await _json_object(request)
        with session_scope(db_url) as session:
            try:
                wallet = link_wallet_to_github(
                    session,
                    address=_required_str(data, "address"),
                    github_login=github_login,
                    nonce=_required_int(data, "nonce"),
                    signature_hex=_required_str(data, "signature_hex"),
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return wallet_to_dict(session, wallet)

    @app.post("/api/v1/github/claim")
    async def api_github_claim(
        request: Request, github_login: str = Depends(require_github_login)
    ) -> dict[str, Any]:
        data = await _json_object(request)
        with session_scope(db_url) as session:
            try:
                entry = submit_github_claim(
                    session,
                    address=_required_str(data, "address"),
                    github_login=github_login,
                    nonce=_required_int(data, "nonce"),
                    signature_hex=_required_str(data, "signature_hex"),
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return ledger_to_dict(entry)

    @app.post("/api/v1/transfers")
    async def api_submit_transfer(request: Request) -> dict[str, Any]:
        data = await _json_object(request)
        with session_scope(db_url) as session:
            try:
                transfer = submit_wallet_transfer(
                    session,
                    from_address=_required_str(data, "from_address"),
                    to_address=_required_str(data, "to_address"),
                    amount_mrwk=_required_str(data, "amount_mrwk"),
                    nonce=_required_int(data, "nonce"),
                    memo=_optional_str(data, "memo"),
                    signature_hex=_required_str(data, "signature_hex"),
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return wallet_transfer_to_dict(transfer)

    @app.get("/api/v1/ledger")
    def api_ledger(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict[str, Any]]:
        with session_scope(db_url) as session:
            return recent_ledger_entries(session, limit)

    @app.get("/api/v1/ledger/{sequence}")
    def api_ledger_entry(sequence: int) -> dict[str, Any]:
        sequence = positive_ledger_sequence(sequence)
        with session_scope(db_url) as session:
            entry = ledger_entry_to_dict(session, sequence)
            if entry is None:
                raise HTTPException(status_code=404, detail="ledger entry not found")
            return entry

    @app.get("/api/v1/proofs/{proof_hash}")
    def api_proof(proof_hash: str) -> dict[str, Any]:
        proof_hash = proof_hash_from_path(proof_hash)
        with session_scope(db_url) as session:
            proof = session.get(Proof, proof_hash)
            if proof is None:
                raise HTTPException(status_code=404, detail="proof not found")
            data = json.loads(proof.public_json)
            if not isinstance(data, dict):
                raise HTTPException(status_code=500, detail="invalid proof payload")
            return data

    register_activity_routes(app, db_url=db_url, templates=templates)

    @app.post("/webhooks/github")
    async def github_webhook(request: Request) -> JSONResponse:
        body = await request.body()
        headers = {key: value for key, value in request.headers.items()}
        normalized = {
            "X-GitHub-Delivery": headers.get("x-github-delivery", ""),
            "X-GitHub-Event": headers.get("x-github-event", ""),
            "X-Hub-Signature-256": headers.get("x-hub-signature-256", ""),
        }
        result = handle_github_webhook(
            db_url, normalized, body, secret, settings.github_accepted_labelers
        )
        code = 401 if result["status"] == "unauthorized" else 200
        return JSONResponse(result, status_code=code)

    @app.post("/mcp")
    async def mcp(request: Request) -> Any:
        return await handle_mcp_request(request, db_url, _call_mcp_tool)

    @app.get("/", response_class=HTMLResponse)
    def hub(request: Request) -> HTMLResponse:
        if is_ltc_lab_host(request.headers.get("host", "")):
            return templates.TemplateResponse(
                request,
                "ltc_lab.html",
                ltc_lab_context(),
            )
        status_data = api_status()
        return templates.TemplateResponse(
            request,
            "hub.html",
            mergework_hub_context(status_data, settings.public_base_url),
        )

    register_public_routes(
        app,
        db_url=db_url,
        templates=templates,
        list_bounties_by_status=list_bounties_by_status,
        api_bounty=api_bounty,
        api_ledger=api_ledger,
        api_ledger_entry=api_ledger_entry,
        api_proof=api_proof,
    )

    @app.get("/auth/github/login")
    def auth_github_login(next_path: str | None = Query(None, alias="next")) -> RedirectResponse:
        if not _oauth_configured(settings):
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        safe_next = _safe_next_path(next_path)
        state_value = f"{secrets.token_urlsafe(24)},{safe_next}"
        state = _signed_value(state_value, settings.cookie_secret)
        query = urlencode(
            {
                "client_id": settings.github_oauth_client_id,
                "redirect_uri": f"{settings.public_base_url}/auth/github/callback",
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

    @app.get("/auth/github/callback")
    async def auth_github_callback(request: Request, code: str, state: str) -> RedirectResponse:
        if not _oauth_configured(settings):
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        cookie_state = request.cookies.get("mrwk_oauth_state")
        if not cookie_state or not hmac.compare_digest(cookie_state, state):
            raise HTTPException(status_code=401, detail="invalid OAuth state")
        state_value = _verified_value(state, settings.cookie_secret, 600)
        if state_value is None:
            raise HTTPException(status_code=401, detail="expired OAuth state")
        try:
            _, next_path = state_value.split(",", 1)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="invalid OAuth state") from exc
        next_path = _safe_next_path(next_path)
        async with httpx.AsyncClient(timeout=10) as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.github_oauth_client_id,
                    "client_secret": settings.github_oauth_client_secret,
                    "code": code,
                    "redirect_uri": f"{settings.public_base_url}/auth/github/callback",
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
            if not login:
                raise HTTPException(status_code=401, detail="GitHub OAuth user lookup failed")
        response = RedirectResponse(next_path, status_code=302)
        response.set_cookie(
            "mrwk_user",
            _signed_value(login, settings.cookie_secret),
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=604_800,
        )
        if login in settings.admin_logins:
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

    @app.get("/admin/login")
    def admin_login() -> RedirectResponse:
        return RedirectResponse("/auth/github/login?next=/admin", status_code=302)

    @app.get("/admin/callback")
    async def admin_callback(request: Request) -> RedirectResponse:
        suffix = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/auth/github/callback{suffix}", status_code=302)

    @app.post("/auth/logout")
    def auth_logout() -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("mrwk_user")
        response.delete_cookie("mrwk_admin")
        return response

    @app.get("/me", response_class=HTMLResponse)
    def me_page(request: Request) -> HTMLResponse:
        login = github_login_from_request(request)
        with session_scope(db_url) as session:
            context = me_page_context(session, login)
        return templates.TemplateResponse(
            request,
            "me.html",
            context,
        )

    @app.post("/admin/logout")
    def admin_logout() -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("mrwk_admin")
        response.delete_cookie("mrwk_user")
        return response

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(
        request: Request,
        webhook_status: str | None = Query(None),
        webhook_limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> Any:
        login = admin_login_from_request(request)
        if login is None:
            if _oauth_configured(settings):
                return RedirectResponse("/auth/github/login?next=/admin", status_code=302)
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        with session_scope(db_url) as session:
            context = admin_page_context(
                session,
                login=login,
                csrf_token=_csrf_token("admin-bounty", login, settings.cookie_secret),
                webhook_status=webhook_status,
                webhook_limit=webhook_limit,
            )
        return templates.TemplateResponse(
            request,
            "admin.html",
            context,
        )

    @app.post("/admin/bounties")
    def admin_create_bounty(
        request: Request,
        repo: str = Form(...),
        issue_number: int = Form(...),
        issue_url: str = Form(...),
        title: str = Form(...),
        reward_mrwk: str = Form(...),
        max_awards: int = Form(1),
        acceptance: str = Form(...),
        csrf_token: str | None = Form(None),
        admin_login: str = Depends(require_admin),
    ) -> RedirectResponse:
        del request
        if admin_login != "api-token" and not _verify_csrf_token(
            csrf_token,
            action="admin-bounty",
            login=admin_login,
            secret=settings.cookie_secret,
        ):
            raise HTTPException(status_code=403, detail="invalid CSRF token")
        with session_scope(db_url) as session:
            try:
                bounty_id = create_admin_bounty_from_form(
                    session,
                    repo=repo,
                    issue_number=issue_number,
                    issue_url=issue_url,
                    title=title,
                    reward_mrwk=reward_mrwk,
                    max_awards=max_awards,
                    acceptance=acceptance,
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(f"/bounties/{bounty_id}", status_code=303)

    return app


def _call_mcp_tool(database_url: str, name: str, args: dict[str, Any]) -> str | dict[str, Any]:
    def int_arg(field: str) -> int:
        value = args[field]
        if isinstance(value, bool):
            raise ValueError(f"{field} must be an integer")
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str):
            clean = value.strip()
            if clean and clean.lstrip("+-").isdigit():
                try:
                    parsed = int(clean)
                except ValueError as exc:
                    raise ValueError(f"{field} must be an integer") from exc
            else:
                raise ValueError(f"{field} must be an integer")
        else:
            raise ValueError(f"{field} must be an integer")
        if parsed < -SQLITE_INTEGER_MAX - 1 or parsed > SQLITE_INTEGER_MAX:
            raise ValueError(f"{field} is too large")
        return parsed

    def positive_int_arg(field: str) -> int:
        value = int_arg(field)
        if value <= 0:
            raise ValueError(f"{field} must be positive")
        return value

    def str_arg(field: str, *, allow_empty: bool = False) -> str:
        value = args[field]
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        if not allow_empty and value == "":
            raise ValueError(f"{field} must not be empty")
        return value

    def optional_str_arg(field: str, default: str = "") -> str:
        value = args.get(field, default)
        if value is None:
            return default
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        return value

    def optional_clean_str_arg(field: str) -> str | None:
        value = args.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        clean = value.strip()
        return clean or None

    def output_format_arg() -> str:
        value = args.get("format", "text")
        if value is None:
            return "text"
        if not isinstance(value, str):
            raise ValueError("format must be a string")
        normalized = value.strip().lower()
        if normalized not in {"text", "json"}:
            raise ValueError("format must be text or json")
        return normalized

    def optional_repo_selector_arg() -> str | None:
        repo = optional_clean_str_arg("repo")
        if repo is None:
            return None
        if len(repo) > 200:
            raise ValueError("repo is too long")
        return repo.lower()

    def list_limit_arg(default: int = 25) -> int:
        if "limit" not in args or args.get("limit") is None:
            return default
        value = positive_int_arg("limit")
        if value > 100:
            raise ValueError("limit must be at most 100")
        return value

    def optional_bool_arg(field: str, default: bool = False) -> bool:
        value = args.get(field, default)
        if value is None:
            return default
        if not isinstance(value, bool):
            raise ValueError(f"{field} must be a boolean")
        return value

    with session_scope(database_url) as session:
        if name == "list_bounties":
            status = optional_clean_str_arg("status") or "open"
            normalized_status = status.lower()
            if normalized_status not in {"open", "paid", "closed"}:
                raise ValueError("status must be one of: open, paid, closed")
            query = select(Bounty).where(Bounty.status == normalized_status)
            query_text = optional_clean_str_arg("q")
            if query_text:
                escaped_query = (
                    query_text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                like_query = f"%{escaped_query}%"
                issue_number = issue_number_search_value(query_text)
                text_filter = or_(
                    func.lower(Bounty.repo).like(like_query, escape="\\"),
                    func.lower(Bounty.title).like(like_query, escape="\\"),
                    func.lower(Bounty.acceptance).like(like_query, escape="\\"),
                )
                if issue_number is not None:
                    text_filter = or_(text_filter, Bounty.issue_number == issue_number)
                query = query.where(text_filter)
            bounties = session.scalars(
                query.order_by(Bounty.id.desc()).limit(list_limit_arg())
            ).all()
            return json.dumps([bounty_to_dict(bounty) for bounty in bounties])
        if name == "get_bounty":
            bounty = session.get(Bounty, positive_int_arg("id"))
            if bounty is None:
                return "bounty not found"
            bounty_data = bounty_to_dict(bounty)
            if optional_bool_arg("include_awards"):
                bounty_data["awards"] = bounty_awards_to_dict(session, bounty.id)
            return json.dumps(bounty_data)
        if name == "list_bounty_attempts":
            bounty_id = positive_int_arg("bounty_id")
            bounty = session.get(Bounty, bounty_id)
            if bounty is None:
                return "bounty not found"
            attempt_listing = list_bounty_attempts(
                session,
                bounty,
                include_expired=optional_bool_arg("include_expired"),
                limit=list_limit_arg(),
            )
            return {
                "bounty_id": bounty_id,
                "issue_number": bounty.issue_number,
                "status": bounty.status,
                "warnings": attempt_listing["warnings"],
                "attempts": attempt_listing["attempts"],
            }
        if name == "get_balance":
            account = normalized_account(str_arg("account"))
            return f"{account}: {format_mrwk(get_balance(session, account))} MRWK"
        if name == "register_wallet":
            wallet = register_wallet(
                session,
                public_key_hex=str_arg("public_key_hex"),
                label=optional_str_arg("label") if args.get("label") is not None else None,
            )
            return json.dumps(wallet_to_dict(session, wallet))
        if name == "get_wallet":
            wallet_row = session.get(Wallet, normalized_wallet_address(str_arg("address")))
            if wallet_row is None:
                return "wallet not found"
            return json.dumps(wallet_to_dict(session, wallet_row))
        if name == "submit_wallet_transfer":
            transfer = submit_wallet_transfer(
                session,
                from_address=str_arg("from_address"),
                to_address=str_arg("to_address"),
                amount_mrwk=str_arg("amount_mrwk"),
                nonce=int_arg("nonce"),
                memo=optional_str_arg("memo"),
                signature_hex=str_arg("signature_hex"),
            )
            return json.dumps(wallet_transfer_to_dict(transfer))
        if name == "get_ledger_entry":
            entry = ledger_entry_to_dict(session, positive_int_arg("sequence"))
            if entry is None:
                return "ledger entry not found"
            return json.dumps(entry)
        if name == "get_proof":
            proof = session.get(Proof, proof_hash_from_path(str_arg("hash")))
            if proof is None:
                return "proof not found"
            public_payload = json.loads(proof.public_json)
            if not isinstance(public_payload, dict):
                raise ValueError("invalid proof payload")
            return json.dumps(
                {
                    "hash": proof.hash,
                    "kind": proof.kind,
                    "ledger_sequence": proof.ledger_sequence,
                    "bounty_id": proof.bounty_id,
                    "submission_id": proof.submission_id,
                    "created_at": proof.created_at.isoformat(),
                    "proof": public_payload,
                }
            )
        if name == "submit_work_proof":
            output_format = output_format_arg()
            has_bounty_id = "bounty_id" in args and args.get("bounty_id") is not None
            has_issue_number = "issue_number" in args and args.get("issue_number") is not None
            repo_selector = optional_repo_selector_arg()
            if has_bounty_id and has_issue_number:
                raise ValueError("use bounty_id or issue_number, not both")
            if repo_selector is not None and not has_issue_number:
                raise ValueError("repo can only be used with issue_number")
            if has_bounty_id:
                bounty = session.get(Bounty, positive_int_arg("bounty_id"))
                if bounty is None:
                    return "bounty not found"
                return (
                    work_proof_guidance_json(bounty)
                    if output_format == "json"
                    else work_proof_guidance(bounty)
                )
            if has_issue_number:
                issue_query = select(Bounty).where(
                    Bounty.issue_number == positive_int_arg("issue_number")
                )
                if repo_selector is not None:
                    issue_query = issue_query.where(Bounty.repo == repo_selector)
                bounties = session.scalars(issue_query.order_by(Bounty.id.desc()).limit(2)).all()
                if not bounties:
                    return "bounty not found"
                if len(bounties) > 1:
                    raise ValueError("issue_number matches multiple bounties")
                return (
                    work_proof_guidance_json(bounties[0])
                    if output_format == "json"
                    else work_proof_guidance(bounties[0])
                )
            if output_format == "json":
                return generic_work_proof_guidance_json()
            return (
                "Open a focused PR or issue, reference the MRWK bounty, include test evidence, "
                "and wait for a maintainer to apply mrwk:accepted."
            )
    raise ValueError("unknown tool")


app = create_app()
