"""テスト共通フィクスチャ

MDM APIテスト用のインメモリ SQLite + FastAPI TestClient を提供する。
各テストモジュールは `client` と `admin_key` フィクスチャをインポートして使う。
"""
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, get_db
from main import app
from config import settings

_TEST_DB_PATH = Path("./test_mdm_temp.db")
_TEST_DB_URL = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"


@pytest_asyncio.fixture(scope="module")
async def client():
    """テスト用DBでオーバーライドしたFastAPIクライアント"""
    engine = create_async_engine(
        _TEST_DB_URL,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    _TEST_DB_PATH.unlink(missing_ok=True)


@pytest_asyncio.fixture(scope="module")
def admin_key():
    return settings.admin_api_key
