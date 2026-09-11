"""
SQLAlchemy ORM-model voor conceptberichten (Drafts).

ISO27001 A.9.4 — Toegangsbeheer en least privilege.
Verzenden is nooit een status — uitsluitend draft/approved/rejected.
"""

from datetime import datetime, timezone
from enum import Enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum as SAEnum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.connection import Base

if TYPE_CHECKING:
    from models.db.email import Email


class DraftStatus(str, Enum):
    """
    Toegestane statussen voor een draft.
    Architectureel géén 'sent' status.
    """
    DRAFT      = "draft"
    SUPERSEDED = "superseded"
    APPROVED   = "approved"
    REJECTED   = "rejected"


class Draft(Base):
    """
    Conceptversie voor een e-mail.
    Elke wijziging creëert een nieuwe versie (version++).
    """

    __tablename__ = "drafts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("emails.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    status: Mapped[DraftStatus] = mapped_column(
        SAEnum(DraftStatus, name="draft_status_enum"),
        default=DraftStatus.DRAFT,
        nullable=False,
        index=True,
    )

    # Versleutelde draft-inhoud (Fernet ciphertext)
    draft_text_enc: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    review_status: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relatie terug naar e-mail
    email: Mapped["Email"] = relationship(
        "Email",
        back_populates="drafts",
    )
