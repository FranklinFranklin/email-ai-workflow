"""
E-mail repository — lees-/schrijfoperaties voor opgeslagen e-mails.

E-mails zijn immutable na ingestie: nooit updaten, alleen lezen of soft-deleten.
"""

from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.base import BaseRepository
from models.db.email import Email


class EmailRepository(BaseRepository[Email]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Email, session)

    async def get_by_message_id(self, message_id: str) -> Email | None:
        """Deduplicatie: controleer of e-mail al geïngesteerd is."""
        result = await self.session.execute(
            select(Email).where(Email.message_id == message_id)
        )
        return result.scalar_one_or_none()

    async def get_unprocessed(self, account_id: UUID, limit: int = 50) -> list[Email]:
        """Haal e-mails op die nog niet geclassificeerd zijn."""
        result = await self.session.execute(
            select(Email)
            .where(Email.account_id == account_id)
            .where(Email.classified == False)  # noqa: E712
            .order_by(Email.received_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_classified(self, email_id: UUID) -> None:
        """Markeer e-mail als geclassificeerd (na succesvolle verwerking)."""
        email = await self.get_by_id(email_id)
        if email:
            email.classified = True
            await self.session.flush()
