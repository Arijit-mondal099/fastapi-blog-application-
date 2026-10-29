"""Application configuration loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Every key mirrors `.env.example`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "local"
    database_url: str = "postgresql+asyncpg://blog:blog@localhost:5432/blog"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: SecretStr = SecretStr("change-me")
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30

    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    admin_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)

    frontend_url: str = "http://localhost:3000"
    post_login_path: str = "/"
    cookie_domain: str | None = None
    trusted_proxy_count: int = 0

    imagekit_public_key: str = ""
    imagekit_private_key: SecretStr = SecretStr("")
    imagekit_url_endpoint: str = ""

    media_allowed_mime_types: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/gif",
            "image/avif",
        ]
    )
    media_max_size_bytes: int = 10 * 1024 * 1024

    @field_validator("admin_emails", mode="before")
    @classmethod
    def _split_admin_emails(cls, value: str | list[str]) -> list[str]:
        """Accept a comma-separated value from the environment."""
        if isinstance(value, str):
            return [email.strip() for email in value.split(",") if email.strip()]
        return value

    @field_validator("media_allowed_mime_types", mode="before")
    @classmethod
    def _split_media_mime_types(cls, value: str | list[str]) -> list[str]:
        """Accept a comma-separated MIME allow-list from the environment."""
        if isinstance(value, str):
            return [mime.strip().lower() for mime in value.split(",") if mime.strip()]
        return [mime.strip().lower() for mime in value]

    def validate_startup(self) -> None:
        """Refuse to start with a weak or placeholder signing secret."""
        if len(self.jwt_secret.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET must be at least 32 bytes")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
