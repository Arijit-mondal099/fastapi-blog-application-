"""Logout cookie clearing and family revocation."""

from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.security import create_access_token, decode_refresh_token
from app.models.user import Role
from tests.helpers import complete_login, cookie_value, make_client, set_cookie_header


async def test_logout_with_expired_access_revokes_family_and_clears_cookies(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    login = await complete_login(client)
    refresh_token = cookie_value(login, "refresh_token")
    claims = decode_refresh_token(refresh_token, get_settings())

    now = datetime.now(UTC)
    expired_access = create_access_token(
        user_id=claims.user_id,
        role=Role.reader,
        family_id=claims.family_id,
        settings=get_settings(),
        issued_at=now - timedelta(hours=1),
        expires_at=now - timedelta(minutes=1),
    )

    async with make_client(app, {"access_token": expired_access}) as logout_client:
        response = await logout_client.post("/auth/logout")

    assert response.status_code == 204
    assert "Max-Age=0" in set_cookie_header(response, "access_token")
    assert "Max-Age=0" in set_cookie_header(response, "refresh_token")

    async with make_client(app, {"refresh_token": refresh_token}) as refresh_client:
        refresh = await refresh_client.post("/auth/refresh")
    assert refresh.status_code == 401


async def test_logout_without_or_with_invalid_access_still_succeeds(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    login = await complete_login(client)
    refresh_token = cookie_value(login, "refresh_token")

    for cookies in ({}, {"access_token": "not-a-valid-jwt"}):
        async with make_client(app, cookies) as logout_client:
            response = await logout_client.post("/auth/logout")
        assert response.status_code == 204
        assert "Max-Age=0" in set_cookie_header(response, "access_token")
        assert "Max-Age=0" in set_cookie_header(response, "refresh_token")

    async with make_client(app, {"refresh_token": refresh_token}) as refresh_client:
        refresh = await refresh_client.post("/auth/refresh")
    assert refresh.status_code == 204
