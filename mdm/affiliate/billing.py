"""収益計算エンジン・代理店精算レポート

報酬タイプ:
  cpi  - Cost Per Install（アプリインストール確認ベース）
  cps  - Cost Per Sale（購入CV確認ベース）
  cpl  - Cost Per Lead（リード獲得）
  cpm  - ロック画面/ウィジェット表示課金（端末×月額）

精算フロー:
  1. AffiliateConversionDB からCV集計
  2. campaign.reward_amount × CV数 = 収益
  3. dealer別に按分（クリックログのdealer_id参照）
"""
import logging
from calendar import monthrange
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import (
    AffiliateCampaignDB,
    AffiliateClickDB,
    AffiliateConversionDB,
    AndroidDeviceDB,
    DealerDB,
    DeviceDB,
)

logger = logging.getLogger(__name__)


async def calculate_monthly_revenue(
    db: AsyncSession,
    year: int,
    month: int,
) -> dict:
    """
    指定月の全体収益を集計する。

    Returns:
        {
          "period": "2026-03",
          "total_revenue_jpy": 12345.0,
          "by_campaign": [...],
          "total_conversions": 42,
        }
    """
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)

    # 期間内のCV合計収益
    total = await db.scalar(
        select(func.sum(AffiliateConversionDB.revenue_jpy))
        .where(
            AffiliateConversionDB.converted_at >= start,
            AffiliateConversionDB.converted_at <= end,
        )
    )

    # 案件別CV数・収益
    rows = await db.execute(
        select(
            AffiliateConversionDB.campaign_id,
            func.count(AffiliateConversionDB.id).label("cv_count"),
            func.sum(AffiliateConversionDB.revenue_jpy).label("revenue"),
        )
        .where(
            AffiliateConversionDB.converted_at >= start,
            AffiliateConversionDB.converted_at <= end,
        )
        .group_by(AffiliateConversionDB.campaign_id)
    )
    by_campaign = []
    for row in rows.all():
        campaign = await db.get(AffiliateCampaignDB, row.campaign_id)
        by_campaign.append({
            "campaign_id": row.campaign_id,
            "campaign_name": campaign.name if campaign else "Unknown",
            "reward_type": campaign.reward_type if campaign else "?",
            "cv_count": row.cv_count,
            "revenue_jpy": float(row.revenue or 0),
        })

    total_cv = await db.scalar(
        select(func.count(AffiliateConversionDB.id))
        .where(
            AffiliateConversionDB.converted_at >= start,
            AffiliateConversionDB.converted_at <= end,
        )
    )

    return {
        "period": f"{year:04d}-{month:02d}",
        "total_revenue_jpy": float(total or 0),
        "total_conversions": total_cv or 0,
        "by_campaign": by_campaign,
    }


async def get_dealer_monthly_report(
    db: AsyncSession,
    dealer_id: str,
    year: int,
    month: int,
) -> dict:
    """
    代理店単位の月次精算レポートを生成する。

    Returns:
        {
          "dealer_id": "...",
          "dealer_name": "...",
          "period": "2026-03",
          "enrolled_devices": 50,
          "active_devices": 45,
          "android_enrolled": 30,
          "clicks": 200,
          "conversions": 12,
          "revenue_jpy": 3600.0,
          "by_campaign": [...],
        }
    """
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)

    dealer = await db.get(DealerDB, dealer_id)
    if not dealer:
        return {}

    # デバイス数
    enrolled = await db.scalar(
        select(func.count(DeviceDB.id)).where(DeviceDB.dealer_id == dealer_id)
    )
    active = await db.scalar(
        select(func.count(DeviceDB.id))
        .where(DeviceDB.dealer_id == dealer_id, DeviceDB.status == "active")
    )

    # Android端末数（enrollmentTokenで紐付け）
    android_count = await db.scalar(
        select(func.count(AndroidDeviceDB.id))
        .join(DeviceDB, AndroidDeviceDB.enrollment_token == DeviceDB.enrollment_token)
        .where(DeviceDB.dealer_id == dealer_id)
    )

    # クリック数（期間内）
    clicks = await db.scalar(
        select(func.count(AffiliateClickDB.id))
        .where(
            AffiliateClickDB.dealer_id == dealer_id,
            AffiliateClickDB.clicked_at >= start,
            AffiliateClickDB.clicked_at <= end,
        )
    )

    # CV数と収益（クリックのdealer_idから逆引き）
    cv_rows = await db.execute(
        select(
            AffiliateConversionDB.campaign_id,
            func.count(AffiliateConversionDB.id).label("cv_count"),
            func.sum(AffiliateConversionDB.revenue_jpy).label("revenue"),
        )
        .join(
            AffiliateClickDB,
            AffiliateConversionDB.click_token == AffiliateClickDB.click_token,
        )
        .where(
            AffiliateClickDB.dealer_id == dealer_id,
            AffiliateConversionDB.converted_at >= start,
            AffiliateConversionDB.converted_at <= end,
        )
        .group_by(AffiliateConversionDB.campaign_id)
    )
    by_campaign = []
    total_revenue = 0.0
    total_cv = 0
    for row in cv_rows.all():
        campaign = await db.get(AffiliateCampaignDB, row.campaign_id)
        rev = float(row.revenue or 0)
        by_campaign.append({
            "campaign_id": row.campaign_id,
            "campaign_name": campaign.name if campaign else "Unknown",
            "reward_type": campaign.reward_type if campaign else "?",
            "cv_count": row.cv_count,
            "revenue_jpy": rev,
        })
        total_revenue += rev
        total_cv += row.cv_count

    return {
        "dealer_id": dealer_id,
        "dealer_name": dealer.name,
        "store_code": dealer.store_code,
        "period": f"{year:04d}-{month:02d}",
        "enrolled_devices": enrolled or 0,
        "active_devices": active or 0,
        "android_enrolled": android_count or 0,
        "clicks": clicks or 0,
        "conversions": total_cv,
        "revenue_jpy": total_revenue,
        "by_campaign": by_campaign,
    }


async def get_all_dealers_report(
    db: AsyncSession,
    year: int,
    month: int,
) -> list[dict]:
    """全代理店の月次サマリーを返す（管理者向け）"""
    rows = await db.execute(select(DealerDB).where(DealerDB.status == "active"))
    dealers = rows.scalars().all()

    reports = []
    for dealer in dealers:
        report = await get_dealer_monthly_report(db, dealer.id, year, month)
        if report:
            reports.append(report)

    reports.sort(key=lambda r: r["revenue_jpy"], reverse=True)
    return reports
