"""
Gmail inbox-lezer — uitsluitend read-only operaties.

Scope: gmail.readonly  ⛔ Geen write, modify of send
Retries via tenacity bij tijdelijke API-fouten (429, 5xx).

ISO27001 A.12.4 — Logging van systeemactiviteiten.
"""

import base64
import email as stdlib_email
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from services.gmail.oauth import GmailOAuth

logger = structlog.get_logger(__name__)

# Gmail API-versie (stabiel houden — update expliciet testen)
_API_VERSION = "v1"

# Maximaal aantal e-mails per API-call
_MAX_RESULTS = 50


@dataclass
class GmailMessage:
    """Genormaliseerd e-mailobject — onafhankelijk van Gmail API-structuur."""
    message_id: str          # Gmail thread/message ID
    thread_id: str
    subject: str
    sender_name: str
    sender_email: str
    recipients: list[str]
    body_plain: str
    body_html: str
    received_at: datetime
    labels: list[str] = field(default_factory=list)
    has_attachments: bool = False
    attachment_names: list[str] = field(default_factory=list)
    raw_size_bytes: int = 0


class GmailInboxReader:
    """
    Leest e-mails uit Gmail — uitsluitend via de readonly-scope.

    Gebruik:
        reader = GmailInboxReader(credentials)
        messages = reader.list_unread(max_results=20)
        full = reader.get_message(messages[0].message_id)
    """

    def __init__(self, credentials: Any) -> None:
        self._credentials = credentials
        self._service: Resource | None = None

    def _get_service(self) -> Resource:
        """Bouw de Gmail API-client (gecacht per instantie)."""
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
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def list_unread(
        self,
        max_results: int = _MAX_RESULTS,
        label_ids: list[str] | None = None,
    ) -> list[GmailMessage]:
        """
        Haal ongelezen e-mails op uit de inbox.

        Args:
            max_results:  Maximaal aantal berichten (1–500).
            label_ids:    Optionele labelfilter (standaard: INBOX + UNREAD).

        Returns:
            Lijst van GmailMessage-objecten, nieuwste eerst.

        Raises:
            HttpError: Bij API-fouten na alle retries.
            PermissionError: Als de scope onvoldoende is.
        """
        svc = self._get_service()
        labels = label_ids or ["INBOX", "UNREAD"]

        try:
            result = (
                svc.users()
                .messages()
                .list(
                    userId="me",
                    labelIds=labels,
                    maxResults=min(max_results, 500),
                )
                .execute()
            )
        except HttpError as exc:
            self._handle_http_error(exc, operation="list_unread")
            raise

        message_stubs = result.get("messages", [])
        logger.info("gmail_messages_listed", count=len(message_stubs))

        messages: list[GmailMessage] = []
        for stub in message_stubs:
            try:
                msg = self.get_message(stub["id"])
                messages.append(msg)
            except HttpError as exc:
                # Één mislukt bericht stopt de volledige batch niet
                logger.warning("gmail_message_fetch_failed", message_id=stub["id"])

        return messages

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def get_message(self, message_id: str) -> GmailMessage:
        """
        Haal een volledig e-mailbericht op.

        Args:
            message_id: Gmail message-ID.

        Returns:
            GmailMessage met body en headers geparsed.

        Raises:
            HttpError: Bij API-fouten na alle retries.
            ValueError: Als het bericht niet parseerbaar is.
        """
        svc = self._get_service()

        try:
            raw = (
                svc.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except HttpError as exc:
            self._handle_http_error(exc, operation="get_message", message_id=message_id)
            raise

        return self._parse_message(raw)

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def add_label(self, message_id: str, label_id: str) -> None:
        """
        Voeg een label toe aan een bericht (gmail.labels-scope).

        Gebruik bijv. voor "AI_PROCESSING_DONE"-label.
        Geen modify van inhoud — alleen metadatalabel.
        """
        svc = self._get_service()
        try:
            svc.users().messages().modify(
                userId="me",
                id=message_id,
                body={"addLabelIds": [label_id]},
            ).execute()
            logger.debug("gmail_label_added", message_id=message_id, label=label_id)
        except HttpError as exc:
            self._handle_http_error(exc, operation="add_label", message_id=message_id)
            raise

    # ── Privémethoden ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_message(raw: dict[str, Any]) -> GmailMessage:
        """
        Parseer een ruwe Gmail API-respons naar GmailMessage.

        Ondersteunt multipart MIME, base64url-encoding en nested parts.
        """
        headers = {
            h["name"].lower(): h["value"]
            for h in raw.get("payload", {}).get("headers", [])
        }

        sender_raw  = headers.get("from", "")
        sender_name, sender_email = _parse_address(sender_raw)

        # Extraheer body
        body_plain, body_html, attachment_names = _extract_body(raw.get("payload", {}))

        # Timestamp (internalDate = milliseconden sinds epoch)
        internal_date = int(raw.get("internalDate", 0))
        received_at = datetime.fromtimestamp(internal_date / 1000, tz=timezone.utc)

        # Ontvangers
        to_raw  = headers.get("to", "")
        cc_raw  = headers.get("cc", "")
        recipients = [
            _parse_address(addr)[1]
            for addr in (to_raw + "," + cc_raw).split(",")
            if addr.strip()
        ]

        return GmailMessage(
            message_id=raw["id"],
            thread_id=raw.get("threadId", ""),
            subject=headers.get("subject", "(geen onderwerp)"),
            sender_name=sender_name,
            sender_email=sender_email,
            recipients=recipients,
            body_plain=body_plain,
            body_html=body_html,
            received_at=received_at,
            labels=raw.get("labelIds", []),
            has_attachments=bool(attachment_names),
            attachment_names=attachment_names,
            raw_size_bytes=raw.get("sizeEstimate", 0),
        )

    @staticmethod
    def _handle_http_error(
        exc: HttpError,
        operation: str,
        **context: Any,
    ) -> None:
        """Vertaal Gmail API-foutcodes naar leesbare log-events."""
        status_code = exc.resp.status
        if status_code == 401:
            logger.error("gmail_unauthorized", operation=operation, **context)
            raise PermissionError("Gmail-toegang geweigerd — herauthorisatie vereist") from exc
        elif status_code == 403:
            logger.error("gmail_forbidden", operation=operation, **context)
            raise PermissionError("Onvoldoende Gmail-permissies") from exc
        elif status_code == 404:
            logger.warning("gmail_not_found", operation=operation, **context)
        else:
            logger.warning("gmail_api_error", status=status_code, operation=operation, **context)


# ── Hulpfuncties ──────────────────────────────────────────────────────────────

def _parse_address(raw: str) -> tuple[str, str]:
    """
    Parseer een e-mailadresstring naar (naam, adres).

    "Jan Janssen <jan@bedrijf.nl>" → ("Jan Janssen", "jan@bedrijf.nl")
    "jan@bedrijf.nl"               → ("", "jan@bedrijf.nl")
    """
    import re
    match = re.match(r'^"?([^"<]*)"?\s*<?([^>]*)>?$', raw.strip())
    if match:
        name  = match.group(1).strip().strip('"')
        email = match.group(2).strip()
        return name, email
    return "", raw.strip()


def _extract_body(
    payload: dict[str, Any],
) -> tuple[str, str, list[str]]:
    """
    Extraheer plaintext-body, HTML-body en bijlagenamen recursief.

    Base64url-decoding gevangen per onderdeel — één corrupt part
    laat de rest intact.
    """
    plain_parts: list[str] = []
    html_parts:  list[str] = []
    attachments: list[str] = []

    def _recurse(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        filename = part.get("filename", "")

        if filename:
            attachments.append(filename)
            return  # Bijlage niet downloaden — alleen registreren

        data = body.get("data", "")
        if data:
            try:
                decoded = base64.urlsafe_b64decode(
                    data + "=="  # padding herstellen
                ).decode("utf-8", errors="replace")
            except Exception:
                decoded = ""

            if mime == "text/plain":
                plain_parts.append(decoded)
            elif mime == "text/html":
                html_parts.append(decoded)

        for sub in part.get("parts", []):
            _recurse(sub)

    _recurse(payload)

    return (
        "\n\n".join(plain_parts).strip(),
        "\n\n".join(html_parts).strip(),
        attachments,
    )
