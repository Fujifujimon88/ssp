"""
dsp #11 phase 1: dsp_floor_price_history テーブル存在検証 (Red)

検証項目:
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
