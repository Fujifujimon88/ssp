"""
dsp_engine bid shading のユニットテスト（優先タスク #2）

first-price 環境で過払いを防ぐ bid shading を検証する。
  - compute_shaded_bid : 過去落札価格の分位点ベース（純粋関数）
  - fetch_past_cleared_prices : 過去落札価格の DB 取得

実行: cd ssp_platform && pytest tests/test_shading.py -v
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database import Base
from db_models import DspSpendLogDB
from dsp_engine.shading import (
    COLD_START_THRESHOLD,
    compute_shaded_bid,
    fetch_past_cleared_prices,
)


# ── フィクスチャ（test_dsp_engine.py と同一パターン）─────────────

@pytest_asyncio.fixture
async def db():
    """インメモリ SQLite（StaticPool で単一コネクション維持）"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ── compute_shaded_bid（純粋関数）──────────────────────────────

def test_compute_shaded_bid_cold_start_returns_raw():
    """過去落札が COLD_START_THRESHOLD 未満なら shading せず raw を返す"""
    past = [100.0] * (COLD_START_THRESHOLD - 1)
    assert compute_shaded_bid(500.0, past, bidfloor_jpy=10.0) == 500.0


def test_compute_shaded_bid_uses_p50():
    """十分な履歴があれば P50 分位点が shaded bid になる"""
    # past = 1..100 ; idx = int(100 * 50 / 100) = 50 ; sorted[50] = 51.0
    past = [float(i) for i in range(1, 101)]
    assert compute_shaded_bid(1000.0, past, bidfloor_jpy=0.0) == 51.0


def test_compute_shaded_bid_never_exceeds_raw_bid():
    """P50 が raw_bid を超えても raw_bid で上限キャップされる（shading で増額しない）"""
    past = [float(i) for i in range(1000, 1100)]  # P50 は 1000台
    assert compute_shaded_bid(300.0, past, bidfloor_jpy=0.0) == 300.0


def test_compute_shaded_bid_floor_clamp():
    """P50 が bidfloor を下回っても bidfloor が下限になる"""
    past = [1.0] * 50
    assert compute_shaded_bid(500.0, past, bidfloor_jpy=120.0) == 120.0


# ── fetch_past_cleared_prices（DB）─────────────────────────────

@pytest.mark.asyncio
async def test_fetch_past_cleared_prices(db):
    """campaign_id 単位で cleared_price_jpy のみを取得する（他キャンペーンは混入しない）"""
    for i in range(5):
        db.add(DspSpendLogDB(
            campaign_id="camp-x",
            click_token=f"tok-{i}",
            cleared_price_jpy=float(100 + i),
        ))
    db.add(DspSpendLogDB(
        campaign_id="camp-other",
        click_token="tok-other",
        cleared_price_jpy=999.0,
    ))
    await db.commit()

    prices = await fetch_past_cleared_prices(db, "camp-x")
    assert len(prices) == 5
    assert 999.0 not in prices
    assert sorted(prices) == [100.0, 101.0, 102.0, 103.0, 104.0]


@pytest.mark.asyncio
async def test_fetch_past_cleared_prices_respects_limit(db):
    """limit を超える履歴は件数で切り取られる"""
    for i in range(15):
        db.add(DspSpendLogDB(
            campaign_id="camp-lim",
            click_token=f"lt-{i}",
            cleared_price_jpy=float(i),
        ))
    await db.commit()

    prices = await fetch_past_cleared_prices(db, "camp-lim", limit=10)
    assert len(prices) == 10


@pytest.mark.asyncio
async def test_fetch_past_cleared_prices_empty(db):
    """落札履歴のないキャンペーンは空リストを返す"""
    prices = await fetch_past_cleared_prices(db, "camp-none")
    assert prices == []
