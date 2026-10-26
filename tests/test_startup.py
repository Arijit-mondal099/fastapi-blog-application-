"""Startup validation and environment-gated test routes."""

import pytest

from app.core.config import get_settings
from app.main import create_app
from tests.helpers import make_client


def test_short_jwt_secret_prevents_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "too-short")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="JWT_SECRET"):
        create_app()


@pytest.mark.parametrize("path", ["/test/admin-only", "/test/author-only"])
async def test_test_routes_are_absent_when_app_env_is_not_test(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    production_app = create_app()

    async with make_client(production_app) as client:
        response = await client.post(path)

    assert response.status_code == 404
