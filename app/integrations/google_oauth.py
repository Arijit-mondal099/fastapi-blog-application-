"""Google OAuth authorization-code flow and ID token verification."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, cast
from urllib.parse import urlencode

import httpx
import jwt

from app.core.config import Settings, get_settings
from app.core.errors import (
    EmailNotVerified,
    InvalidIdToken,
    OAuthExchangeError,
)

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_ENDPOINT = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
GOOGLE_SCOPE = "openid email profile"


@dataclass(frozen=True)
class GoogleIdentity:
    """Verified claims needed to create or update a user."""

    sub: str
    email: str
    email_verified: bool
    name: str | None
    avatar_url: str | None


class GoogleOAuthClient:
    """Google OAuth client with asynchronous, cached JWKS handling."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        jwks_ttl_seconds: int = 3600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._jwks_ttl_seconds = jwks_ttl_seconds
        self._clock = clock
        self._signing_keys: dict[str, Any] = {}
        self._jwks_expires_at = 0.0

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        """Build the Google authorization URL with state and PKCE S256."""
        params = {
            "client_id": self._settings.google_client_id,
            "redirect_uri": self._settings.google_redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_SCOPE,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "online",
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> str:
        """Exchange an authorization code and return the raw ID token."""
        data = {
            "code": code,
            "client_id": self._settings.google_client_id,
            "client_secret": self._settings.google_client_secret.get_secret_value(),
            "redirect_uri": self._settings.google_redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        try:
            response = await self._post(GOOGLE_TOKEN_ENDPOINT, data=data)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OAuthExchangeError() from exc

        if not isinstance(payload, dict) or "id_token" not in payload:
            raise OAuthExchangeError()
        id_token = payload.get("id_token")
        if not isinstance(id_token, str):
            raise OAuthExchangeError()
        return id_token

    async def verify_id_token(self, id_token: str) -> GoogleIdentity:
        """Verify signature and standard claims, then require a verified email."""
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise InvalidIdToken() from exc

        kid = header.get("kid")
        if not isinstance(kid, str) or header.get("alg") != "RS256":
            raise InvalidIdToken()

        signing_key = await self._get_signing_key(kid)
        try:
            payload = jwt.decode(
                id_token,
                signing_key,
                algorithms=["RS256"],
                audience=self._settings.google_client_id,
                issuer=list(GOOGLE_ISSUERS),
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidIdToken() from exc

        if payload.get("email_verified") is not True:
            raise EmailNotVerified()

        sub = payload.get("sub")
        email = payload.get("email")
        if not isinstance(sub, str) or not isinstance(email, str):
            raise InvalidIdToken()

        name = payload.get("name")
        picture = payload.get("picture")
        return GoogleIdentity(
            sub=sub,
            email=email,
            email_verified=True,
            name=name if isinstance(name, str) else None,
            avatar_url=picture if isinstance(picture, str) else None,
        )

    async def _get_signing_key(self, kid: str) -> Any:
        now = self._clock()
        if now >= self._jwks_expires_at or kid not in self._signing_keys:
            await self._refresh_jwks()
        key = self._signing_keys.get(kid)
        if key is None:
            raise InvalidIdToken()
        return key

    async def _refresh_jwks(self) -> None:
        try:
            response = await self._get(GOOGLE_JWKS_ENDPOINT)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise InvalidIdToken() from exc

        if not isinstance(payload, dict):
            raise InvalidIdToken()
        keys = payload.get("keys")
        if not isinstance(keys, list):
            raise InvalidIdToken()

        signing_keys: dict[str, Any] = {}
        for jwk_data in keys:
            if not isinstance(jwk_data, dict):
                continue
            kid = jwk_data.get("kid")
            if not isinstance(kid, str):
                continue
            try:
                signing_keys[kid] = jwt.PyJWK(cast(dict[str, Any], jwk_data)).key
            except jwt.PyJWTError:
                continue

        if not signing_keys:
            raise InvalidIdToken()
        self._signing_keys = signing_keys
        self._jwks_expires_at = self._clock() + self._jwks_ttl_seconds

    async def _get(self, url: str) -> httpx.Response:
        if self._http_client is not None:
            return await self._http_client.get(url)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.get(url)

    async def _post(self, url: str, *, data: dict[str, str]) -> httpx.Response:
        if self._http_client is not None:
            return await self._http_client.post(url, data=data)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.post(url, data=data)


@lru_cache
def get_google_oauth_client() -> GoogleOAuthClient:
    """Return the process-wide Google OAuth client (with JWKS cache)."""
    return GoogleOAuthClient(get_settings())
