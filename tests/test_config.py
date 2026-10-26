"""Tests for environment-backed settings."""

from app.core.config import Settings

ENV_VALUES = {
    "APP_ENV": "test",
    "DATABASE_URL": "postgresql+asyncpg://user:pass@db:5432/blog_test",
    "REDIS_URL": "redis://redis:6379/1",
    "JWT_SECRET": "test-jwt-secret-" + "x" * 32,
    "ACCESS_TOKEN_TTL_MINUTES": "5",
    "REFRESH_TOKEN_TTL_DAYS": "7",
    "GOOGLE_CLIENT_ID": "test-google-client-id",
    "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
    "GOOGLE_REDIRECT_URI": "http://localhost:8000/auth/google/callback",
    "ADMIN_EMAILS": "admin@example.com, editor@example.com",
    "FRONTEND_URL": "http://localhost:3000",
    "POST_LOGIN_PATH": "/welcome",
    "COOKIE_DOMAIN": "example.com",
    "TRUSTED_PROXY_COUNT": "2",
    "IMAGEKIT_PUBLIC_KEY": "test-imagekit-public-key",
    "IMAGEKIT_PRIVATE_KEY": "test-imagekit-private-key",
    "IMAGEKIT_URL_ENDPOINT": "https://ik.imagekit.io/test",
}


def test_settings_load_from_env(monkeypatch) -> None:
    for key, value in ENV_VALUES.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.database_url == "postgresql+asyncpg://user:pass@db:5432/blog_test"
    assert settings.redis_url == "redis://redis:6379/1"
    assert settings.jwt_secret.get_secret_value() == "test-jwt-secret-" + "x" * 32
    assert settings.access_token_ttl_minutes == 5
    assert settings.refresh_token_ttl_days == 7
    assert settings.google_client_id == "test-google-client-id"
    assert settings.google_client_secret.get_secret_value() == "test-google-client-secret"
    assert settings.google_redirect_uri == "http://localhost:8000/auth/google/callback"
    assert settings.admin_emails == ["admin@example.com", "editor@example.com"]
    assert settings.frontend_url == "http://localhost:3000"
    assert settings.post_login_path == "/welcome"
    assert settings.cookie_domain == "example.com"
    assert settings.trusted_proxy_count == 2
    assert settings.imagekit_public_key == "test-imagekit-public-key"
    assert settings.imagekit_private_key.get_secret_value() == "test-imagekit-private-key"
    assert settings.imagekit_url_endpoint == "https://ik.imagekit.io/test"
