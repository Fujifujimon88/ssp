"""動的フロアバッチ — Phase 3 (dsp_engine #11)。

publisher 別の最適フロア CPM(USD) を 1 時間ごとに recompute し、
DspFloorPriceHistoryDB に保存 + _floor_cache を更新。
入札パスは get_dynamic_floor() で L1 キャッシュ参照のみ。
"""
from __future__ import annotations

import asyncio
import logging
import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from db_models import DspBidLogDB, DspFloorPriceHistoryDB, DspSpendLogDB
from dsp_engine.currency import get_jpy_per_usd
from dsp_engine.floor import (
    compute_dynamic_floor,
    DEFAULT_FLOOR_CONFIG,
    FloorConfig,
)

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30

_floor_cache: dict[str, float] = {}


def get_dynamic_floor(publisher_id: str | None) -> float | None:
    """入札パス用 L1 キャッシュ参照。未登録 / None → None を返す。"""
    if not publisher_id:
        return None
    return _floor_cache.get(publisher_id)


async def recompute_floor_prices(
    db: AsyncSession,
    config: FloorConfig | None = None,
) -> dict[str, float]:
    """publisher 別の動的フロアを再計算し DspFloorPriceHistoryDB に保存、_floor_cache を更新する。

    Args:
        db: 呼び出し元が用意した AsyncSession。
        config: FloorConfig (None なら DEFAULT_FLOOR_CONFIG を使用)。

    Returns:
        {publisher_id: floor_usd, ...} (Cold-start で書かなかった publisher は含まない)。
    """
    cfg = config or DEFAULT_FLOOR_CONFIG
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=cfg.FLOOR_LOOKBACK_DAYS)

    # 過去 FLOOR_LOOKBACK_DAYS 日の spend_log を publisher_id ごとに集める
    spend_rows = (await db.execute(
        select(DspSpendLogDB.publisher_id, DspSpendLogDB.cleared_price_jpy)
        .where(DspSpendLogDB.logged_at >= cutoff)
    )).all()

    by_publisher: dict[str, list[float]] = {}
    for pub_id, price in spend_rows:
        if pub_id is None or price is None:
            continue
        by_publisher.setdefault(pub_id, []).append(float(price))

    # win_rate = 全 spend_log 件数 / outcome='bid' の bid_log 件数
    spend_count = len((await db.execute(
        select(DspSpendLogDB.id)
    )).all())

    bid_count = len((await db.execute(
        select(DspBidLogDB.id).where(DspBidLogDB.outcome == "bid")
    )).all())
    win_rate = (spend_count / bid_count) if bid_count > 0 else 0.0

    # bid_density = 全 DspBidLogDB の candidate_count の中央値 (空なら 1.0)
    densities = (await db.execute(
        select(DspBidLogDB.candidate_count)
    )).scalars().all()
    bid_density = statistics.median(densities) if densities else 1.0

    jpy_per_usd = get_jpy_per_usd()

    result: dict[str, float] = {}
    for pub_id, prices in by_publisher.items():
        floor_usd = compute_dynamic_floor(prices, win_rate, float(bid_density), jpy_per_usd, cfg)
        if floor_usd is None:
            continue
        floor_jpy = floor_usd * jpy_per_usd
        db.add(DspFloorPriceHistoryDB(
            publisher_id=pub_id,
            floor_usd=floor_usd,
            floor_jpy=floor_jpy,
            win_rate=win_rate,
            bid_density=float(bid_density),
            sample_count=len(prices),
            computed_at=now,
        ))
        result[pub_id] = floor_usd

    # L1 キャッシュを全置換
    _floor_cache.clear()
    _floor_cache.update(result)

    # retention: 30 日超のレコードを削除 (spend_log の有無に関わらず必ず実行)
    await db.execute(
        delete(DspFloorPriceHistoryDB)
        .where(DspFloorPriceHistoryDB.computed_at < now - timedelta(days=RETENTION_DAYS))
    )
    await db.commit()
    return result


async def prime_floor_cache(db: AsyncSession) -> None:
    """起動時に DB の最新フロア履歴を _floor_cache に読み込む。

    SQLite 互換のため DISTINCT ON / window function は使わず、
    ORDER BY computed_at DESC + seen set で publisher_id 初回採用方式を使う。
    """
    rows = (await db.execute(
        select(DspFloorPriceHistoryDB).order_by(DspFloorPriceHistoryDB.computed_at.desc())
    )).scalars().all()

    _floor_cache.clear()
    seen: set[str] = set()
    for row in rows:
        if row.publisher_id in seen:
            continue
        _floor_cache[row.publisher_id] = float(row.floor_usd)
        seen.add(row.publisher_id)


async def schedule_floor_tasks() -> None:
    """lifespan から create_task で起動するバックグラウンドループ。

    DEFAULT_FLOOR_CONFIG.FLOOR_REFRESH_SEC (=3600) ごとにフロアを再計算する。
    例外は握りつぶしてループを継続する (バッチ失敗で本体を巻き込まない)。
    CancelledError は再 raise して正常 shutdown を保証する。
    """
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await recompute_floor_prices(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("floor recompute failed")
        try:
            await asyncio.sleep(DEFAULT_FLOOR_CONFIG.FLOOR_REFRESH_SEC)
        except asyncio.CancelledError:
            raise
