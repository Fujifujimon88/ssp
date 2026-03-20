"""SQLAlchemy async DB接続（SQLite/PostgreSQL 両対応）"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
_is_asyncpg = "+asyncpg" in settings.database_url

if _is_sqlite:
    _connect_args = {"check_same_thread": False}
elif _is_asyncpg:
    _connect_args = {"statement_cache_size": 0}   # asyncpg: pgbouncer対応
else:
    _connect_args = {"prepare_threshold": 0}       # psycopg3: pgbouncer対応

engine = create_async_engine(
    settings.database_url,
    poolclass=NullPool if not _is_sqlite else None,
    connect_args=_connect_args,
    echo=(settings.app_env == "development"),
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
