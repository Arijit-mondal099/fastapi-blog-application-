"""Unit tests for ImageKit upload signing and the mocked API client."""

import base64

import httpx
import pytest

from app.core.config import Settings
from app.integrations.imagekit import (
    ImageKitClient,
    ImageKitClientError,
    sign_upload_auth,
)


def test_sign_upload_auth_matches_known_vector() -> None:
    """The signature is lowercase HMAC-SHA1 over token + expire."""
    assert (
        sign_upload_auth(
            private_key="test-imagekit-private-key",
            token="0123456789abcdef0123456789abcdef",
            expire=1700000000,
        )
        == "84a6826a5232beec3ffd3f3baceb5cd9536e1c53"
    )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        imagekit_private_key="test-imagekit-private-key",
        imagekit_public_key="test-imagekit-public-key",
        imagekit_url_endpoint="https://ik.imagekit.io/test",
    )


async def test_get_file_details_uses_basic_auth_and_parses_payload() -> None:
    """The client authenticates with the private key and maps the response."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "fileId": "file-1",
                "url": "https://ik.imagekit.io/test/file-1.jpg",
                "mime": "image/jpeg",
                "size": 1234,
                "filePath": "/media/file-1.jpg",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = ImageKitClient(_settings(), http_client=http_client)
        file = await client.get_file_details("file-1")

    expected_auth = "Basic " + base64.b64encode(b"test-imagekit-private-key:").decode("ascii")
    assert seen["url"] == "https://api.imagekit.io/v1/files/file-1/details"
    assert seen["authorization"] == expected_auth
    assert file is not None
    assert file.file_id == "file-1"
    assert file.url == "https://ik.imagekit.io/test/file-1.jpg"
    assert file.mime_type == "image/jpeg"
    assert file.size_bytes == 1234
    assert file.file_path == "/media/file-1.jpg"


async def test_get_file_details_returns_none_for_404() -> None:
    """A missing ImageKit file is represented as None."""
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={}))
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = ImageKitClient(_settings(), http_client=http_client)
        assert await client.get_file_details("missing") is None


async def test_get_file_details_raises_on_server_error() -> None:
    """Unexpected ImageKit failures are surfaced as client errors."""
    transport = httpx.MockTransport(lambda request: httpx.Response(500, json={}))
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = ImageKitClient(_settings(), http_client=http_client)
        with pytest.raises(ImageKitClientError):
            await client.get_file_details("file-1")
