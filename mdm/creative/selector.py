"""MDM広告枠へのクリエイティブ選択・インプレッション記録

選択ロジック（シンプルオークション）:
  1. 指定 slot_type に対応するアクティブなクリエイティブを取得
  2. デバイスセグメント（age_group / platform）でフィルタ
  3. campaign.reward_amount（広告主支払い意欲）が高い順 × CPM floor 以上 → 最上位を選択
  4. MdmImpressionDB に記録
"""
import json
import logging
import random
import time
from datetime import date, datetime, timezone

from sqlalchemy import Integer, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import (
    AffiliateCampaignDB,
    CreativeDB,
    CreativeExperimentDB,
    DealerDB,
    DeviceDB,
    MdmAdSlotDB,
    MdmImpressionDB,
    StoreAdAssignmentDB,
    TimeSlotMultiplierDB,
)

logger = logging.getLogger(__name__)

# Frequency cap: max impressions per creative per device per day
FREQ_CAP_DAILY = 3

DEFAULT_CTR = 0.03  # コールドスタート: 3%

# ── タイムスロット乗数キャッシュ（5分TTL） ───────────────────
_TS_CACHE_TTL = 300  # seconds
# key: (hour, dow) → (multiplier: float, expires_at: float)
_ts_cache: dict[tuple[int, int], tuple[float, float]] = {}


async def get_time_slot_multiplier(hour: int, dow: int, db: AsyncSession) -> float:
    """
    指定した時刻・曜日に一致するタイムスロット乗数を返す。
    一致するレコードがなければ 1.0 を返す。
    結果は 5 分間モジュールレベルでキャッシュする。

    Args:
        hour: 0-23
        dow:  0=月曜 .. 6=日曜
        db:   AsyncSession

    Returns:
        multiplier (float), デフォルト 1.0
    """
    cache_key = (hour, dow)
    now = time.monotonic()
    cached = _ts_cache.get(cache_key)
    if cached is not None:
        multiplier, expires_at = cached
        if now < expires_at:
            return multiplier

    # day_of_week が NULL（全曜日） または dow と一致する行を検索
    # 複数ヒット時は最も具体的な（day_of_week IS NOT NULL）行を優先する
    row = await db.scalar(
        select(TimeSlotMultiplierDB)
        .where(
            TimeSlotMultiplierDB.hour_start <= hour,
            TimeSlotMultiplierDB.hour_end >= hour,
            or_(
                TimeSlotMultiplierDB.day_of_week == dow,
                TimeSlotMultiplierDB.day_of_week.is_(None),
            ),
        )
        .order_by(
            # day_of_week IS NOT NULL → 0 (優先), IS NULL → 1 (フォールバック)
            TimeSlotMultiplierDB.day_of_week.is_(None),
        )
        .limit(1)
    )

    multiplier = row.multiplier if row else 1.0
    _ts_cache[cache_key] = (multiplier, now + _TS_CACHE_TTL)
    return multiplier


def _matches_targeting(
    targeting: dict,
    *,
    platform: str | None,
    age_group: str | None,
    region: str | None,
    hour: int,
    screen_on_count: int | None,
) -> bool:
    """
    全軸AND論理。axis が absent = 制限なし（デフォルト全配信）。
    hour=-1 は未知扱いでtime_slots軸をスキップ。
    screen_on_count=None は未知扱いでscreen_on_count_max軸をスキップ。
    """
    ts = targeting.get("time_slots")
    if ts and hour >= 0 and hour not in ts:
        return False

    p = targeting.get("platform")
    if p and platform and platform != p:
        return False

    ag = targeting.get("age_groups")
    if ag and age_group and age_group not in ag:
        return False

    rg = targeting.get("regions")
    if rg and region and region not in rg:
        return False

    soc_max = targeting.get("screen_on_count_max")
    if soc_max is not None and screen_on_count is not None and screen_on_count > soc_max:
        return False

    return True


async def _get_creative_ctrs(
    db: AsyncSession,
    creative_ids: list[str],
    slot_type: str,
    min_impressions: int = 100,
) -> dict[str, float]:
    """
    クリエイティブ×スロットタイプ別の実績CTRを返す。
    インプレッション数が min_impressions 未満の場合はデフォルト値(0.03=3%)を使用。

    Returns: {creative_id: ctr_float}
    """
    if not creative_ids:
        return {}

    rows = await db.execute(
        select(
            MdmImpressionDB.creative_id,
            func.count(MdmImpressionDB.id).label("impressions"),
            func.sum(func.cast(MdmImpressionDB.clicked, Integer)).label("clicks"),
        )
        .where(MdmImpressionDB.creative_id.in_(creative_ids))
        .group_by(MdmImpressionDB.creative_id)
    )

    ctrs: dict[str, float] = {}

    for row in rows.all():
        if row.impressions >= min_impressions:
            ctrs[row.creative_id] = (row.clicks or 0) / row.impressions
        else:
            # ベイズ平均: 実績データが少ない場合はデフォルトCTRとブレンド
            alpha = row.impressions / min_impressions
            ctrs[row.creative_id] = alpha * ((row.clicks or 0) / max(row.impressions, 1)) + (1 - alpha) * DEFAULT_CTR

    return ctrs


