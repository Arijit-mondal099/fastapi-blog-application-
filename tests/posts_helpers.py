"""Shared helpers for post and tag integration tests."""

from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.user import Role, User


async def create_user(
    db_session: AsyncSession,
    role: Role,
    *,
    name: str = "Test User",
) -> User:
    user = User(
        google_sub=f"sub-{uuid4()}",
        email=f"{uuid4()}@example.com",
        name=name,
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def auth_cookies(user: User) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        family_id=uuid4(),
        settings=get_settings(),
    )
    return {"access_token": token}


async def create_post(
    client: httpx.AsyncClient,
    *,
    title: str,
    body_md: str = "Body",
    tags: list[str] | None = None,
) -> httpx.Response:
    return await client.post(
        "/posts",
        json={"title": title, "body_md": body_md, "tags": tags or []},
    )


async def publish_post(client: httpx.AsyncClient, post_id: str) -> httpx.Response:
    return await client.post(f"/posts/{post_id}/publish")
