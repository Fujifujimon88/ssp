"""インプレッション・落札イベントの記録（DB永続化対応）"""
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import func, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from auction.engine import AuctionResult
from cache import incr_impression_counter
from db_models import ImpressionDB
from utils import utcnow

logger = logging.getLogger(__name__)


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    return datetime.combine(d, datetime.min.time()), datetime.combine(d, datetime.max.time())


async def record_auction(
    result: AuctionResult,
    slot_id: str,
    publisher_id: str,
    db: AsyncSession,
) -> None:
    imp = ImpressionDB(
        id=str(uuid.uuid4()),
        auction_id=result.auction_id,
        imp_id=result.imp_id,
        slot_id=slot_id,
        publisher_id=publisher_id,
        winning_dsp=result.winner.dsp_id if result.winner else None,
        clearing_price=result.clearing_price,
        bid_count=len(result.all_bids),
        duration_ms=result.duration_ms,
        filled=result.winner is not None,
        timestamp=utcnow(),
    )
    db.add(imp)
    await db.commit()

    await incr_impression_counter(publisher_id, date.today().isoformat())

    if imp.filled:
        logger.info(
            f"Impression | pub={publisher_id} | cpm={result.clearing_price:.3f} "
            f"| dsp={result.winner.dsp_id} | {result.duration_ms:.1f}ms"
        )


async def get_daily_stats(publisher_id: str, for_date: date, db: AsyncSession) -> dict:
    """日次集計をSQLで取得（SQLite/PostgreSQL 両対応）"""
    start, end = _day_bounds(for_date)

    result = await db.execute(
        select(
            func.count(ImpressionDB.id).label("total"),
            # case を使って bool → int 変換（SQLite対応）
            func.sum(
                case((ImpressionDB.filled == True, 1), else_=0)
            ).label("filled_count"),
            func.sum(ImpressionDB.clearing_price).label("total_cpm"),
            func.avg(ImpressionDB.duration_ms).label("avg_latency"),
        ).where(
            ImpressionDB.publisher_id == publisher_id,
            ImpressionDB.timestamp >= start,
            ImpressionDB.timestamp <= end,
        )
    )
    row = result.one()
    total = row.total or 0
    filled = int(row.filled_count or 0)
    total_cpm = float(row.total_cpm or 0)

    return {
        "total": total,
        "filled": filled,
        "fill_rate": round(filled / total, 4) if total > 0 else 0.0,
        "revenue_usd": round(total_cpm / 1000, 6),
        "ecpm": round(total_cpm / filled, 4) if filled > 0 else 0.0,
        "avg_latency_ms": round(float(row.avg_latency or 0), 1),
    }


async def get_top_dsp(publisher_id: str, for_date: date, db: AsyncSession) -> Optional[str]:
    start, end = _day_bounds(for_date)

    result = await db.execute(
        select(ImpressionDB.winning_dsp, func.count(ImpressionDB.id).label("cnt"))
        .where(
            ImpressionDB.publisher_id == publisher_id,
            ImpressionDB.timestamp >= start,
            ImpressionDB.timestamp <= end,
            ImpressionDB.filled == True,
        )
        .group_by(ImpressionDB.winning_dsp)
        .order_by(func.count(ImpressionDB.id).desc())
        .limit(1)
    )
    row = result.first()
    return row.winning_dsp if row else None
