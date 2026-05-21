"""
dsp_engine 広告主キャンペーン管理（DspCampaignDB の CRUD と実績集計）。
"""
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import DspCampaignDB, DspClickEventDB, DspConversionEventDB, DspSpendLogDB


async def list_active_campaigns(db: AsyncSession) -> list[DspCampaignDB]:
    """status="active" のキャンペーン一覧（入札対象）。"""
    rows = await db.execute(
        select(DspCampaignDB).where(DspCampaignDB.status == "active")
    )
    return list(rows.scalars().all())


async def list_campaigns(db: AsyncSession) -> list[DspCampaignDB]:
    """全キャンペーン一覧（管理画面用）。"""
    rows = await db.execute(
        select(DspCampaignDB).order_by(DspCampaignDB.created_at.desc())
    )
    return list(rows.scalars().all())


async def get_campaign(db: AsyncSession, campaign_id: str) -> Optional[DspCampaignDB]:
    return await db.get(DspCampaignDB, campaign_id)


async def get_campaign_by_login(db: AsyncSession, login_id: str) -> Optional[DspCampaignDB]:
    return await db.scalar(
        select(DspCampaignDB).where(DspCampaignDB.login_id == login_id)
    )


async def create_campaign(db: AsyncSession, **fields) -> DspCampaignDB:
    campaign = DspCampaignDB(**fields)
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def update_campaign(db: AsyncSession, campaign_id: str, **fields) -> Optional[DspCampaignDB]:
    campaign = await db.get(DspCampaignDB, campaign_id)
    if campaign is None:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(campaign, key):
            setattr(campaign, key, value)
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def get_campaign_stats(db: AsyncSession, campaign_id: str) -> dict:
    """キャンペーンの実績集計を返す。

    Returns:
        {"impressions": int, "clicks": int, "spend_jpy": float,
         "conversions": int, "revenue_jpy": float}
        impressions は落札ログ件数（= 配信インプレッション数）、clicks はクリック計測数。
    """
    spend_row = (
        await db.execute(
            select(
                func.count(DspSpendLogDB.id),
                func.coalesce(func.sum(DspSpendLogDB.spend_jpy), 0.0),
            ).where(DspSpendLogDB.campaign_id == campaign_id)
        )
    ).one()
    clicks = await db.scalar(
        select(func.count(DspClickEventDB.id)).where(
            DspClickEventDB.campaign_id == campaign_id
        )
    )
    conv_row = (
        await db.execute(
            select(
                func.count(DspConversionEventDB.id),
                func.coalesce(func.sum(DspConversionEventDB.revenue_jpy), 0.0),
            ).where(DspConversionEventDB.campaign_id == campaign_id)
        )
    ).one()
    return {
        "impressions": int(spend_row[0] or 0),
        "spend_jpy": float(spend_row[1] or 0.0),
        "clicks": int(clicks or 0),
        "conversions": int(conv_row[0] or 0),
        "revenue_jpy": float(conv_row[1] or 0.0),
    }
