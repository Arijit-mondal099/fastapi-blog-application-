"""Role demotion takes effect with the next access token."""

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.user import Role, User
from tests.helpers import complete_login, cookie_value, make_client


async def test_demoted_admin_loses_admin_route_after_access_expiry(
    app: FastAPI,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    login = await complete_login(client)
    assert login.status_code == 302
    refresh_token = cookie_value(login, "refresh_token")

    as_admin = await client.post("/test/admin-only")
    assert as_admin.status_code == 200

    monkeypatch.setenv("ACCESS_TOKEN_TTL_MINUTES", "0")
    get_settings.cache_clear()
    async with make_client(app, {"refresh_token": refresh_token}) as refresh_client:
        expired_pair = await refresh_client.post("/auth/refresh")
    assert expired_pair.status_code == 204
    expired_access = cookie_value(expired_pair, "access_token")
    refresh_token = cookie_value(expired_pair, "refresh_token")

    await asyncio.sleep(1.1)
    async with make_client(app, {"access_token": expired_access}) as expired_client:
        expired_response = await expired_client.post("/test/admin-only")
    assert expired_response.status_code == 401

    user = await db_session.scalar(select(User).where(User.email == "admin@example.com"))
    assert user is not None
    user.role = Role.reader
    await db_session.commit()

    monkeypatch.setenv("ACCESS_TOKEN_TTL_MINUTES", "15")
    get_settings.cache_clear()
    async with make_client(app, {"refresh_token": refresh_token}) as refresh_client:
        reader_pair = await refresh_client.post("/auth/refresh")
    assert reader_pair.status_code == 204

    reader_access = cookie_value(reader_pair, "access_token")
    async with make_client(app, {"access_token": reader_access}) as reader_client:
        denied = await reader_client.post("/test/admin-only")
    assert denied.status_code == 403
