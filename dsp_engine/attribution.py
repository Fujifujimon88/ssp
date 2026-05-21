"""
dsp_engine 購入CVの取り込みと ROAS 計算。

広告主は AppsFlyer/Adjust の purchase ポストバック先を /dsp-engine/conversion に
設定する。ad markup に埋め込んだ click_token（dsp_ct）を使って
DspSpendLogDB → campaign_id / impression_id を解決しアトリビューションする。
dedup_key（appsflyer_event_id 等）で重複ポストバックを冪等に排除する。
"""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import DspConversionEventDB, DspSpendLogDB
from dsp_engine.campaign_manager import get_campaign_stats
from utils import utcnow

logger = logging.getLogger(__name__)


async def record_conversion(
    db: AsyncSession,
    *,
    campaign_id: Optional[str] = None,
    click_token: Optional[str] = None,
    event_type: str = "purchase",
    revenue_jpy: float = 0.0,
    dedup_key: Optional[str] = None,
    source: str = "direct",
    platform: str = "unknown",
    raw_payload: Optional[str] = None,
) -> tuple[DspConversionEventDB, bool]:
    """購入CVを記録する。

    Returns:
        (event, created) — created=False は dedup_key 重複でスキップした場合。

    Raises:
        ValueError: campaign_id も click_token からの解決も不可能な場合。
    """
    # 冪等性チェック（重複ポストバック排除）
    if dedup_key:
        existing = await db.scalar(
            select(DspConversionEventDB).where(DspConversionEventDB.dedup_key == dedup_key)
        )
        if existing:
            return existing, False

    impression_id: Optional[str] = None

    # click_token から campaign_id / impression_id を解決
    if click_token:
        spend = await db.scalar(
            select(DspSpendLogDB).where(DspSpendLogDB.click_token == click_token)
        )
        if spend:
            campaign_id = campaign_id or spend.campaign_id
            impression_id = spend.impression_id
            if platform == "unknown":
                platform = spend.platform

    if not campaign_id:
        raise ValueError("campaign_id を特定できません（click_token も未解決）")

    event = DspConversionEventDB(
        campaign_id=campaign_id,
        impression_id=impression_id,
        click_token=click_token,
        platform=platform,
        source=source,
        event_type=event_type,
        revenue_jpy=revenue_jpy,
        dedup_key=dedup_key,
        raw_payload=raw_payload,
        attributed_at=utcnow(),
    )
    db.add(event)
    try:
        await db.commit()
    except IntegrityError:
        # dedup_key の同時挿入レース → 既存行を返す
        await db.rollback()
        if dedup_key:
            existing = await db.scalar(
                select(DspConversionEventDB).where(DspConversionEventDB.dedup_key == dedup_key)
            )
            if existing:
                return existing, False
        raise
    await db.refresh(event)
    logger.info(
        f"dsp-engine conversion | campaign={campaign_id} | revenue=¥{revenue_jpy:.0f} "
        f"| event={event_type}"
    )
    return event, True


async def get_campaign_roas(db: AsyncSession, campaign_id: str) -> dict:
    """キャンペーンの ROAS サマリーを返す。

    Returns:
        {impressions, spend_jpy, conversions, revenue_jpy, roas(%), cpa(円)}
        ROAS(%) = 売上 / 消化 × 100、CPA = 消化 / CV数。
    """
    stats = await get_campaign_stats(db, campaign_id)
    spend = stats["spend_jpy"]
    revenue = stats["revenue_jpy"]
    conversions = stats["conversions"]
    roas = (revenue / spend * 100.0) if spend > 0 else 0.0
    cpa = (spend / conversions) if conversions > 0 else 0.0
    return {
        **stats,
        "roas": round(roas, 2),
        "cpa": round(cpa, 2),
    }
