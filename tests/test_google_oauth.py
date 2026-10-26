"""ID token verification without any real Google network calls."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.core.config import Settings
from app.core.errors import EmailNotVerified, InvalidIdToken
from app.integrations.google_oauth import GoogleOAuthClient


@pytest.fixture(scope="module")
def private_key() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def public_jwk(private_key: Any) -> dict[str, Any]:
    jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk.update({"kid": "test-kid", "alg": "RS256", "use": "sig"})
    return jwk


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret="unit-test-jwt-secret-" + "x" * 32,
        google_client_id="test-client-id",
    )


def _token(private_key: Any, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": "test-client-id",
        "sub": "google-sub-1",
        "email": "user@example.com",
        "email_verified": True,
        "name": "Test User",
        "picture": "https://example.com/avatar.png",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )


def _client(
    public_jwk: dict[str, Any], calls: list[str]
) -> tuple[GoogleOAuthClient, httpx.AsyncClient]:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"keys": [public_jwk]})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        GoogleOAuthClient(
            _settings(),
            http_client=http_client,
            clock=lambda: 0.0,
        ),
        http_client,
    )


async def test_valid_id_token_and_jwks_cache(private_key: Any, public_jwk: dict[str, Any]) -> None:
    calls: list[str] = []
    oauth, http_client = _client(public_jwk, calls)
    token = _token(private_key)

    async with http_client:
        first = await oauth.verify_id_token(token)
        second = await oauth.verify_id_token(token)

    assert first.sub == "google-sub-1"
    assert first.email == "user@example.com"
    assert first.email_verified is True
    assert second == first
    assert len(calls) == 1


async def test_wrong_issuer_is_rejected(private_key: Any, public_jwk: dict[str, Any]) -> None:
    oauth, http_client = _client(public_jwk, [])
    token = _token(private_key, iss="https://evil.example")

    async with http_client:
        with pytest.raises(InvalidIdToken):
            await oauth.verify_id_token(token)


async def test_wrong_audience_is_rejected(private_key: Any, public_jwk: dict[str, Any]) -> None:
    oauth, http_client = _client(public_jwk, [])
    token = _token(private_key, aud="someone-else")

    async with http_client:
        with pytest.raises(InvalidIdToken):
            await oauth.verify_id_token(token)


async def test_expired_token_is_rejected(private_key: Any, public_jwk: dict[str, Any]) -> None:
    oauth, http_client = _client(public_jwk, [])
    token = _token(
        private_key,
        iat=datetime.now(UTC) - timedelta(hours=1),
        exp=datetime.now(UTC) - timedelta(minutes=1),
    )

    async with http_client:
        with pytest.raises(InvalidIdToken):
            await oauth.verify_id_token(token)


async def test_bad_signature_is_rejected(private_key: Any, public_jwk: dict[str, Any]) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    oauth, http_client = _client(public_jwk, [])
    token = _token(other_key)

    async with http_client:
        with pytest.raises(InvalidIdToken):
            await oauth.verify_id_token(token)


async def test_unverified_email_is_rejected(private_key: Any, public_jwk: dict[str, Any]) -> None:
    oauth, http_client = _client(public_jwk, [])
    token = _token(private_key, email_verified=False)

    async with http_client:
        with pytest.raises(EmailNotVerified):
            await oauth.verify_id_token(token)
