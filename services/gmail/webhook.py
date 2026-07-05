"""
Gmail Push Notifications via Google Cloud Pub/Sub.

Google stuurt een POST naar onze webhook zodra er nieuwe e-mails zijn.
Wij halen de berichten vervolgens op via de readonly-scope.

Flow:
    [Gmail] → wijziging → [Pub/Sub topic] → POST /webhooks/gmail
    → wij verifieren → berichten ophalen → verwerkingswachtrij

ISO27001 A.12.4 — Logging van inkomende integraties.
SOC2 CC7.2      — Detectie van beveiligingsincidenten.
"""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass

import structlog
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import settings

logger = structlog.get_logger(__name__)

_API_VERSION = "v1"
# Hoe lang een Pub/Sub-push registratie geldig blijft (seconden)
_WATCH_EXPIRY_SECONDS = 7 * 24 * 3600  # 7 dagen


@dataclass
class WebhookNotification:
    """Geparsed Pub/Sub-notificatiebericht van Gmail."""
    email_address: str
    history_id: str
    raw_payload: dict


@dataclass
class NewMessageInfo:
    """Nieuw bericht-ID gevonden via history-API."""
    message_id: str
    thread_id: str


class GmailWebhookHandler:
    """
    Verwerkt inkomende Gmail Push Notifications.

    Gebruik:
        handler = GmailWebhookHandler(credentials)

        # Eenmalig bij opstarten of na verlopen watch:
        handler.setup_push_watch()

        # In de webhook-route (POST /webhooks/gmail):
        notification = handler.parse_notification(raw_body)
        new_messages = handler.get_new_messages(notification)
    """

    def __init__(self, credentials) -> None:
        self._credentials = credentials
        self._service: Resource | None = None

    def _get_service(self) -> Resource:
        if self._service is None:
            self._service = build(
                "gmail",
                _API_VERSION,
                credentials=self._credentials,
                cache_discovery=False,
            )
        return self._service

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def setup_push_watch(self) -> dict:
        """
        Registreer een Gmail push-notificatie via Pub/Sub.

        Vereist een geconfigureerd Pub/Sub-topic met juiste IAM-rechten
        (roles/pubsub.publisher voor gmail-api@system.gserviceaccount.com).

        Moet elke ~7 dagen hernieuwd worden (zie setup_watch_renewal).
        """
        if not settings.GMAIL_PUBSUB_TOPIC:
            raise ValueError(
                "GMAIL_PUBSUB_TOPIC is niet geconfigureerd. "
                "Stel de omgevingsvariabele in."
            )

        svc = self._get_service()
        try:
            response = (
                svc.users()
                .watch(
                    userId="me",
                    body={
                        "topicName":  settings.GMAIL_PUBSUB_TOPIC,
                        "labelIds":   ["INBOX"],
                        "labelFilterBehavior": "INCLUDE",
                    },
                )
                .execute()
            )
        except HttpError as exc:
            logger.error("gmail_watch_setup_failed", status=exc.resp.status)
            raise

        logger.info(
            "gmail_watch_registered",
            expiration=response.get("expiration"),
            history_id=response.get("historyId"),
        )
        return response

    @staticmethod
    def parse_notification(raw_body: bytes) -> WebhookNotification:
        """
        Parseer en decodeer een inkomend Pub/Sub-notificatiebericht.

        Pub/Sub stuurt JSON met een base64-gecodeerde `data`-sleutel:
        {
          "message": {
            "data": "<base64 encoded JSON>",
            "messageId": "...",
            "publishTime": "..."
          },
          "subscription": "projects/.../subscriptions/..."
        }

        Args:
            raw_body: Ruwe request-body bytes van de POST.

        Returns:
            WebhookNotification met email_address en history_id.

        Raises:
            ValueError: Als het bericht niet te parsen is.
        """
        try:
            envelope = json.loads(raw_body)
            pubsub_message = envelope.get("message", {})
            data_b64 = pubsub_message.get("data", "")
            data_json = base64.b64decode(data_b64 + "==").decode("utf-8")
            data = json.loads(data_json)
        except Exception as exc:
            logger.warning("gmail_webhook_parse_failed")
            raise ValueError("Ongeldig Pub/Sub-notificatiebericht") from exc

        email_address = data.get("emailAddress", "")
        history_id = str(data.get("historyId", ""))

        if not email_address or not history_id:
            raise ValueError(
                "Pub/Sub-bericht mist verplichte velden: emailAddress of historyId"
            )

        logger.info(
            "gmail_notification_received",
            history_id=history_id,
            # Geen email_address loggen — PII
        )

        return WebhookNotification(
            email_address=email_address,
            history_id=history_id,
            raw_payload=data,
        )

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def get_new_messages(
        self,
        notification: WebhookNotification,
        start_history_id: str | None = None,
    ) -> list[NewMessageInfo]:
        """
        Gebruik de Gmail History API om nieuwe berichten-IDs op te halen.

        Args:
            notification:     Verwerkt notificatiebericht.
            start_history_id: Laatste bekende history-ID (uit database).
                              Als None, gebruik dan de ID uit de notificatie.

        Returns:
            Lijst van NewMessageInfo met message- en thread-IDs.
        """
        svc = self._get_service()
        since_id = start_history_id or notification.history_id

        try:
            response = (
                svc.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=since_id,
                    historyTypes=["messageAdded"],
                    labelId="INBOX",
                )
                .execute()
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                # history_id te oud — volledige sync nodig
                logger.warning("gmail_history_id_expired", history_id=since_id)
                return []
            logger.error("gmail_history_fetch_failed", status=exc.resp.status)
            raise

        new_messages: list[NewMessageInfo] = []
        for history_record in response.get("history", []):
            for added in history_record.get("messagesAdded", []):
                msg = added.get("message", {})
                if msg.get("id"):
                    new_messages.append(
                        NewMessageInfo(
                            message_id=msg["id"],
                            thread_id=msg.get("threadId", ""),
                        )
                    )

        logger.info("gmail_new_messages_found", count=len(new_messages))
        return new_messages

    @staticmethod
    def verify_pubsub_token(token: str) -> bool:
        """
        Verifieer de authenticatietoken van een inkomend Pub/Sub-bericht.

        FIX H-5: Gebruikt PUBSUB_VERIFICATION_TOKEN — een eigen secret,
        volledig los van APP_SECRET_KEY en de JWT-signing-sleutel.
        Credential-hergebruik tussen JWT en Pub/Sub is hiermee geblokkeerd.

        FIX H-6: Het token wordt nu verwacht in de Authorization-header
        ("Bearer <token>"), niet als URL-query-parameter. Zie webhooks.py.

        Vergelijking is constant-time om timing-aanvallen te voorkomen.

        Configureer het token in de Pub/Sub-subscriptie als authenticatie-header:
            Authorization: Bearer <PUBSUB_VERIFICATION_TOKEN>
        """
        if not settings.PUBSUB_VERIFICATION_TOKEN:
            # Token niet geconfigureerd → alle verzoeken weigeren
            return False
        return hmac.compare_digest(
            token.encode("utf-8"),
            settings.PUBSUB_VERIFICATION_TOKEN.encode("utf-8"),
        )
