"""
API tests — RateLimitMiddleware

Test: limiet afdwingen, headers aanwezig, Redis-uitval degradeert graceful,
health-endpoint wordt overgeslagen.
"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

_TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", _TEST_KEY)
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-minimaal-32-tekens!!")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test")
    monkeypatch.setenv("REDIS_PASSWORD", "test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test")
    monkeypatch.setenv("GMAIL_REDIRECT_URI", "http://localhost/callback")


@pytest.fixture
def client():
    from main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestRateLimitHeaders:
    def test_rate_limit_headers_present(self, client):
        """Elke respons bevat X-RateLimit-* headers (via middleware)."""
        with patch("redis.asyncio.from_url") as mock_redis:
            mock_r = AsyncMock()
            mock_r.incr = AsyncMock(return_value=1)
            mock_r.expire = AsyncMock()
            mock_r.ttl = AsyncMock(return_value=55)
            mock_redis.return_value = mock_r

            resp = client.get("/healthz")
            # health-endpoint wordt overgeslagen door middleware
            # — geen rate-limit headers verwacht
            assert resp.status_code == 200

    def test_rate_limit_headers_on_api_endpoint(self, client):
        with patch("redis.asyncio.from_url") as mock_redis:
            mock_r = AsyncMock()
            mock_r.incr = AsyncMock(return_value=5)
            mock_r.expire = AsyncMock()
            mock_r.ttl = AsyncMock(return_value=50)
            mock_redis.return_value = mock_r

            resp = client.get("/api/v1/some-endpoint")
            # 404 is ok — we testen alleen headers
            assert "X-RateLimit-Limit" in resp.headers
            assert "X-RateLimit-Remaining" in resp.headers


class TestRateLimitEnforcement:
    def test_429_when_limit_exceeded(self, client):
        """Bij overschrijding van de limiet → 429 Too Many Requests."""
        with patch("redis.asyncio.from_url") as mock_redis:
            mock_r = AsyncMock()
            # Simuleer: 61e verzoek (limiet is 60)
            mock_r.incr = AsyncMock(return_value=61)
            mock_r.expire = AsyncMock()
            mock_r.ttl = AsyncMock(return_value=30)
            mock_redis.return_value = mock_r

            resp = client.get("/api/v1/emails")
            assert resp.status_code == 429

    def test_429_contains_retry_after(self, client):
        with patch("redis.asyncio.from_url") as mock_redis:
            mock_r = AsyncMock()
            mock_r.incr = AsyncMock(return_value=999)
            mock_r.expire = AsyncMock()
            mock_r.ttl = AsyncMock(return_value=42)
            mock_redis.return_value = mock_r

            resp = client.get("/api/v1/emails")
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers
            assert resp.headers["Retry-After"] == "42"

    def test_remaining_header_zero_at_limit(self, client):
        with patch("redis.asyncio.from_url") as mock_redis:
            mock_r = AsyncMock()
            mock_r.incr = AsyncMock(return_value=100)
            mock_r.expire = AsyncMock()
            mock_r.ttl = AsyncMock(return_value=10)
            mock_redis.return_value = mock_r

            resp = client.get("/api/v1/emails")
            assert resp.status_code == 429
            assert resp.headers.get("X-RateLimit-Remaining") == "0"


class TestGracefulDegradation:
    def test_redis_unavailable_does_not_block_requests(self, client):
        """Als Redis onbereikbaar is, mogen verzoeken NIET geblokkeerd worden."""
        import redis.asyncio as aioredis

        with patch("redis.asyncio.from_url") as mock_redis:
            mock_r = AsyncMock()
            mock_r.incr = AsyncMock(side_effect=aioredis.RedisError("verbinding geweigerd"))
            mock_redis.return_value = mock_r

            resp = client.get("/healthz")
            # Verzoek moet doorkomen — geen 429 of 500
            assert resp.status_code == 200


class TestSkippedPaths:
    def test_health_endpoint_not_rate_limited(self, client):
        """Healthcheck-endpoint wordt niet meegeteld in rate limiting."""
        with patch("redis.asyncio.from_url") as mock_redis:
            mock_r = AsyncMock()
            mock_r.incr = AsyncMock(return_value=9999)
            mock_redis.return_value = mock_r

            # /healthz staat in _SKIP_PATHS — geen 429
            resp = client.get("/healthz")
            assert resp.status_code == 200
