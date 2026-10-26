"""OAuth login and callback behavior."""

import base64
import hashlib
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import get_settings
from tests.helpers import (
    FakeGoogleClient,
    complete_login,
    make_client,
    set_cookie_header,
    start_login,
    state_from_redirect,
)


async def test_missing_oauth_state_returns_400(client: httpx.AsyncClient) -> None:
    await start_login(client)

    response = await client.get("/auth/google/callback", params={"code": "test-code"})

    assert response.status_code == 400
    assert "Max-Age=0" in set_cookie_header(response, "oauth_state")


async def test_wrong_oauth_state_returns_400(client: httpx.AsyncClient) -> None:
    await start_login(client)

    response = await client.get(
        "/auth/google/callback",
        params={"code": "test-code", "state": "not-the-state"},
    )

    assert response.status_code == 400
    assert "Max-Age=0" in set_cookie_header(response, "oauth_state")


async def test_missing_oauth_state_cookie_returns_400(app: FastAPI) -> None:
    async with make_client(app) as test_client:
        response = await test_client.get(
            "/auth/google/callback",
            params={"code": "test-code", "state": "some-state"},
        )

    assert response.status_code == 400


async def test_unverified_google_email_returns_403(
    client: httpx.AsyncClient, fake_google: FakeGoogleClient
) -> None:
    fake_google.identity = replace(
        fake_google.identity,
        email_verified=False,
    )

    response = await complete_login(client)

    assert response.status_code == 403
    assert "Max-Age=0" in set_cookie_header(response, "oauth_state")


async def test_successful_login_sets_cookies_and_redirects(
    client: httpx.AsyncClient, fake_google: FakeGoogleClient
) -> None:
    login = await client.get("/auth/google/login")
    assert login.status_code == 302
    state_cookie = set_cookie_header(login, "oauth_state")
    assert "Path=/auth/google" in state_cookie
    assert "HttpOnly" in state_cookie
    assert "Secure" in state_cookie
    assert "SameSite=lax" in state_cookie

    state = state_from_redirect(login)
    callback = await client.get(
        "/auth/google/callback",
        params={"code": "test-code", "state": state},
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "https://frontend.test/"
    assert fake_google.last_challenge is not None
    assert fake_google.last_code_verifier is not None
    digest = hashlib.sha256(fake_google.last_code_verifier.encode("ascii")).digest()
    expected_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert fake_google.last_challenge == expected_challenge

    access_header = set_cookie_header(callback, "access_token")
    refresh_header = set_cookie_header(callback, "refresh_token")
    assert "Path=/" in access_header
    assert "HttpOnly" in access_header
    assert "Secure" in access_header
    assert "SameSite=strict" in access_header
    assert "Path=/auth/refresh" in refresh_header
    assert "HttpOnly" in refresh_header
    assert "Secure" in refresh_header
    assert "SameSite=strict" in refresh_header
    assert "Max-Age=0" in set_cookie_header(callback, "oauth_state")

    me = await client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "admin@example.com"
    assert body["role"] == "admin"


async def test_callback_uses_configured_post_login_path(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POST_LOGIN_PATH", "/welcome")
    get_settings.cache_clear()

    callback = await complete_login(client)

    assert callback.status_code == 302
    assert callback.headers["location"] == "https://frontend.test/welcome"
