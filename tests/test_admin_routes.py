from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_login_callback_and_logout_routes_remain_registered(sqlite_url: str) -> None:
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )

    login = client.get("/admin/login", follow_redirects=False)
    callback = client.get("/admin/callback?code=abc&state=xyz", follow_redirects=False)
    client.cookies.set("mrwk_admin", "admin-cookie")
    client.cookies.set("mrwk_user", "user-cookie")
    logout = client.post("/admin/logout", follow_redirects=False)

    assert login.status_code == 302
    assert login.headers["location"] == "/auth/github/login?next=/admin"
    assert callback.status_code == 302
    assert callback.headers["location"] == "/auth/github/callback?code=abc&state=xyz"
    assert logout.status_code == 303
    assert logout.headers["location"] == "/"
    set_cookie = logout.headers.get_list("set-cookie")
    assert any(cookie.startswith("mrwk_admin=") and "Max-Age=0" in cookie for cookie in set_cookie)
    assert any(cookie.startswith("mrwk_user=") and "Max-Age=0" in cookie for cookie in set_cookie)


def test_admin_page_requires_oauth_config_before_redirect(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/admin", follow_redirects=False)

    assert response.status_code == 503
    assert response.json()["detail"] == "GitHub OAuth is not configured"
