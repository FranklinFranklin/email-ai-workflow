"""
Security tests — Gmail OAuth scope-afdwinging.

Verifieert dat verboden scopes (gmail.send, gmail.modify, admin)
altijd een ScopeViolationError gooien, ook als ze via een
omweg worden meegegeven.

ISO27001 A.9.4 — Toegangsbeheer, principe van minste privilege.
SOC2 CC6.3     — Autorisatie en toegangscontrole.
"""

import pytest
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet

_TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", _TEST_KEY)
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-minimaal-32-tekens!!")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test")
    monkeypatch.setenv("REDIS_PASSWORD", "test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GMAIL_REDIRECT_URI", "http://localhost/callback")


FORBIDDEN_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/gmail.settings.sharing",
]

ALLOWED_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.labels",
]


class TestForbiddenScopesRejected:
    @pytest.mark.parametrize("forbidden_scope", FORBIDDEN_SCOPES)
    def test_forbidden_scope_raises_on_token_check(self, forbidden_scope):
        """Verboden scope in token-set → ScopeViolationError."""
        from services.gmail.oauth import GmailOAuth, ScopeViolationError
        oauth = GmailOAuth()
        scopes = set(ALLOWED_SCOPES) | {forbidden_scope}

        with pytest.raises(ScopeViolationError):
            oauth._assert_no_forbidden_scopes(scopes)

    @pytest.mark.parametrize("forbidden_scope", FORBIDDEN_SCOPES)
    def test_forbidden_scope_in_deserialized_token_raises(self, forbidden_scope):
        """Tokens met verboden scopes worden geweigerd bij deserializatie."""
        import json
        from services.gmail.oauth import GmailOAuth, ScopeViolationError
        from security.encryption import encrypt

        oauth = GmailOAuth()
        token_data = {
            "token":         "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "client_id":     "test-client",
            "client_secret": "test-secret",
            "scopes":        list(ALLOWED_SCOPES) + [forbidden_scope],
            "expiry":        None,
        }
        encrypted = encrypt(json.dumps(token_data))

        with pytest.raises(ScopeViolationError):
            oauth.deserialize_tokens(encrypted)

    def test_send_scope_alone_raises(self):
        """Alleen de send-scope is al voldoende voor ScopeViolationError."""
        from services.gmail.oauth import GmailOAuth, ScopeViolationError
        oauth = GmailOAuth()
        with pytest.raises(ScopeViolationError):
            oauth._assert_no_forbidden_scopes(
                {"https://www.googleapis.com/auth/gmail.send"}
            )

    def test_combined_forbidden_raises(self):
        """Meerdere verboden scopes gooien ook ScopeViolationError."""
        from services.gmail.oauth import GmailOAuth, ScopeViolationError
        oauth = GmailOAuth()
        with pytest.raises(ScopeViolationError):
            oauth._assert_no_forbidden_scopes(set(FORBIDDEN_SCOPES))


class TestAllowedScopesAccepted:
    def test_allowed_scopes_do_not_raise(self):
        """Toegestane scopes mogen geen exception gooien."""
        from services.gmail.oauth import GmailOAuth
        oauth = GmailOAuth()
        # Mag geen exception gooien
        oauth._assert_no_forbidden_scopes(set(ALLOWED_SCOPES))

    def test_empty_scope_set_accepted(self):
        """Lege scope-set mag ook geen exception gooien."""
        from services.gmail.oauth import GmailOAuth
        oauth = GmailOAuth()
        oauth._assert_no_forbidden_scopes(set())

    @pytest.mark.parametrize("scope", ALLOWED_SCOPES)
    def test_each_allowed_scope_accepted(self, scope):
        from services.gmail.oauth import GmailOAuth
        oauth = GmailOAuth()
        oauth._assert_no_forbidden_scopes({scope})


class TestDraftCreatorHasNoSendMethod:
    """Architecturele test: DraftCreator bevat geen send()-methode."""

    def test_no_send_method_exists(self):
        from services.gmail.draft_creator import GmailDraftCreator
        assert not hasattr(GmailDraftCreator, "send"), (
            "GmailDraftCreator heeft een send()-methode — "
            "dit is een architecturele beveiligingsovertreding!"
        )

    def test_no_send_in_module_namespace(self):
        import services.gmail.draft_creator as module
        public_names = [n for n in dir(module) if not n.startswith("_")]
        assert "send" not in public_names, (
            "De naam 'send' is aanwezig in de module-namespace van draft_creator"
        )
