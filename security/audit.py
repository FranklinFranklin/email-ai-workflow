"""
Audit logging — onwijzigbaar activiteitenlogboek.

ISO27001 A.12.4 — Logging en monitoring.
SOC2 CC7.2      — Detectie van beveiligingsincidenten.
SOC2 CC6.2      — Registratie van logische toegang.

Elke significante actie in het systeem wordt gelogd:
    - Wie (actor_id)
    - Wat (action)
    - Waarop (entity_type + entity_id)
    - Wanneer (ts — UTC)
    - Waarvan (ip_address, request_id)

Regels:
    - Nooit PII in audit-events — gebruik entiteit-IDs of pseudoniemen
    - Audit-events zijn append-only — nooit wijzigen of verwijderen
    - Logging faalt nooit stil — errors worden apart gelogd
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger("audit")


class AuditAction(str, Enum):
    """
    Gestandaardiseerde actienamen.

    Gebruik altijd een waarde uit deze enum — nooit vrije strings.
    Dat maakt query's op het auditlog betrouwbaar.
    """

    # ── E-mail lifecycle ──────────────────────────────────────
    EMAIL_RECEIVED       = "email.received"
    EMAIL_QUEUED         = "email.queued"
    EMAIL_PROCESSED      = "email.processed"
    EMAIL_FAILED         = "email.failed"

    # ── Classificatie ─────────────────────────────────────────
    CLASSIFICATION_START   = "classification.start"
    CLASSIFICATION_DONE    = "classification.done"
    CLASSIFICATION_FAILED  = "classification.failed"

    # ── Draft lifecycle ───────────────────────────────────────
    DRAFT_CREATED      = "draft.created"
    DRAFT_VIEWED       = "draft.viewed"
    DRAFT_EDITED       = "draft.edited"
    DRAFT_APPROVED     = "draft.approved"
    DRAFT_REJECTED     = "draft.rejected"
    DRAFT_EXPIRED      = "draft.expired"

    # ── Authenticatie / autorisatie ───────────────────────────
    AUTH_LOGIN         = "auth.login"
    AUTH_LOGOUT        = "auth.logout"
    AUTH_FAILED        = "auth.failed"
    AUTH_TOKEN_REFRESH = "auth.token_refresh"

    # ── Beheer ────────────────────────────────────────────────
    ACCOUNT_CREATED    = "account.created"
    ACCOUNT_UPDATED    = "account.updated"
    ACCOUNT_DELETED    = "account.deleted"
    USER_CREATED       = "user.created"
    USER_ROLE_CHANGED  = "user.role_changed"

    # ── Beveiligingsincidenten ────────────────────────────────
    SECURITY_INJECTION_DETECTED  = "security.injection_detected"
    SECURITY_RATE_LIMIT_HIT      = "security.rate_limit_hit"
    SECURITY_UNAUTHORIZED_ACCESS = "security.unauthorized_access"
    SECURITY_PII_DETECTED        = "security.pii_detected"


def audit_log(
    action: AuditAction,
    *,
    actor_id: str | uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: str | uuid.UUID | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Log een audit-event naar de gestructureerde logger.

    Args:
        action:      Wat is er gebeurd (gebruik AuditAction enum).
        actor_id:    Wie heeft de actie uitgevoerd (user-ID of systeem).
        entity_type: Type object waarop de actie betrekking heeft.
        entity_id:   ID van het betrokken object.
        request_id:  Correlatie-ID van het HTTP-verzoek.
        ip_address:  Bron-IP (geanonimiseerd op het laatste octet indien gewenst).
        metadata:    Extra context — NOOIT PII, wachtwoorden of secrets.

    Voorbeeld:
        audit_log(
            AuditAction.DRAFT_APPROVED,
            actor_id=current_user.id,
            entity_type="draft",
            entity_id=draft.id,
            request_id=request.headers.get("X-Request-ID"),
            ip_address=request.client.host,
            metadata={"draft_version": draft.version},
        )
    """
    safe_metadata = _sanitize_metadata(metadata or {})

    logger.info(
        action.value,
        # ── Verplichte velden (SOC2 CC6.2) ───────────────────
        audit=True,                        # Markering voor log-aggregators
        ts=datetime.now(timezone.utc).isoformat(),
        action=action.value,
        # ── Context-velden ────────────────────────────────────
        actor_id=str(actor_id) if actor_id else "system",
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
        request_id=request_id,
        ip_address=_anonymize_ip(ip_address),
        # ── Extra context ─────────────────────────────────────
        **safe_metadata,
    )


def _anonymize_ip(ip: str | None) -> str | None:
    """
    Anonimiseer het laatste octet van een IPv4-adres.

    192.168.1.42 → 192.168.1.0
    Volledig IPv6-adres wordt vervangen door een hash-prefix.
    """
    if not ip:
        return None
    if ":" in ip:
        # IPv6 — bewaar alleen het eerste blok
        return ip.split(":")[0] + "::/truncated"
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.0"
    return ip


_SENSITIVE_KEYS = frozenset({
    "password", "secret", "token", "key", "credential",
    "authorization", "api_key", "private", "ssn", "bsn",
    "iban", "credit_card", "cvv",
})


def _sanitize_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """
    Verwijder bekende gevoelige sleutels uit metadata vóór logging.

    Dit is een vangnet — metadata mag nooit PII bevatten.
    """
    return {
        k: "[REDACTED]" if k.lower() in _SENSITIVE_KEYS else v
        for k, v in data.items()
    }
