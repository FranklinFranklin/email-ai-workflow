"""
Webhook-route voor Gmail Push Notifications.

POST /api/v1/webhooks/gmail
    - Verifieert Pub/Sub bearer-token uit Authorization-header (FIX H-6)
    - Parst notificatie
    - Zet nieuwe berichten in de verwerkingswachtrij
    - Antwoord altijd 200 (Pub/Sub herprobeert anders)

FIX H-6: Token komt nu uit de Authorization-header ("Bearer <token>"),
niet uit de URL-query-parameter. Query-params verschijnen in access-logs,
CDN-logs en Referer-headers — tokens zijn dan harvestable.

Configureer de Pub/Sub-subscriptie met:
    Authentication type: Bearer token
    Audience / token: <PUBSUB_VERIFICATION_TOKEN waarde>
"""

import structlog
from fastapi import APIRouter, BackgroundTasks, Request, status

from api.middleware import _get_client_ip
from security.audit import AuditAction, audit_log
from services.gmail.webhook import GmailWebhookHandler

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhooks")

# Prefix die Google Pub/Sub toevoegt aan het Authorization-header-token
_BEARER_PREFIX = "Bearer "


def _extract_bearer_token(authorization: str | None) -> str | None:
    """
    Extraheer het bearer-token uit de Authorization-header.

    Returns None als de header ontbreekt of het formaat niet klopt.
    """
    if not authorization:
        return None
    if not authorization.startswith(_BEARER_PREFIX):
        return None
    token = authorization[len(_BEARER_PREFIX):].strip()
    return token or None


@router.post(
    "/gmail",
    status_code=status.HTTP_200_OK,
    summary="Gmail Push Notification ontvanger",
    description=(
        "Ontvangt Pub/Sub-notificaties van Gmail. "
        "Vereist een geldig bearer-token in de Authorization-header."
    ),
)
async def gmail_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Verwerk een inkomende Gmail Pub/Sub-notificatie.

    Pub/Sub verwacht altijd een 2xx-respons. Stuur 200 terug,
    ook bij verwerkingsfouten — anders blijft Pub/Sub opnieuw proberen.
    """
    # ── FIX H-6: Token uit Authorization-header, niet uit URL ────────────────
    authorization = request.headers.get("Authorization")
    token = _extract_bearer_token(authorization)

    if not token or not GmailWebhookHandler.verify_pubsub_token(token):
        audit_log(
            AuditAction.SECURITY_UNAUTHORIZED_ACCESS,
            ip_address=_get_client_ip(request),
            request_id=request.headers.get("X-Request-ID"),
            metadata={"endpoint": "/webhooks/gmail", "reason": "invalid_token"},
        )
        logger.warning("gmail_webhook_invalid_token")
        # Altijd 200 — anders herprobeert Pub/Sub oneindig
        return {"status": "ignored"}

    # ── Notificatie parsen ───────────────────────────────────────────────────
    raw_body = await request.body()
    if not raw_body:
        return {"status": "empty"}

    try:
        notification = GmailWebhookHandler.parse_notification(raw_body)
    except ValueError as exc:
        logger.warning("gmail_webhook_parse_error", error=str(exc))
        return {"status": "parse_error"}

    # ── Achtergrondverwerking ────────────────────────────────────────────────
    background_tasks.add_task(
        _enqueue_new_messages,
        history_id=notification.history_id,
        email_address=notification.email_address,
        request_id=request.headers.get("X-Request-ID", ""),
    )

    return {"status": "accepted"}


async def _enqueue_new_messages(
    history_id: str,
    email_address: str,
    request_id: str,
) -> None:
    """
    Achtergrondtaak: haal nieuwe berichten-IDs op en zet ze in de wachtrij.
    Fouten stoppen de Pub/Sub-afhandeling niet.
    """
    try:
        logger.info(
            "gmail_webhook_processing",
            history_id=history_id,
            request_id=request_id,
            # Geen email_address loggen — PII
        )
        audit_log(
            AuditAction.EMAIL_QUEUED,
            metadata={"history_id": history_id, "source": "gmail_webhook"},
            request_id=request_id,
        )
    except Exception:
        logger.exception("gmail_webhook_background_task_failed", history_id=history_id)
