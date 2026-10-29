"""Shared helpers and fakes for Phase 1 auth tests."""

from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastapi import FastAPI
from redis.exceptions import ConnectionError as RedisConnectionError

from app.integrations.google_oauth import GoogleIdentity


class FakeGoogleClient:
    """Deterministic stand-in for the real Google integration."""

    def __init__(self) -> None:
        self.identity = GoogleIdentity(
            sub="google-sub-1",
            email="admin@example.com",
            email_verified=True,
            name="Admin User",
            avatar_url="https://example.com/avatar.png",
        )
        self.last_state: str | None = None
        self.last_challenge: str | None = None
        self.last_code: str | None = None
        self.last_code_verifier: str | None = None

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        self.last_state = state
        self.last_challenge = code_challenge
        query = urlencode(
            {
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"https://google.test/authorize?{query}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> str:
        self.last_code = code
        self.last_code_verifier = code_verifier
        return "fake-id-token"

    async def verify_id_token(self, id_token: str) -> GoogleIdentity:
        assert id_token == "fake-id-token"
        return self.identity


class FakeRedis:
    """Small fixed-window Redis substitute used by auth tests."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.fail = False

    async def incr(self, key: str) -> int:
        if self.fail:
            raise RedisConnectionError("redis unavailable")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        if self.fail:
            raise RedisConnectionError("redis unavailable")
        self.expires[key] = seconds
        return True

    async def aclose(self) -> None:
        return None


class FakeJobQueue:
    """Recording job queue used by HTTP and job tests."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[object, ...], str | None]] = []
        self.fail = False

    async def enqueue(
        self,
        function: str,
        *args: object,
        request_id: str | None = None,
    ) -> None:
        if self.fail:
            raise RedisConnectionError("redis unavailable")
        self.jobs.append((function, args, request_id))


def cookie_value(response: httpx.Response, name: str) -> str:
    """Extract a cookie value from a Set-Cookie header."""
    prefix = f"{name}="
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(prefix):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"cookie {name!r} not found")


def set_cookie_header(response: httpx.Response, name: str) -> str:
    """Return the Set-Cookie header for a named cookie."""
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    raise AssertionError(f"cookie {name!r} not found")


def state_from_redirect(response: httpx.Response) -> str:
    """Read the OAuth state from a redirect Location."""
    location = response.headers["location"]
    return parse_qs(urlparse(location).query)["state"][0]


def make_client(app: FastAPI, cookies: dict[str, str] | None = None) -> httpx.AsyncClient:
    """Create an HTTPS ASGI client, optionally with explicit cookies."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
        cookies=cookies,
    )


async def start_login(client: httpx.AsyncClient) -> str:
    """Kick off login and return the state embedded in the redirect."""
    response = await client.get("/auth/google/login")
    assert response.status_code == 302
    return state_from_redirect(response)


async def complete_login(client: httpx.AsyncClient) -> httpx.Response:
    """Run login then callback and return the callback response."""
    state = await start_login(client)
    return await client.get(
        "/auth/google/callback",
        params={"code": "test-code", "state": state},
    )
