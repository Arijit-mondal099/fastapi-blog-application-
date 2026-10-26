"""Role checks, admin user management, audit logging, and pagination."""

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import ROLE_RANK
from app.core.logging import JsonFormatter
from app.core.security import create_access_token
from app.models.refresh_token import RefreshToken
from app.models.user import Role, User
from app.services.admin import AUDIT_LOGGER_NAME
from tests.helpers import make_client


async def _create_user(
    db_session: AsyncSession,
    role: Role,
    *,
    name: str = "Test User",
    created_at: datetime | None = None,
) -> User:
    user = User(
        google_sub=f"sub-{uuid4()}",
        email=f"{uuid4()}@example.com",
        name=name,
        role=role,
    )
    if created_at is not None:
        user.created_at = created_at
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _access_cookies(user: User) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        family_id=uuid4(),
        settings=get_settings(),
    )
    return {"access_token": token}


async def _add_refresh_token(
    db_session: AsyncSession,
    user: User,
    *,
    revoked_at: datetime | None = None,
) -> RefreshToken:
    now = datetime.now(UTC)
    row = RefreshToken(
        id=uuid4(),
        user_id=user.id,
        family_id=uuid4(),
        token_hash=uuid4().hex + uuid4().hex,
        expires_at=now + timedelta(days=30),
        revoked_at=revoked_at,
        created_at=now,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


def _role_change_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == AUDIT_LOGGER_NAME and getattr(record, "event", None) == "role_changed"
    ]


def test_role_rank_orders_reader_author_admin() -> None:
    assert ROLE_RANK[Role.reader] < ROLE_RANK[Role.author] < ROLE_RANK[Role.admin]


@pytest.mark.parametrize(
    ("role", "author_status", "patch_status", "list_status"),
    [
        (Role.reader, 403, 403, 403),
        (Role.author, 200, 403, 403),
        (Role.admin, 200, 200, 200),
        (None, 401, 401, 401),
    ],
    ids=["reader", "author", "admin", "anonymous"],
)
async def test_author_and_admin_endpoint_permissions(
    app: FastAPI,
    db_session: AsyncSession,
    role: Role | None,
    author_status: int,
    patch_status: int,
    list_status: int,
) -> None:
    target = await _create_user(db_session, Role.reader)
    cookies: dict[str, str] = {}
    if role is not None:
        actor = await _create_user(db_session, role)
        cookies = _access_cookies(actor)

    async with make_client(app, cookies) as client:
        author_only = await client.post("/test/author-only")
        patch = await client.patch(
            f"/admin/users/{target.id}/role",
            json={"role": Role.author.value},
        )
        listing = await client.get("/admin/users")

    assert author_only.status_code == author_status
    assert patch.status_code == patch_status
    assert listing.status_code == list_status


