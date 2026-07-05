"""
Gmail draft-aanmaker — uitsluitend compose-scope.

⛔ GEEN send()-methode in dit bestand.
⛔ GEEN smtp-verbinding.
⛔ Verzenden is altijd een menselijke handeling buiten dit systeem.

Scope: gmail.compose
ISO27001 A.9.4 — Toegangsbeheer, principe van minste privilege.
"""

import base64
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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

logger = structlog.get_logger(__name__)

_API_VERSION = "v1"


@dataclass
class DraftResult:
    """Resultaat van een succesvolle draft-aanmaak."""
    draft_id: str        # Gmail draft-ID
    message_id: str      # Onderliggend message-ID
    thread_id: str
    subject: str
    recipient: str


class GmailDraftCreator:
    """
    Maakt concepten aan in Gmail via de compose-scope.

    Nooit send() aanroepen — concepten worden handmatig door een
    medewerker verzonden vanuit de eigen Gmail-client.

    Gebruik:
        creator = GmailDraftCreator(credentials)
        result = creator.create_draft(
            to="klant@bedrijf.nl",
            subject="Re: Uw vraag over factuur #1234",
            body_plain="Geachte...",
            body_html="<p>Geachte...</p>",
            thread_id="abc123",   # optioneel: antwoord in thread
        )
    """

    def __init__(self, credentials: Any) -> None:
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
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def create_draft(
        self,
        to: str,
        subject: str,
        body_plain: str,
        body_html: str = "",
        thread_id: str | None = None,
        cc: list[str] | None = None,
    ) -> DraftResult:
        """
        Maak een concept aan in Gmail.

        Args:
            to:          Ontvanger (e-mailadres).
            subject:     Onderwerpregel.
            body_plain:  Platte tekst (vereist).
            body_html:   HTML-variant (optioneel).
            thread_id:   Gmail thread-ID voor antwoorden in dezelfde conversatie.
            cc:          CC-ontvangers.

        Returns:
            DraftResult met het Gmail draft-ID.

        Raises:
            HttpError: Bij API-fouten na alle retries.
            PermissionError: Als de scope onvoldoende is.
        """
        raw_message = _build_mime_message(
            to=to,
            subject=subject,
            body_plain=body_plain,
            body_html=body_html,
            cc=cc or [],
        )

        body: dict[str, Any] = {
            "message": {"raw": raw_message}
        }

        # Koppel aan bestaande thread als opgegeven
        if thread_id:
            body["message"]["threadId"] = thread_id

        svc = self._get_service()

        try:
            draft = (
                svc.users()
                .drafts()
                .create(userId="me", body=body)
                .execute()
            )
        except HttpError as exc:
            _handle_http_error(exc, operation="create_draft")
            raise

        result = DraftResult(
            draft_id=draft["id"],
            message_id=draft.get("message", {}).get("id", ""),
            thread_id=draft.get("message", {}).get("threadId", ""),
            subject=subject,
            recipient=to,
        )

        logger.info(
            "gmail_draft_created",
            draft_id=result.draft_id,
            thread_id=result.thread_id,
            # Nooit `to` of `subject` loggen — kunnen PII bevatten
        )

        return result

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def update_draft(
        self,
        draft_id: str,
        to: str,
        subject: str,
        body_plain: str,
        body_html: str = "",
        thread_id: str | None = None,
    ) -> DraftResult:
        """
        Vervang de inhoud van een bestaand concept.

        Gebruik wanneer een medewerker het concept bewerkt heeft en
        de nieuwe versie terug naar Gmail moet.
        """
        raw_message = _build_mime_message(
            to=to,
            subject=subject,
            body_plain=body_plain,
            body_html=body_html,
        )

        body: dict[str, Any] = {
            "message": {"raw": raw_message}
        }
        if thread_id:
            body["message"]["threadId"] = thread_id

        svc = self._get_service()

        try:
            draft = (
                svc.users()
                .drafts()
                .update(userId="me", id=draft_id, body=body)
                .execute()
            )
        except HttpError as exc:
            _handle_http_error(exc, operation="update_draft")
            raise

        logger.info("gmail_draft_updated", draft_id=draft_id)

        return DraftResult(
            draft_id=draft["id"],
            message_id=draft.get("message", {}).get("id", ""),
            thread_id=draft.get("message", {}).get("threadId", ""),
            subject=subject,
            recipient=to,
        )

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=2, max=30),  # FIX C-2: max(30) → max=30
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def get_draft(self, draft_id: str) -> dict[str, Any]:
        """
        Haal een bestaand concept op (voor verificatie).

        Returns:
            Ruwe Gmail API draft-respons.
        """
        svc = self._get_service()

        try:
            return (
                svc.users()
                .drafts()
                .get(userId="me", id=draft_id, format="full")
                .execute()
            )
        except HttpError as exc:
            _handle_http_error(exc, operation="get_draft")
            raise

    # ── Bewust niet geïmplementeerd ───────────────────────────────────────────
    # def send(self): ...
    #
    # ARCHITECTURELE BESLISSING: Dit systeem heeft geen send-methode.
    # Verzenden is altijd een menselijke handeling via de eigen Gmail-client.
    # De goedkeur-actie in het dashboard opent een mailto:-link of de
    # Gmail-webinterface, maar verstuurt nooit via deze API-client.


# ── Hulpfuncties ──────────────────────────────────────────────────────────────

def _build_mime_message(
    to: str,
    subject: str,
    body_plain: str,
    body_html: str = "",
    cc: list[str] | None = None,
) -> str:
    """
    Bouw een MIME-bericht en codeer het als base64url.

    Multipart/alternative als HTML opgegeven, anders text/plain.
    """
    if body_html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body_plain, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))
    else:
        msg = MIMEText(body_plain, "plain", "utf-8")

    msg["To"]      = to
    msg["Subject"] = subject

    if cc:
        msg["Cc"] = ", ".join(cc)

    raw_bytes = msg.as_bytes()
    return base64.urlsafe_b64encode(raw_bytes).decode("utf-8")


def _handle_http_error(exc: HttpError, operation: str) -> None:
    """Vertaal Gmail API-statuscodes naar leesbare log-events."""
    status = exc.resp.status
    if status == 401:
        logger.error("gmail_unauthorized", operation=operation)
        raise PermissionError("Gmail-toegang geweigerd — herauthorisatie vereist") from exc
    elif status == 403:
        logger.error("gmail_forbidden", operation=operation)
        raise PermissionError(
            "Onvoldoende Gmail-permissies voor draft-aanmaak"
        ) from exc
    elif status == 429:
        logger.warning("gmail_rate_limited", operation=operation)
    else:
        logger.warning("gmail_api_error", status=status, operation=operation)
