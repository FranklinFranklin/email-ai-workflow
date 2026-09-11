"""
SQLAlchemy ORM-model voor onwijzigbare audit-logs.

ISO27001 A.12.4 — Logging en monitoring.
SOC2 CC7.2      — Detectie van beveiligingsincidenten.
"""

from datetime import datetime, timezone
import uuid
from typing import Any

from sqlalchemy import DateTime, JSON, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.connection import Base
from security.audit import AuditAction

# Herexporteer AuditAction zodat database/repositories/audit_repo.py
# deze direct uit models.db.audit_log kan importeren
__all__ = ["AuditLog", "AuditAction"]


class AuditLog(Base):
    """
    Append-only audit-log tabel.
    Bevat geen PII, wachtwoorden of ruwe payloads.
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    entity_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        index=True,
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        nullable=False,
    )
    ip_address: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
