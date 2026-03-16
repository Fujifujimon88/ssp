"""MDM広告枠へのクリエイティブ選択・インプレッション記録

選択ロジック（シンプルオークション）:
  1. 指定 slot_type に対応するアクティブなクリエイティブを取得
  2. デバイスセグメント（age_group / platform）でフィルタ
  3. campaign.reward_amount（広告主支払い意欲）が高い順 × CPM floor 以上 → 最上位を選択
  4. MdmImpressionDB に記録
"""
import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import (
    AffiliateCampaignDB,
    CreativeDB,
    DeviceDB,
    MdmAdSlotDB,
    MdmImpressionDB,
)

logger = logging.getLogger(__name__)

# Frequency cap: max impressions per creative per device per day
FREQ_CAP_DAILY = 3


async def select_creative(
    db: AsyncSession,
    slot_type: str,
    device_id: str | None = None,
    enrollment_token: str | None = None,
    platform: str = "android",
) -> dict | None:
    """
    指定スロットタイプに最適なクリエイティブを選択してインプレッションを記録する。

    Args:
        slot_type:        "lockscreen" / "widget" / "notification" / "webclip_ios"
        device_id:        Android ID（ターゲティング用）
        enrollment_token: エンロールトークン（dealer_id取得用）
        platform:         "android" / "ios"

    Returns:
        クリエイティブ情報の dict、または None（配信可能なものなし）
    """
    # スロット定義を取得（floor_price確認）
    slot = await db.scalar(
        select(MdmAdSlotDB)
        .where(MdmAdSlotDB.slot_type == slot_type, MdmAdSlotDB.status == "active")
        .order_by(MdmAdSlotDB.created_at)
        .limit(1)
    )
    floor_cpm = slot.floor_price_cpm if slot else 0.0

    # デバイス情報を取得（セグメントターゲティング用）
    age_group = None
    dealer_id = None
    if enrollment_token:
        device = await db.scalar(
            select(DeviceDB).where(DeviceDB.enrollment_token == enrollment_token)
        )
        if device:
            age_group = device.age_group
            dealer_id = device.dealer_id

    # アクティブなクリエイティブを取得（slot_typeに対応するcampaign）
    # slot_typeとcampaign.categoryのマッピング
    category_map = {
        "lockscreen": None,    # 全カテゴリ
        "widget": "app",       # アプリ案件のみ
        "notification": None,
        "webclip_ios": None,
    }
    target_category = category_map.get(slot_type)

    q = (
        select(CreativeDB, AffiliateCampaignDB)
        .join(AffiliateCampaignDB, CreativeDB.campaign_id == AffiliateCampaignDB.id)
        .where(
            CreativeDB.status == "active",
            AffiliateCampaignDB.status == "active",
        )
    )
    if target_category:
        q = q.where(AffiliateCampaignDB.category == target_category)

    rows = await db.execute(q)
    candidates = rows.all()

    if not candidates:
        return None

    # フリークエンシーキャップ: 同一デバイスへの同一クリエイティブ配信を1日FREQ_CAP_DAILY回に制限
    if device_id or enrollment_token:
        today_start = datetime.combine(date.today(), datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        filter_col = (
            MdmImpressionDB.device_id if device_id else MdmImpressionDB.enrollment_token
        )
        filter_val = device_id or enrollment_token

        freq_rows = await db.execute(
            select(
                MdmImpressionDB.creative_id,
                func.count(MdmImpressionDB.id).label("count"),
            )
            .where(
                filter_col == filter_val,
                MdmImpressionDB.created_at >= today_start,
            )
            .group_by(MdmImpressionDB.creative_id)
        )
        capped_creative_ids = {
            row.creative_id
            for row in freq_rows.all()
            if row.count >= FREQ_CAP_DAILY
        }
        candidates = [r for r in candidates if r[0].id not in capped_creative_ids]

    if not candidates:
        logger.info(
            f"MDM frequency cap reached | slot={slot_type} | device_id={device_id} "
            f"| enrollment_token={enrollment_token}"
        )
        return None

    # reward_amount（広告主支払い意欲）で降順ソートして最上位を選択
    candidates.sort(key=lambda r: r[1].reward_amount, reverse=True)
    creative, campaign = candidates[0]

    # インプレッション記録
    imp = MdmImpressionDB(
        slot_id=slot.id if slot else None,
        creative_id=creative.id,
        device_id=device_id,
        enrollment_token=enrollment_token,
        dealer_id=dealer_id,
        platform=platform,
        age_group=age_group,
        cpm_price=floor_cpm,
    )
    db.add(imp)
    await db.commit()
    await db.refresh(imp)

    logger.info(
        f"MDM impression | slot={slot_type} | creative={creative.id[:8]}... "
        f"| campaign={campaign.name} | cpm=¥{floor_cpm}"
    )

    return {
        "impression_id": imp.id,
        "creative_id": creative.id,
        "campaign_id": campaign.id,
        "type": creative.type,
        "title": creative.title,
        "body": creative.body,
        "image_url": creative.image_url,
        "click_url": creative.click_url,
        "width": creative.width,
        "height": creative.height,
        "category": campaign.category,
    }


async def record_click(db: AsyncSession, impression_id: str) -> bool:
    """クリックをインプレッションレコードに記録する"""
    imp = await db.get(MdmImpressionDB, impression_id)
    if not imp:
        return False
    imp.clicked = True
    imp.clicked_at = datetime.now(timezone.utc)
    await db.commit()
    return True
