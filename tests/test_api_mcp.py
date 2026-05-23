from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis
from app.main import create_app


def test_health_status_and_bounty_api(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=1,
            issue_url="https://github.com/ramimbo/mergework/issues/1",
            title="First bounty",
            reward_mrwk="75",
            acceptance="Accepted label",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    assert client.get("/health").json()["ok"] is True
    status = client.get("/api/v1/status").json()
    assert status["ticker"] == "MRWK"
    assert status["ledger_height"] == 2
    assert status["active_bounties"] == 1
    bounties = client.get("/api/v1/bounties").json()
    assert bounties[0]["title"] == "First bounty"


def test_mcp_tools_list_and_call(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
    assert tools["result"]["tools"][0]["name"] == "list_bounties"

    balance = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_balance", "arguments": {"account": "treasury:mrwk"}},
        },
    ).json()
    assert balance["result"]["content"][0]["type"] == "text"
    assert "100000000" in balance["result"]["content"][0]["text"]
