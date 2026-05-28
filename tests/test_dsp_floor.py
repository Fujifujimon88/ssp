"""
dsp #11 phase 1: dsp_floor_price_history テーブル存在検証 (Red)
dsp #11 phase 2: compute_dynamic_floor 純粋関数 テスト (Red)

検証項目 (phase 1):
  1. PRAGMA table_info でテーブルが存在
  2. 期待カラム 8 個が全部存在: id, publisher_id, floor_usd, floor_jpy,
     win_rate, bid_density, sample_count, computed_at
  3. 複合インデックス ix_dsp_floor_hist_pub_computed が存在
  4. インデックスのカラム順が (publisher_id, computed_at)

モデル未定義のため FAIL する (Red)。
"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database import Base
from dsp_engine.floor import compute_dynamic_floor, DEFAULT_FLOOR_CONFIG, FloorConfig


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


# ── dsp #11 phase 1: dsp_floor_price_history テーブル存在検証 (Red) ──

@pytest.mark.asyncio
async def test_floor_price_history_table_exists(db):
    """
    dsp_floor_price_history テーブルが存在し、期待カラム 8 個 +
    複合インデックス ix_dsp_floor_hist_pub_computed が揃っていることを検証する。

    モデル未定義のため FAIL する (Red)。
    """
    # 1. テーブルが存在するか確認
    rows = (await db.execute(text("PRAGMA table_info('dsp_floor_price_history')"))).fetchall()
    assert rows, (
        "テーブル 'dsp_floor_price_history' が存在しない。"
        "PRAGMA table_info の結果が空。"
    )

    # 2. 期待カラム 8 個が全部存在
    cols = {row[1] for row in rows}  # col 1 = column name
    expected_cols = {"id", "publisher_id", "floor_usd", "floor_jpy",
                     "win_rate", "bid_density", "sample_count", "computed_at"}
    for col in expected_cols:
        assert col in cols, f"カラム '{col}' が存在しない。現在のカラム: {cols}"

    # 3. 複合インデックス ix_dsp_floor_hist_pub_computed が存在
    index_rows = (
        await db.execute(text("PRAGMA index_list('dsp_floor_price_history')"))
    ).fetchall()
    index_names = [r[1] for r in index_rows]  # col 1 = index name
    assert "ix_dsp_floor_hist_pub_computed" in index_names, (
        "インデックス 'ix_dsp_floor_hist_pub_computed' が存在しない。"
        f"現在の index_list: {index_names}"
    )

    # 4. インデックスのカラム順が (publisher_id, computed_at)
    info_rows = (
        await db.execute(text("PRAGMA index_info('ix_dsp_floor_hist_pub_computed')"))
    ).fetchall()
    actual_cols = [r[2] for r in info_rows]  # seqno 順 (= index_rank 順)
    assert actual_cols == ["publisher_id", "computed_at"], (
        "インデックス 'ix_dsp_floor_hist_pub_computed' のカラム構成が不一致。"
        f"expected=['publisher_id', 'computed_at'], actual={actual_cols}"
    )


# ── dsp #11 phase 2: compute_dynamic_floor 純粋関数テスト (Red) ──


def test_compute_dynamic_floor_cold_start():
    """sample 数 < FLOOR_COLD_START_MIN (=10) なら None を返す"""
    prices = [100.0] * 9  # 9 件 < 10
    result = compute_dynamic_floor(prices, win_rate=0.3, bid_density=1.0, jpy_per_usd=150.0)
    assert result is None


def test_compute_dynamic_floor_p50():
    """10 件 / win_rate=TARGET / density=1.0 のとき floor = P50_jpy / jpy_per_usd"""
    # P50 (statistics.median) of [10..100 step 10] = (50+60)/2 = 55
    prices = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    result = compute_dynamic_floor(prices, win_rate=0.3, bid_density=1.0, jpy_per_usd=150.0)
    # win_rate_factor = 1.0, density_factor = 1.0 → floor_jpy = 55.0 → USD = 55/150
    assert result == pytest.approx(55.0 / 150.0)


def test_compute_dynamic_floor_high_win_rate():
    """win_rate > TARGET なら floor が上振れする"""
    prices = [50.0] * 10  # P50 = 50
    baseline = compute_dynamic_floor(prices, win_rate=0.3, bid_density=1.0, jpy_per_usd=150.0)
    high = compute_dynamic_floor(prices, win_rate=0.6, bid_density=1.0, jpy_per_usd=150.0)
    assert high is not None and baseline is not None
    assert high > baseline


def test_compute_dynamic_floor_low_win_rate():
    """win_rate < TARGET なら floor が下振れする (clamp 下限 0.5 が効く境界も確認)"""
    prices = [50.0] * 10
    baseline = compute_dynamic_floor(prices, win_rate=0.3, bid_density=1.0, jpy_per_usd=150.0)
    low = compute_dynamic_floor(prices, win_rate=0.1, bid_density=1.0, jpy_per_usd=150.0)
    assert low is not None and baseline is not None
    assert low < baseline
    # 極端に低い win_rate でも clamp 下限 0.5 で止まる: factor=clamp(1+(0-0.3)*0.5, 0.5, 2.0)=clamp(0.85,...) → 0.85
    # 0 でも 0.85 で止まる。clamp が機能する境界として下限テスト:
    extreme_low = compute_dynamic_floor(prices, win_rate=-5.0, bid_density=1.0, jpy_per_usd=150.0)
    # win_rate_factor = clamp(1 + (-5 - 0.3)*0.5, 0.5, 2.0) = clamp(-1.65, 0.5, 2.0) = 0.5
    # floor_jpy = 50 * 0.5 * 1.0 = 25 → USD = 25/150
    assert extreme_low == pytest.approx(25.0 / 150.0)


def test_compute_dynamic_floor_high_density():
    """bid_density > 1 なら floor が上振れする"""
    prices = [50.0] * 10
    baseline = compute_dynamic_floor(prices, win_rate=0.3, bid_density=1.0, jpy_per_usd=150.0)
    dense = compute_dynamic_floor(prices, win_rate=0.3, bid_density=3.0, jpy_per_usd=150.0)
    assert dense is not None and baseline is not None
    assert dense > baseline
    # density_factor = clamp(1 + max(0, 3-1)*0.1, 1.0, 1.5) = 1.2
    # floor_jpy = 50 * 1.0 * 1.2 = 60 → USD = 60/150
    assert dense == pytest.approx(60.0 / 150.0)


def test_compute_dynamic_floor_returns_usd():
    """戻り値は jpy_per_usd で割った USD CPM"""
    prices = [150.0] * 10  # P50 = 150
    # win_rate_factor=1.0, density_factor=1.0 → floor_jpy = 150 → USD = 150/150 = 1.0
    result = compute_dynamic_floor(prices, win_rate=0.3, bid_density=1.0, jpy_per_usd=150.0)
    assert result == pytest.approx(1.0)
    # jpy_per_usd を変えると USD 値も変わる
    result2 = compute_dynamic_floor(prices, win_rate=0.3, bid_density=1.0, jpy_per_usd=100.0)
    assert result2 == pytest.approx(1.5)


def test_compute_dynamic_floor_clamp_upper():
    """win_rate_factor の上限 2.0 と density_factor の上限 1.5 が効く"""
    prices = [100.0] * 10  # P50 = 100
    # win_rate=10.0 → factor = clamp(1 + (10-0.3)*0.5, 0.5, 2.0) = clamp(5.85, ...) = 2.0
    # density=100 → factor = clamp(1 + 99*0.1, 1.0, 1.5) = clamp(10.9, ...) = 1.5
    # floor_jpy = 100 * 2.0 * 1.5 = 300 → USD = 300/150 = 2.0
    result = compute_dynamic_floor(prices, win_rate=10.0, bid_density=100.0, jpy_per_usd=150.0)
    assert result == pytest.approx(2.0)


# ── dsp #11 phase 3: バッチ + lifespan テスト (Red) ──

import uuid as _uuid_mod
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from db_models import DspBidLogDB, DspFloorPriceHistoryDB, DspSpendLogDB
from dsp_engine.floor_batch import (
    _floor_cache,
    get_dynamic_floor,
    prime_floor_cache,
    recompute_floor_prices,
)


@pytest_asyncio.fixture(autouse=True)
async def _clear_floor_cache():
    """各テスト前後で module-level cache をクリアして test isolation を保証"""
    _floor_cache.clear()
    yield
    _floor_cache.clear()


def _make_spend(campaign_id: str, publisher_id: str, cleared_price_jpy: float) -> DspSpendLogDB:
    return DspSpendLogDB(
        campaign_id=campaign_id,
        click_token=_uuid_mod.uuid4().hex,
        cleared_price_jpy=cleared_price_jpy,
        publisher_id=publisher_id,
    )


def _make_bid_log(outcome: str = "bid", candidate_count: int = 2) -> DspBidLogDB:
    return DspBidLogDB(outcome=outcome, candidate_count=candidate_count)


@pytest.mark.asyncio
async def test_recompute_floor_writes_history(db):
    """spend_log 10 件 seed → recompute → DspFloorPriceHistoryDB に 1 行 INSERT"""
    pub_id = "pub_test_phase3_1"
    camp_id = "camp_phase3_1"
    for i in range(10):
        db.add(_make_spend(camp_id, pub_id, cleared_price_jpy=float(100 + i * 10)))
    for _ in range(5):
        db.add(_make_bid_log(outcome="bid", candidate_count=3))
    await db.commit()

    result = await recompute_floor_prices(db)

    assert pub_id in result
    assert result[pub_id] > 0

    rows = (
        await db.execute(
            select(DspFloorPriceHistoryDB).where(
                DspFloorPriceHistoryDB.publisher_id == pub_id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].floor_usd > 0
    assert rows[0].floor_jpy > 0


@pytest.mark.asyncio
async def test_recompute_floor_cache_updated(db):
    """recompute 後 get_dynamic_floor が float を返す"""
    pub_id = "pub_test_phase3_2"
    camp_id = "camp_phase3_2"
    for i in range(10):
        db.add(_make_spend(camp_id, pub_id, cleared_price_jpy=float(80 + i * 5)))
    for _ in range(3):
        db.add(_make_bid_log(outcome="bid", candidate_count=2))
    await db.commit()

    await recompute_floor_prices(db)

    floor = get_dynamic_floor(pub_id)
    assert floor is not None
    assert isinstance(floor, float)
    assert floor > 0


@pytest.mark.asyncio
async def test_prime_floor_cache_from_db(db):
    """DspFloorPriceHistoryDB に 1 行 seed → prime_floor_cache → cache に反映"""
    pub_id = "pub_test_phase3_3"
    db.add(DspFloorPriceHistoryDB(
        publisher_id=pub_id,
        floor_usd=0.5,
        floor_jpy=75.0,
        win_rate=0.3,
        bid_density=1.0,
        sample_count=10,
        computed_at=datetime.now(timezone.utc),
    ))
    await db.commit()

    await prime_floor_cache(db)

    assert get_dynamic_floor(pub_id) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_recompute_floor_cold_start_no_write(db):
    """spend_log 5 件のみ (< FLOOR_COLD_START_MIN=10) → DB INSERT なし、cache 空"""
    pub_id = "pub_test_phase3_4"
    camp_id = "camp_phase3_4"
    for i in range(5):
        db.add(_make_spend(camp_id, pub_id, cleared_price_jpy=float(100 + i * 10)))
    await db.commit()

    result = await recompute_floor_prices(db)

    assert pub_id not in result

    rows = (
        await db.execute(
            select(DspFloorPriceHistoryDB).where(
                DspFloorPriceHistoryDB.publisher_id == pub_id
            )
        )
    ).scalars().all()
    assert len(rows) == 0
    assert get_dynamic_floor(pub_id) is None


@pytest.mark.asyncio
async def test_old_records_deleted(db):
    """31 日前の DspFloorPriceHistoryDB は recompute で DELETE、1 日前は残る"""
    now = datetime.now(timezone.utc)

    db.add(DspFloorPriceHistoryDB(
        publisher_id="pub_old_phase3",
        floor_usd=0.1,
        floor_jpy=15.0,
        win_rate=0.3,
        bid_density=1.0,
        sample_count=10,
        computed_at=now - timedelta(days=31),
    ))
    db.add(DspFloorPriceHistoryDB(
        publisher_id="pub_recent_phase3",
        floor_usd=0.2,
        floor_jpy=30.0,
        win_rate=0.3,
        bid_density=1.0,
        sample_count=10,
        computed_at=now - timedelta(days=1),
    ))
    await db.commit()

    # spend_log 0 件 → new INSERT なし、retention だけ実行される
    await recompute_floor_prices(db)

    remaining = (
        await db.execute(select(DspFloorPriceHistoryDB.publisher_id))
    ).scalars().all()
    assert "pub_old_phase3" not in remaining
    assert "pub_recent_phase3" in remaining


# ── dsp #11 phase 4: _extract_publisher_id テスト (Red) ──


def test_extract_publisher_id_from_site():
    """site.publisher.id="pub_x" → "pub_x" を返す"""
    from dsp_engine.floor import _extract_publisher_id  # noqa: PLC0415
    from auction.openrtb import BidRequest, Site, Publisher

    bid_request = BidRequest(
        imp=[],
        site=Site(publisher=Publisher(id="pub_x")),
    )
    assert _extract_publisher_id(bid_request) == "pub_x"


def test_extract_publisher_id_none():
    """site/app/publisher 全て None or 欠落 → None"""
    from dsp_engine.floor import _extract_publisher_id  # noqa: PLC0415
    from auction.openrtb import BidRequest

    bid_request = BidRequest(imp=[])
    assert _extract_publisher_id(bid_request) is None
