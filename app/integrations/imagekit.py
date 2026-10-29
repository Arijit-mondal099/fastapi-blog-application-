"""ImageKit upload-auth signing and a thin server-side API client."""

import hashlib
import hmac
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote

import httpx

from app.core.config import Settings, get_settings

IMAGEKIT_API_BASE_URL = "https://api.imagekit.io"


@dataclass(frozen=True)
class ImageKitFile:
    """The subset of ImageKit file details needed by the application."""

    file_id: str
    url: str
    mime_type: str | None
    size_bytes: int | None
    file_path: str | None


class ImageKitClientError(Exception):
    """Raised when ImageKit cannot be reached or returns an unusable response."""


def sign_upload_auth(*, private_key: str, token: str, expire: int) -> str:
    """Return the lowercase HMAC-SHA1 digest of ``token + expire``."""
    message = f"{token}{expire}".encode()
    return hmac.new(private_key.encode("utf-8"), message, hashlib.sha1).hexdigest()


class ImageKitClient:
    """Small ImageKit API client using the private key for HTTP Basic Auth."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client

    async def get_file_details(self, file_id: str) -> ImageKitFile | None:
        """Look up a file by id, returning ``None`` when ImageKit reports 404."""
        encoded_id = quote(file_id, safe="")
        try:
            response = await self._get(f"{IMAGEKIT_API_BASE_URL}/v1/files/{encoded_id}/details")
        except httpx.HTTPError as exc:
            raise ImageKitClientError("ImageKit request failed") from exc

        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ImageKitClientError(f"ImageKit returned status {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ImageKitClientError("ImageKit returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ImageKitClientError("ImageKit returned an invalid file payload")

        file_id_value = payload.get("fileId")
        url_value = payload.get("url")
        if not isinstance(file_id_value, str) or not isinstance(url_value, str):
            raise ImageKitClientError("ImageKit file payload is missing required fields")

        mime_value = payload.get("mime")
        size_value = payload.get("size")
        path_value = payload.get("filePath")
        return ImageKitFile(
            file_id=file_id_value,
            url=url_value,
            mime_type=mime_value if isinstance(mime_value, str) else None,
            size_bytes=size_value
            if isinstance(size_value, int) and not isinstance(size_value, bool)
            else None,
            file_path=path_value if isinstance(path_value, str) else None,
        )

    async def _get(self, url: str) -> httpx.Response:
        auth = httpx.BasicAuth(
            self._settings.imagekit_private_key.get_secret_value(),
            "",
        )
        if self._http_client is not None:
            return await self._http_client.get(url, auth=auth)
        async with httpx.AsyncClient(timeout=10.0, auth=auth) as client:
            return await client.get(url)


@lru_cache
def get_imagekit_client() -> ImageKitClient:
    """Return the process-wide ImageKit API client."""
    return ImageKitClient(get_settings())