async def select_creative(
    db: AsyncSession,
    slot_type: str,
    device_id: str | None = None,
    enrollment_token: str | None = None,
    platform: str = "android",
    hour: int = -1,
    screen_on_count: int | None = None,
) -> dict | None:
    """
    指定スロットタイプに最適なクリエイティブを選択してインプレッションを記録する。

    Args:
        slot_type:        "lockscreen" / "widget" / "notification" / "webclip_ios"
        device_id:        Android ID（ターゲティング用）
        enrollment_token: エンロールトークン（dealer_id取得用）
        platform:         "android" / "ios"
        hour:             0-23 の時刻。-1 の場合はタイムスロット乗数を適用しない。

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

    # targeting_jsonを解析
    targeting: dict = {}
    if slot and slot.targeting_json:
        try:
            targeting = json.loads(slot.targeting_json)
        except (json.JSONDecodeError, TypeError):
            pass

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

    region = None
    if dealer_id:
        dealer = await db.get(DealerDB, dealer_id)
        region = dealer.region if dealer else None

    # フリークエンシーキャップ計算（店舗枠・通常枠で共用）
    capped_creative_ids: set[str] = set()
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

    # 店舗専用枠 優先選択（lockscreen / widget のみ）
    # dealer_id が判明している場合、StoreAdAssignmentDB を先に確認して最優先配信する
    if dealer_id and slot_type in ("lockscreen", "widget"):
        store_rows = await db.execute(
            select(CreativeDB, AffiliateCampaignDB)
            .join(AffiliateCampaignDB, CreativeDB.campaign_id == AffiliateCampaignDB.id)
            .join(StoreAdAssignmentDB, StoreAdAssignmentDB.campaign_id == AffiliateCampaignDB.id)
            .where(
                StoreAdAssignmentDB.dealer_id == dealer_id,
                StoreAdAssignmentDB.status == "active",
                CreativeDB.status == "active",
                AffiliateCampaignDB.status == "active",
            )
            .order_by(StoreAdAssignmentDB.priority)
        )
        store_candidates = store_rows.all()

        # フリークエンシーキャップ適用
        store_candidates = [r for r in store_candidates if r[0].id not in capped_creative_ids]

        if store_candidates:
            creative, campaign = store_candidates[0]
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
                f"MDM store priority impression | slot={slot_type} "
                f"| creative={creative.id[:8]}... | dealer={dealer_id[:8]}..."
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
                "is_store_creative": True,
            }

    # アクティブなクリエイティブを取得（slot_typeに対応するcampaign）
    # slot_typeとcampaign.categoryのマッピング
    category_map = {
        "lockscreen": None,    # 全カテゴリ（store カテゴリは除外）
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
            AffiliateCampaignDB.category != "store",  # 店舗専用枠は通常オークション対象外
        )
    )
    if target_category:
        q = q.where(AffiliateCampaignDB.category == target_category)

    rows = await db.execute(q)
    candidates = rows.all()

    if not candidates:
        return None

    # ターゲティングフィルタ（5軸AND論理）
    if targeting:
        candidates = [
            row for row in candidates
            if _matches_targeting(
                targeting,
                platform=platform,
                age_group=age_group,
                region=region,
                hour=hour,
                screen_on_count=screen_on_count,
            )
        ]

    if not candidates:
        return None

    # フリークエンシーキャップ適用
    candidates = [r for r in candidates if r[0].id not in capped_creative_ids]

    if not candidates:
        logger.info(
            f"MDM frequency cap reached | slot={slot_type} | device_id={device_id} "
            f"| enrollment_token={enrollment_token}"
        )
        return None

    # A/Bテスト: アクティブな実験があれば実験アームを使用
    exp = await db.scalar(
        select(CreativeExperimentDB).where(
            CreativeExperimentDB.slot_type == slot_type,
            CreativeExperimentDB.status == "active",
        ).limit(1)
    )
    if exp:
        chosen_id = exp.control_creative_id if random.random() > exp.traffic_split else exp.variant_creative_id
        exp_candidates = [r for r in candidates if r[0].id == chosen_id]
        if exp_candidates:
            candidates = exp_candidates

    # eCPM = reward_amount × predicted_CTR × 1000 で降順ソートして最上位を選択
    creative_ids = [r[0].id for r in candidates]
    ctrs = await _get_creative_ctrs(db, creative_ids, slot_type)

    # タイムスロット乗数の取得（hour >= 0 のときのみ）
    ts_multiplier = 1.0
    if hour >= 0:
        dow = datetime.now(timezone.utc).weekday()
        ts_multiplier = await get_time_slot_multiplier(hour, dow, db)
        if ts_multiplier != 1.0:
            logger.info(
                f"MDM time-slot multiplier applied | slot={slot_type} "
                f"| hour={hour} | dow={dow} | multiplier={ts_multiplier}"
            )

    def ecpm(row) -> float:
        creative, campaign = row
        ctr = ctrs.get(creative.id, DEFAULT_CTR)
        base = campaign.reward_amount * ctr * 1000
        return base * ts_multiplier

    candidates.sort(key=ecpm, reverse=True)
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
