"""SQLAlchemy async DB接続（SQLite/PostgreSQL 両対応）"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

engine = create_async_engine(
    settings.database_url,
    poolclass=NullPool if not _is_sqlite else None,
    connect_args={"check_same_thread": False} if _is_sqlite else {
        "prepare_threshold": 0,  # pgbouncer（Supabase接続プーラー）対応
    },
    echo=(settings.app_env == "development"),
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
