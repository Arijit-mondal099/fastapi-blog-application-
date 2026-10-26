"""User upsert, bootstrap role, and profile refresh behavior."""

from dataclasses import replace

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from tests.helpers import FakeGoogleClient, complete_login


async def test_first_login_outside_admin_emails_creates_reader(
    client: httpx.AsyncClient, fake_google: FakeGoogleClient
) -> None:
    fake_google.identity = replace(fake_google.identity, email="reader@example.com")

    login = await complete_login(client)
    assert login.status_code == 302

    me = await client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "reader"


async def test_login_updates_email_but_keeps_google_identity_and_role(
    client: httpx.AsyncClient,
    fake_google: FakeGoogleClient,
    db_session: AsyncSession,
) -> None:
    first_login = await complete_login(client)
    assert first_login.status_code == 302
    first_profile = (await client.get("/auth/me")).json()

    fake_google.identity = replace(
        fake_google.identity, email="renamed@example.com", name="Renamed User"
    )
    second_login = await complete_login(client)
    assert second_login.status_code == 302
    second_profile = (await client.get("/auth/me")).json()

    assert second_profile["id"] == first_profile["id"]
    assert second_profile["email"] == "renamed@example.com"
    assert second_profile["name"] == "Renamed User"
    assert second_profile["role"] == "admin"

    count = await db_session.scalar(select(func.count()).select_from(User))
    assert count == 1
