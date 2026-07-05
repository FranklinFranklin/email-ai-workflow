"""
Database-verbinding — async SQLAlchemy 2.0 + asyncpg.

Geen modellen hier; alleen de engine, sessionfactory en base-klasse.
Modellen leven in models/db/*.py.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings


engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,      # test verbinding vóór gebruik uit de pool
    pool_recycle=3600,        # herstel connections elk uur
    echo=settings.APP_ENV == "development",
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # objecten bruikbaar ná commit
    autoflush=False,
)


class Base(DeclarativeBase):
    """Gemeenschappelijke basisklasse voor alle ORM-modellen."""
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI-dependency — levert een request-scoped database-sessie.

    Commit bij succes, rollback bij exception. Sluit altijd af.

    Gebruik:
        @router.get("/")
        async def handler(db: AsyncSession = Depends(get_session)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