async def test_admin_role_change_revokes_families_and_audits(
    app: FastAPI,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    admin = await _create_user(db_session, Role.admin)
    target = await _create_user(db_session, Role.reader)
    first_family = await _add_refresh_token(db_session, target)
    second_family = await _add_refresh_token(db_session, target)

    async with make_client(app, _access_cookies(admin)) as client:
        response = await client.patch(
            f"/admin/users/{target.id}/role",
            json={"role": Role.author.value},
        )

    assert response.status_code == 200
    assert response.json()["role"] == Role.author.value
    await db_session.refresh(target)
    assert target.role == Role.author

    for row in (first_family, second_family):
        await db_session.refresh(row)
        assert row.revoked_at is not None

    records = _role_change_records(caplog)
    assert len(records) == 1
    payload = json.loads(JsonFormatter().format(records[0]))
    assert payload["event"] == "role_changed"
    assert payload["actor_id"] == str(admin.id)
    assert payload["target_id"] == str(target.id)
    assert payload["old_role"] == Role.reader.value
    assert payload["new_role"] == Role.author.value


async def test_admin_cannot_demote_self(
    app: FastAPI,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    admin = await _create_user(db_session, Role.admin)

    async with make_client(app, _access_cookies(admin)) as client:
        response = await client.patch(
            f"/admin/users/{admin.id}/role",
            json={"role": Role.reader.value},
        )

    assert response.status_code == 409
    await db_session.refresh(admin)
    assert admin.role == Role.admin
    assert _role_change_records(caplog) == []


async def test_unknown_user_id_returns_404(
    app: FastAPI,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    admin = await _create_user(db_session, Role.admin)

    async with make_client(app, _access_cookies(admin)) as client:
        response = await client.patch(
            f"/admin/users/{uuid4()}/role",
            json={"role": Role.author.value},
        )

    assert response.status_code == 404
    assert _role_change_records(caplog) == []


async def test_same_role_change_is_a_no_op(
    app: FastAPI,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    admin = await _create_user(db_session, Role.admin)
    target = await _create_user(db_session, Role.author)
    family = await _add_refresh_token(db_session, target)

    async with make_client(app, _access_cookies(admin)) as client:
        response = await client.patch(
            f"/admin/users/{target.id}/role",
            json={"role": Role.author.value},
        )

    assert response.status_code == 200
    await db_session.refresh(family)
    assert family.revoked_at is None
    assert _role_change_records(caplog) == []


async def test_admin_change_rereads_actor_role_from_database(
    app: FastAPI,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    admin = await _create_user(db_session, Role.admin)
    target = await _create_user(db_session, Role.reader)
    cookies = _access_cookies(admin)

    admin.role = Role.reader
    await db_session.commit()

    async with make_client(app, cookies) as client:
        response = await client.patch(
            f"/admin/users/{target.id}/role",
            json={"role": Role.author.value},
        )

    assert response.status_code == 403
    await db_session.refresh(target)
    assert target.role == Role.reader
    assert _role_change_records(caplog) == []


async def test_admin_users_pagination_is_stable_across_inserts(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)
    admin = await _create_user(
        db_session,
        Role.admin,
        created_at=base - timedelta(minutes=1),
    )
    users = [
        await _create_user(
            db_session,
            Role.reader,
            name=f"User {index}",
            created_at=base + timedelta(minutes=index),
        )
        for index in range(5)
    ]
    ordered = sorted(users, key=lambda user: (user.created_at, user.id), reverse=True)

    async with make_client(app, _access_cookies(admin)) as client:
        first = await client.get("/admin/users", params={"limit": 2})
        assert first.status_code == 200
        first_items = first.json()["items"]
        assert [item["id"] for item in first_items] == [
            str(ordered[0].id),
            str(ordered[1].id),
        ]

        inserted = await _create_user(
            db_session,
            Role.reader,
            name="Inserted User",
            created_at=base + timedelta(minutes=10),
        )

        second = await client.get(
            "/admin/users",
            params={"limit": 2, "cursor": first.json()["next_cursor"]},
        )
        assert second.status_code == 200
        second_items = second.json()["items"]
        assert [item["id"] for item in second_items] == [
            str(ordered[2].id),
            str(ordered[3].id),
        ]

        third = await client.get(
            "/admin/users",
            params={"limit": 2, "cursor": second.json()["next_cursor"]},
        )
        assert third.status_code == 200
        third_items = third.json()["items"]
        assert [item["id"] for item in third_items] == [
            str(ordered[4].id),
            str(admin.id),
        ]
        assert third.json()["next_cursor"] is None

        refreshed_first = await client.get("/admin/users", params={"limit": 2})
        refreshed_ids = [item["id"] for item in refreshed_first.json()["items"]]
        assert refreshed_ids == [str(inserted.id), str(ordered[0].id)]

    for item in first_items + second_items + third_items:
        assert set(item) == {"id", "email", "name", "role"}
        assert "google_sub" not in item
    assert "google_sub" not in first.text


async def test_invalid_cursor_returns_400(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    admin = await _create_user(db_session, Role.admin)

    async with make_client(app, _access_cookies(admin)) as client:
        response = await client.get("/admin/users", params={"cursor": "not-a-cursor"})

    assert response.status_code == 400
