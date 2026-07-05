"""
API tests — POST /api/v1/webhooks/gmail

Test: geldige Pub/Sub-notificaties, ongeldige tokens,
lege body, misvormd JSON, altijd-200-gedrag.
"""

import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

_TEST_KEY = Fernet.generate_key().decode()
_WEBHOOK_URL = "/api/v1/webhooks/gmail"


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


def _make_pubsub_body(email: str = "test@bedrijf.nl", history_id: str = "12345") -> bytes:
    """Bouw een geldig Pub/Sub-notificatie-payload."""
    data = base64.b64encode(
        json.dumps({"emailAddress": email, "historyId": history_id}).encode()
    ).decode()
    return json.dumps({
        "message": {"data": data, "messageId": "msg-001", "publishTime": "2026-01-01T00:00:00Z"},
        "subscription": "projects/test/subscriptions/gmail-sub",
    }).encode()


def _valid_token() -> str:
    """Genereer het verwachte verificatietoken (eerste 32 tekens van APP_SECRET_KEY)."""
    return "test-secret-key-minimaal-32-tekens!!"[:32]


class TestWebhookEndpoint:
    def test_valid_notification_returns_200(self, client):
        token = _valid_token()
        resp = client.post(
            f"{_WEBHOOK_URL}?token={token}",
            content=_make_pubsub_body(),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

    def test_valid_notification_returns_accepted(self, client):
        token = _valid_token()
        resp = client.post(
            f"{_WEBHOOK_URL}?token={token}",
            content=_make_pubsub_body(),
        )
        data = resp.json()
        assert data["status"] == "accepted"

    def test_invalid_token_returns_200_ignored(self, client):
        """
        Pub/Sub-vereiste: altijd 200 teruggeven, ook bij ongeldige token.
        Inhoud geeft 'ignored' aan.
        """
        resp = client.post(
            f"{_WEBHOOK_URL}?token=fout-token",
            content=_make_pubsub_body(),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_missing_token_returns_200_ignored(self, client):
        resp = client.post(_WEBHOOK_URL, content=_make_pubsub_body())
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_empty_body_returns_200_empty(self, client):
        token = _valid_token()
        resp = client.post(f"{_WEBHOOK_URL}?token={token}", content=b"")
        assert resp.status_code == 200
        assert resp.json()["status"] == "empty"

    def test_malformed_json_returns_200_parse_error(self, client):
        token = _valid_token()
        resp = client.post(
            f"{_WEBHOOK_URL}?token={token}",
            content=b"dit is geen json {{{",
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "parse_error"

    def test_missing_history_id_returns_parse_error(self, client):
        token = _valid_token()
        data = base64.b64encode(
            json.dumps({"emailAddress": "test@bedrijf.nl"}).encode()
            # historyId ontbreekt
        ).decode()
        body = json.dumps({
            "message": {"data": data},
            "subscription": "projects/test/subscriptions/sub",
        }).encode()
        resp = client.post(f"{_WEBHOOK_URL}?token={token}", content=body)
        assert resp.status_code == 200
        assert resp.json()["status"] == "parse_error"


class TestWebhookNeverSends:
    """Architecturele test: webhook triggert nooit verzending."""

    def test_webhook_has_no_send_logic(self):
        import ast
        import pathlib

        source = pathlib.Path(
            "api/v1/routes/webhooks.py"
        ).read_text()
        tree = ast.parse(source)

        send_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(getattr(node.func, "attr", None), str)
            and "send" in node.func.attr.lower()
        ]
        assert len(send_calls) == 0, (
            f"Webhook-route bevat send()-aanroepen: {[ast.dump(c) for c in send_calls]}"
        )
