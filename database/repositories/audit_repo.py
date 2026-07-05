"""
Audit repository — append-only, onafhankelijk van business-transacties.

FIX H-3: Audit-entries worden nu in een EIGEN sessie gecommit, los van de
business-logica-sessie. Als de business-transactie teruggedraaid wordt,
blijft de audit-entry behouden. Dit is verplicht voor SOC2 CC7.2 en
ISO27001 A.12.4 — audit-trails mogen nooit stil verdwijnen.

Gebruik:
    # In een route of service:
    async with AuditRepository.get_audit_session() as audit_session:
        repo = AuditRepository(audit_session)
        await repo.log(AuditAction.DRAFT_APPROVED, ...)
    # Commit vindt plaats bij verlaten van de context manager,
    # ongeacht wat er daarna gebeurt met de business-sessie.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import UUID

import structlog
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models.db.audit_log import AuditLog, AuditAction

logger = structlog.get_logger(__name__)

# Aparte engine voor audit-log — volledig onafhankelijk van de hoofd-engine
_audit_engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    echo=False,  # Nooit audit-queries loggen — kunnen context onthullen
)

_AuditSessionFactory = async_sessionmaker(
    bind=_audit_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class AuditRepository:
    """
    Schrijf-only repository — geen update/delete methoden.

    Gebruik altijd via `AuditRepository.get_audit_session()` zodat
    de commit onafhankelijk van de business-transactie plaatsvindt.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    @asynccontextmanager
    async def get_audit_session() -> AsyncGenerator["AuditRepository", None]:
        """
        Context manager die een onafhankelijke audit-sessie levert.

        De sessie wordt direct gecommit bij verlaten — rollback in de
        business-sessie heeft hier geen effect op.
        """
        async with _AuditSessionFactory() as session:
            try:
                yield AuditRepository(session)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.error("audit_log_write_failed")
                raise

    async def log(
        self,
        action: AuditAction,
        actor_id: UUID | None,
        entity_type: str | None,
        entity_id: UUID | None,
        metadata: dict | None = None,
        ip_address: str | None = None,
    ) -> None:
        """
        Voeg een audit-event toe. Nooit PII in metadata opnemen.
        Gebruik pseudoniemen of entiteit-IDs.

        Gooit nooit een exception naar de aanroeper — audit-fouten
        worden gelogd maar stoppen de business-flow niet.
        """
        try:
            await self.session.execute(
                insert(AuditLog).values(
                    action=action,
                    actor_id=actor_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    metadata=metadata or {},
                    ip_address=ip_address,
                    ts=datetime.now(timezone.utc),
                )
            )
            await self.session.flush()  # Directe persistentie binnen de audit-sessie
        except Exception:
            logger.error(
                "audit_entry_write_failed",
                action=action.value if action else None,
                # Nooit metadata loggen — kan gevoelige context bevatten
            )
