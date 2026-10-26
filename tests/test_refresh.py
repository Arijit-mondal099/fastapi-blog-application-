"""Refresh rotation and reuse detection."""

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decode_refresh_token
from app.models.refresh_token import RefreshToken
from tests.helpers import complete_login, cookie_value, make_client


async def test_refresh_rotates_and_old_token_is_rejected(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    login = await complete_login(client)
    assert login.status_code == 302
    old_refresh = cookie_value(login, "refresh_token")

    async with make_client(app, {"refresh_token": old_refresh}) as refresh_client:
        rotated = await refresh_client.post("/auth/refresh")
    assert rotated.status_code == 204
    new_refresh = cookie_value(rotated, "refresh_token")
    assert new_refresh != old_refresh
    assert cookie_value(rotated, "access_token")

    async with make_client(app, {"refresh_token": new_refresh}) as refresh_client:
        second_rotation = await refresh_client.post("/auth/refresh")
    assert second_rotation.status_code == 204

    async with make_client(app, {"refresh_token": old_refresh}) as reuse_client:
        rejected = await reuse_client.post("/auth/refresh")
    assert rejected.status_code == 401


async def test_reuse_of_revoked_token_revokes_entire_family(
    app: FastAPI,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    login = await complete_login(client)
    first = cookie_value(login, "refresh_token")
    family_id = decode_refresh_token(first, get_settings()).family_id

    async with make_client(app, {"refresh_token": first}) as refresh_client:
        second_response = await refresh_client.post("/auth/refresh")
    second = cookie_value(second_response, "refresh_token")

    async with make_client(app, {"refresh_token": second}) as refresh_client:
        third_response = await refresh_client.post("/auth/refresh")
    third = cookie_value(third_response, "refresh_token")

    async with make_client(app, {"refresh_token": first}) as reuse_client:
        reuse = await reuse_client.post("/auth/refresh")
    assert reuse.status_code == 401

    async with make_client(app, {"refresh_token": third}) as newest_client:
        newest = await newest_client.post("/auth/refresh")
    assert newest.status_code == 401

    rows = (
        await db_session.scalars(select(RefreshToken).where(RefreshToken.family_id == family_id))
    ).all()
    assert rows
    assert all(row.revoked_at is not None for row in rows)
