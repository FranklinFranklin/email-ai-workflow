"""
SQLAlchemy ORM-model voor e-mails.

ISO27001 A.8.2 — Dataclassificatie en versleutelde opslag.
GDPR Art. 32    — Beveiliging van verwerking.
"""

from datetime import datetime, timezone
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.connection import Base

if TYPE_CHECKING:
    from models.db.draft import Draft


class Email(Base):
    """
    E-mail entiteit — onwijzigbaar na ingestie.
    Gevoelige velden worden versleuteld opgeslagen (kolomniveau).
    """

    __tablename__ = "emails"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    message_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    # Versleutelde velden (Fernet ciphertext)
    subject_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_plain_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    classified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relaties
    drafts: Mapped[list["Draft"]] = relationship(
        "Draft",
        back_populates="email",
        cascade="all, delete-orphan",
    )
