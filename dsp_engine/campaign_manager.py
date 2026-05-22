"""
dsp_engine 広告主キャンペーン管理（DspCampaignDB の CRUD と実績集計）。
"""
from typing import Optional

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import (
    DspAbExperimentDB,
    DspCampaignDB,
    DspClickEventDB,
    DspConversionEventDB,
    DspCreativeDB,
    DspSpendLogDB,
)
from utils import utcnow


async def list_active_campaigns(db: AsyncSession) -> list[DspCampaignDB]:
    """入札対象のキャンペーン一覧。

    status="active" かつ配信期間内（start_date <= 今日 <= end_date、
    各 NULL は無制限）のものだけを返す。
    """
    today = utcnow().date()
    rows = await db.execute(
        select(DspCampaignDB).where(
            DspCampaignDB.status == "active",
            or_(DspCampaignDB.start_date.is_(None), DspCampaignDB.start_date <= today),
            or_(DspCampaignDB.end_date.is_(None), DspCampaignDB.end_date >= today),
        )
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
         "daily_spend_jpy": float, "conversions": int, "revenue_jpy": float}
        impressions は落札ログ件数（= 配信インプレッション数）、clicks はクリック計測数。
    """
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    spend_row = (
        await db.execute(
            select(
                func.count(DspSpendLogDB.id),
                func.coalesce(func.sum(DspSpendLogDB.spend_jpy), 0.0),
                func.coalesce(func.sum(case((DspSpendLogDB.logged_at >= day_start, DspSpendLogDB.spend_jpy), else_=0.0)), 0.0),
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
            ).where(
                DspConversionEventDB.campaign_id == campaign_id,
                DspConversionEventDB.attributed == True,  # noqa: E712
            )
        )
    ).one()
    return {
        "impressions": int(spend_row[0] or 0),
        "spend_jpy": float(spend_row[1] or 0.0),
        "daily_spend_jpy": float(spend_row[2] or 0.0),
        "clicks": int(clicks or 0),
        "conversions": int(conv_row[0] or 0),
        "revenue_jpy": float(conv_row[1] or 0.0),
    }


def _empty_stats() -> dict:
    return {"impressions": 0, "spend_jpy": 0.0, "daily_spend_jpy": 0.0, "clicks": 0,
            "conversions": 0, "revenue_jpy": 0.0}


async def get_all_campaign_stats(
    db: AsyncSession, campaign_ids: list[str]
) -> dict[str, dict]:
    """複数キャンペーンの実績を一括集計する（入札パスの N+1 クエリ解消）。

    キャンペーン数に関わらず 3 クエリ（消化・クリック・CV）で全件を返す。
    Returns:
        {campaign_id: {impressions, spend_jpy, clicks, conversions, revenue_jpy}}
        実績の無いキャンペーンIDもゼロ値で含む。
    """
    result = {cid: _empty_stats() for cid in campaign_ids}
    if not campaign_ids:
        return result

    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    spend_rows = await db.execute(
        select(
            DspSpendLogDB.campaign_id,
            func.count(DspSpendLogDB.id),
            func.coalesce(func.sum(DspSpendLogDB.spend_jpy), 0.0),
            func.coalesce(func.sum(case((DspSpendLogDB.logged_at >= day_start, DspSpendLogDB.spend_jpy), else_=0.0)), 0.0),
        )
        .where(DspSpendLogDB.campaign_id.in_(campaign_ids))
        .group_by(DspSpendLogDB.campaign_id)
    )
    for cid, imp, spend, daily in spend_rows.all():
        if cid in result:
            result[cid]["impressions"] = int(imp or 0)
            result[cid]["spend_jpy"] = float(spend or 0.0)
            result[cid]["daily_spend_jpy"] = float(daily or 0.0)

    click_rows = await db.execute(
        select(DspClickEventDB.campaign_id, func.count(DspClickEventDB.id))
        .where(DspClickEventDB.campaign_id.in_(campaign_ids))
        .group_by(DspClickEventDB.campaign_id)
    )
    for cid, clk in click_rows.all():
        if cid in result:
            result[cid]["clicks"] = int(clk or 0)

    conv_rows = await db.execute(
        select(
            DspConversionEventDB.campaign_id,
            func.count(DspConversionEventDB.id),
            func.coalesce(func.sum(DspConversionEventDB.revenue_jpy), 0.0),
        )
        .where(
            DspConversionEventDB.campaign_id.in_(campaign_ids),
            DspConversionEventDB.attributed == True,  # noqa: E712
        )
        .group_by(DspConversionEventDB.campaign_id)
    )
    for cid, cnt, rev in conv_rows.all():
        if cid in result:
            result[cid]["conversions"] = int(cnt or 0)
            result[cid]["revenue_jpy"] = float(rev or 0.0)

    return result


# ── クリエイティブ（#7。1キャンペーン : N クリエイティブ） ──────────

async def list_creatives(db: AsyncSession, campaign_id: str) -> list[DspCreativeDB]:
    """キャンペーンの全クリエイティブ一覧（管理画面用。status 問わず）。"""
    rows = await db.execute(
        select(DspCreativeDB)
        .where(DspCreativeDB.campaign_id == campaign_id)
        .order_by(DspCreativeDB.created_at.asc())
    )
    return list(rows.scalars().all())


async def get_creative(db: AsyncSession, creative_id: str) -> Optional[DspCreativeDB]:
    return await db.get(DspCreativeDB, creative_id)


async def create_creative(db: AsyncSession, **fields) -> DspCreativeDB:
    creative = DspCreativeDB(**fields)
    db.add(creative)
    await db.commit()
    await db.refresh(creative)
    return creative


async def update_creative(
    db: AsyncSession, creative_id: str, **fields
) -> Optional[DspCreativeDB]:
    creative = await db.get(DspCreativeDB, creative_id)
    if creative is None:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(creative, key):
            setattr(creative, key, value)
    await db.commit()
    await db.refresh(creative)
    return creative


async def get_active_creatives_by_campaign(
    db: AsyncSession, campaign_ids: list[str]
) -> dict[str, list[DspCreativeDB]]:
    """複数キャンペーンの active クリエイティブを一括取得する（入札パスの N+1 回避）。

    Returns: {campaign_id: [active な DspCreativeDB ...]}。
    クリエイティブの無いキャンペーンIDも空リストで含む。
    """
    result: dict[str, list[DspCreativeDB]] = {cid: [] for cid in campaign_ids}
    if not campaign_ids:
        return result
    rows = await db.execute(
        select(DspCreativeDB).where(
            DspCreativeDB.campaign_id.in_(campaign_ids),
            DspCreativeDB.status == "active",
        )
    )
    for creative in rows.scalars().all():
        if creative.campaign_id in result:
            result[creative.campaign_id].append(creative)
    return result


# ── A/B テスト実験（#7。メタデータ管理用。入札ロジックは参照しない） ──

async def list_experiments(db: AsyncSession, campaign_id: str) -> list[DspAbExperimentDB]:
    rows = await db.execute(
        select(DspAbExperimentDB)
        .where(DspAbExperimentDB.campaign_id == campaign_id)
        .order_by(DspAbExperimentDB.created_at.desc())
    )
    return list(rows.scalars().all())


async def create_experiment(
    db: AsyncSession, *, campaign_id: str, name: str
) -> DspAbExperimentDB:
    experiment = DspAbExperimentDB(campaign_id=campaign_id, name=name, status="active")
    db.add(experiment)
    await db.commit()
    await db.refresh(experiment)
    return experiment


async def conclude_experiment(
    db: AsyncSession, experiment_id: str, winner_creative_id: Optional[str] = None
) -> Optional[DspAbExperimentDB]:
    """実験を concluded にし、winner クリエイティブと終了時刻を記録する。"""
    experiment = await db.get(DspAbExperimentDB, experiment_id)
    if experiment is None:
        return None
    experiment.status = "concluded"
    experiment.winner_creative_id = winner_creative_id
    experiment.concluded_at = utcnow()
    await db.commit()
    await db.refresh(experiment)
    return experiment
