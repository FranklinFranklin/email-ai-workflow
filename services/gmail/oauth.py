"""
Gmail OAuth2-flow.

Scopes: readonly + compose + labels  ⛔ NOOIT send of admin
Tokens worden versleuteld opgeslagen — nooit in logs of plaintext DB.

ISO27001 A.9.4 — Toegangsbeheer op applicatieniveau.
SOC2 CC6.3     — Autorisatie en toegangscontrole.
"""

import json
from datetime import datetime, timezone
from typing import Any

import structlog
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from config import settings
from security.encryption import decrypt, encrypt

logger = structlog.get_logger(__name__)

# ── Toegestane scopes — uitputtend gedefinieerd, nooit dynamisch uitbreiden ──
ALLOWED_SCOPES: frozenset[str] = frozenset({
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.labels",
})

# ⛔ Verboden scopes — gedetecteerd en geblokkeerd bij token-opslag
FORBIDDEN_SCOPES: frozenset[str] = frozenset({
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/gmail.settings.sharing",
})


class ScopeViolationError(Exception):
    """Gooit wanneer een verboden OAuth-scope gedetecteerd wordt."""


class GmailOAuth:
    """
    Beheert de OAuth2-flow en token-lifecycle voor Gmail.

    Gebruik:
        oauth = GmailOAuth()
        url, state = oauth.get_authorization_url()
        # Stuur gebruiker naar `url`

        tokens = await oauth.exchange_code(code, state)
        encrypted = oauth.serialize_tokens(tokens)
        # Sla `encrypted` op in de database (AccountModel.oauth_token_enc)
    """

    def __init__(self) -> None:
        self._client_config = {
            "web": {
                "client_id":     settings.GMAIL_CLIENT_ID,
                "client_secret": settings.GMAIL_CLIENT_SECRET,
                "redirect_uris": [settings.GMAIL_REDIRECT_URI],
                "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                "token_uri":     "https://oauth2.googleapis.com/token",
            }
        }

    def get_authorization_url(self) -> tuple[str, str]:
        """
        Genereer de Google autorisatie-URL.

        Returns:
            (authorization_url, state) — sla `state` op in de sessie
            voor CSRF-verificatie bij de callback.
        """
        flow = self._build_flow()
        flow.redirect_uri = settings.GMAIL_REDIRECT_URI
        url, state = flow.authorization_url(
            access_type="offline",        # refresh_token verkrijgen
            include_granted_scopes="true",
            prompt="consent",             # altijd refresh_token forceren
        )
        logger.info("oauth_authorization_url_generated")
        return url, state

    def exchange_code(
        self,
        code: str,
        received_state: str,
        expected_state: str,
    ) -> Credentials:
        """
        Wissel autorisatiecode in voor access + refresh tokens.

        FIX H-2: Valideert de CSRF-state vóór token-uitwisseling.
        De `expected_state` moet opgehaald worden uit de gebruikerssessie
        die werd opgeslagen bij `get_authorization_url()`.

        Args:
            code:           Autorisatiecode uit de callback-URL.
            received_state: State-parameter ontvangen in de callback-URL.
            expected_state: State opgeslagen in de gebruikerssessie.

        Returns:
            Google Credentials-object.

        Raises:
            ValueError:         Als CSRF-state niet overeenkomt of uitwisseling mislukt.
            ScopeViolationError: Als een verboden scope is toegekend.
        """
        # CSRF-verificatie — constant-time vergelijking voorkomt timing-aanvallen
        import hmac as _hmac
        if not _hmac.compare_digest(
            received_state.encode("utf-8"),
            expected_state.encode("utf-8"),
        ):
            logger.warning("oauth_csrf_state_mismatch")
            raise ValueError(
                "OAuth2 CSRF-verificatie mislukt: state-parameter komt niet overeen. "
                "Mogelijk een CSRF-aanval of een verlopen sessie."
            )

        flow = self._build_flow()
        flow.redirect_uri = settings.GMAIL_REDIRECT_URI

        try:
            flow.fetch_token(code=code)
        except Exception as exc:
            logger.error("oauth_token_exchange_failed")
            raise ValueError("OAuth2 token-uitwisseling mislukt") from exc

        credentials = flow.credentials
        self._assert_no_forbidden_scopes(set(credentials.scopes or []))

        logger.info(
            "oauth_token_exchanged",
            scopes=sorted(credentials.scopes or []),
        )
        return credentials

    def refresh_if_expired(self, credentials: Credentials) -> Credentials:
        """
        Vernieuw het access-token als het verlopen is.

        Gooit RefreshError als het refresh_token ongeldig is —
        gebruiker moet dan opnieuw autoriseren.
        """
        if not credentials.expired:
            return credentials

        try:
            credentials.refresh(GoogleRequest())
            logger.info("oauth_token_refreshed")
        except RefreshError as exc:
            logger.error("oauth_token_refresh_failed")
            raise

        return credentials

    def serialize_tokens(self, credentials: Credentials) -> str:
        """
        Serialiseer en versleutel tokens voor database-opslag.

        Nooit plaintext opslaan — altijd via encrypt().
        """
        data: dict[str, Any] = {
            "token":         credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri":     credentials.token_uri,
            "client_id":     credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes":        list(credentials.scopes or []),
            "expiry":        credentials.expiry.isoformat() if credentials.expiry else None,
        }
        return encrypt(json.dumps(data))

    def deserialize_tokens(self, encrypted: str) -> Credentials:
        """
        Ontsleutel en herstel Credentials vanuit database.

        Raises:
            ScopeViolationError: Als opgeslagen token verboden scopes bevat.
        """
        data: dict[str, Any] = json.loads(decrypt(encrypted))

        self._assert_no_forbidden_scopes(set(data.get("scopes", [])))

        expiry = (
            datetime.fromisoformat(data["expiry"])
            if data.get("expiry")
            else None
        )

        return Credentials(
            token=data["token"],
            refresh_token=data["refresh_token"],
            token_uri=data["token_uri"],
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            scopes=data["scopes"],
            expiry=expiry,
        )

    # ── Privémethoden ─────────────────────────────────────────────────────────

    def _build_flow(self) -> Flow:
        return Flow.from_client_config(
            self._client_config,
            scopes=sorted(ALLOWED_SCOPES),
        )

    @staticmethod
    def _assert_no_forbidden_scopes(granted: set[str]) -> None:
        """
        Blokkeer tokens die verboden scopes bevatten.

        Dit is een harde veiligheidscheck — nooit uitschakelen.
        """
        violations = granted & FORBIDDEN_SCOPES
        if violations:
            logger.critical(
                "forbidden_scope_detected",
                violations=sorted(violations),
            )
            raise ScopeViolationError(
                f"Verboden OAuth-scope(s) gedetecteerd: {sorted(violations)}. "
                "Token wordt geweigerd."
            )
