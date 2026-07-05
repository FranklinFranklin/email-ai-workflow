"""
Generieke basis-repository met CRUD-operaties.

Alle domein-repositories erven hiervan en voegen
specifieke queries toe zonder boilerplate te herhalen.
"""

from typing import Generic, TypeVar, Type
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import Base

ModelT = TypeVar("ModelT", bound=Base)  # type: ignore[type-arg]


class BaseRepository(Generic[ModelT]):
    """Generieke async repository voor SQLAlchemy-modellen."""

    def __init__(self, model: Type[ModelT], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    async def get_by_id(self, record_id: UUID) -> ModelT | None:
        result = await self.session.execute(
            select(self.model).where(self.model.id == record_id)
        )
        return result.scalar_one_or_none()

    async def get_all(self, limit: int = 100, offset: int = 0) -> list[ModelT]:
        result = await self.session.execute(
            select(self.model).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def create(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()   # ID ophalen zonder te committen
        await self.session.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)
        await self.session.flush()
