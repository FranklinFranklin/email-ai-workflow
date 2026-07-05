"""
Draft repository.

Beheert alle conceptversies. Verzenden is nooit een status hier —
dat is architectureel uitgesloten.
"""

from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.base import BaseRepository
from models.db.draft import Draft, DraftStatus


class DraftRepository(BaseRepository[Draft]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Draft, session)

    async def get_by_email(self, email_id: UUID) -> list[Draft]:
        """Haal alle conceptversies op voor een e-mail (nieuwste eerst)."""
        result = await self.session.execute(
            select(Draft)
            .where(Draft.email_id == email_id)
            .order_by(Draft.version.desc())
        )
        return list(result.scalars().all())

    async def get_pending_review(
        self, account_id: UUID, limit: int = 50
    ) -> list[Draft]:
        """Concepten die wachten op menselijke beoordeling."""
        result = await self.session.execute(
            select(Draft)
            .join(Draft.email)
            .where(Draft.email.has(account_id=account_id))
            .where(Draft.status == DraftStatus.DRAFT)
            .order_by(Draft.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def next_version(self, email_id: UUID) -> int:
        """Bepaal het volgende versienummer voor een e-mail."""
        result = await self.session.execute(
            select(Draft.version)
            .where(Draft.email_id == email_id)
            .order_by(Draft.version.desc())
            .limit(1)
        )
        current = result.scalar_one_or_none()
        return (current or 0) + 1

    async def supersede_previous(self, email_id: UUID, current_id: UUID) -> None:
        """Markeer oudere actieve concepten als vervangen."""
        drafts = await self.get_by_email(email_id)
        for draft in drafts:
            if draft.id != current_id and draft.status == DraftStatus.DRAFT:
                draft.status = DraftStatus.SUPERSEDED
        await self.session.flush()
