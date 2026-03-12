"""日次レポート生成（DB集計 + Redisキャッシュ）"""
from datetime import date
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from analytics.collector import get_daily_stats, get_top_dsp
from cache import cache_report, get_cached_report
from publisher.models import DailyReport


async def generate_daily_report(
    publisher_id: str,
    db: AsyncSession,
    for_date: Optional[date] = None,
) -> DailyReport:
    target_date = for_date or date.today()
    cache_key = f"{publisher_id}:{target_date.isoformat()}"

    # キャッシュヒット確認
    cached = await get_cached_report(cache_key)
    if cached:
        return DailyReport(**cached)

    stats = await get_daily_stats(publisher_id, target_date, db)
    top_dsp = await get_top_dsp(publisher_id, target_date, db)

    report = DailyReport(
        publisher_id=publisher_id,
        date=target_date.isoformat(),
        impressions=stats["total"],
        fill_rate=stats["fill_rate"],
        revenue_usd=stats["revenue_usd"],
        ecpm=stats["ecpm"],
        top_dsp=top_dsp,
    )

    # 今日以外はキャッシュに保存
    if target_date < date.today():
        await cache_report(cache_key, report.model_dump())

    return report
