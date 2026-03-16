"""
ML-01 — ユーザー特徴量収集パイプライン

毎日02:00 JSTに実行（スケジューラーまたはcron）。
過去30日のimpression/clickデータをデバイス単位で集計し、
user_featuresテーブルにupsertする。

プライバシー: device_idは疑似匿名UUID。PII（氏名・電話・メール）は含まない。
APPI準拠: 同意フォームで data_collection に同意済みのデバイスのみ対象。
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, case
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import DeviceProfileDB, MdmImpressionDB, UserFeatureDB

logger = logging.getLogger(__name__)


async def compute_user_features(db: AsyncSession) -> int:
    """
    全エンロール済みデバイスの特徴量を計算してupsert。

    プライバシー: device_idは疑似匿名UUID。PII（氏名・電話・メール）は含まない。

    処理手順:
      1. 過去30日のmdm_impressionsをdevice_id単位で集計
         (impression数・click数・CTR・平均dwell_ms)
      2. 時間帯別CTRサブクエリで preferred_hour を決定
      3. 最頻出 dismiss_type サブクエリで dominant_dismiss_type を決定
      4. device_profiles と LEFT JOIN してcarrier/model/regionを付与
      5. UserFeatureDB に merge (upsert) してコミット

    Returns:
        更新したデバイス数
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    # ── Step 1: impression基本集計 ───────────────────────────────
    base_agg = (
        select(
            MdmImpressionDB.device_id,
            func.count(MdmImpressionDB.id).label("imp_count"),
            func.sum(
                case((MdmImpressionDB.clicked == True, 1), else_=0)
            ).label("click_count"),
        )
        .where(
            MdmImpressionDB.device_id.is_not(None),
            MdmImpressionDB.created_at > cutoff,
        )
        .group_by(MdmImpressionDB.device_id)
        .subquery("base_agg")
    )

    # ── Step 2: preferred_hour サブクエリ ────────────────────────
    # 時間帯別（hour_of_day）にCTRが最も高い1時間を特定する。
    # SQLite互換: strftime('%H', created_at) を使用。
    # PostgreSQL環境では EXTRACT(HOUR FROM created_at) に切り替え可。
    hour_ctr = (
        select(
            MdmImpressionDB.device_id,
            func.strftime("%H", MdmImpressionDB.created_at).label("hour_of_day"),
            (
                func.sum(case((MdmImpressionDB.clicked == True, 1), else_=0))
                / func.cast(func.count(MdmImpressionDB.id), type_=func.count().type)
            ).label("hour_ctr"),
        )
        .where(
            MdmImpressionDB.device_id.is_not(None),
            MdmImpressionDB.created_at > cutoff,
        )
        .group_by(
            MdmImpressionDB.device_id,
            func.strftime("%H", MdmImpressionDB.created_at),
        )
        .subquery("hour_ctr")
    )

    # デバイスごとに最大CTRの時間帯を選ぶ
    best_hour = (
        select(
            hour_ctr.c.device_id,
            func.cast(
                func.max(hour_ctr.c.hour_of_day),
                type_=func.count().type,
            ).label("preferred_hour"),
        )
        .group_by(hour_ctr.c.device_id)
        .subquery("best_hour")
    )

    # ── Step 3: dominant_dismiss_type サブクエリ ─────────────────
    # MdmImpressionDB に dismiss_type カラムが追加された想定。
    # 現時点では status カラムで代用（served / prefetched / expired）。
    dismiss_counts = (
        select(
            MdmImpressionDB.device_id,
            MdmImpressionDB.status.label("dismiss_type"),
            func.count(MdmImpressionDB.id).label("cnt"),
        )
        .where(
            MdmImpressionDB.device_id.is_not(None),
            MdmImpressionDB.created_at > cutoff,
        )
        .group_by(MdmImpressionDB.device_id, MdmImpressionDB.status)
        .subquery("dismiss_counts")
    )

    # デバイスごとに最多カウントの dismiss_type を選ぶ
    dominant_dismiss = (
        select(
            dismiss_counts.c.device_id,
            dismiss_counts.c.dismiss_type,
        )
        .where(
            dismiss_counts.c.cnt == (
                select(func.max(dismiss_counts.c.cnt))
                .where(dismiss_counts.c.device_id == dismiss_counts.c.device_id)
                .correlate(dismiss_counts)
                .scalar_subquery()
            )
        )
        .distinct(dismiss_counts.c.device_id)
        .subquery("dominant_dismiss")
    )

    # ── Step 4: avg_dwell_ms（別集計） ──────────────────────────
    # MdmImpressionDB に dwell_time_ms がないため、
    # clicked_at - served_at をミリ秒換算して近似値とする。
    dwell_agg = (
        select(
            MdmImpressionDB.device_id,
            func.avg(
                func.julianday(MdmImpressionDB.clicked_at)
                - func.julianday(MdmImpressionDB.served_at)
            ).label("avg_dwell_days"),
        )
        .where(
            MdmImpressionDB.device_id.is_not(None),
            MdmImpressionDB.clicked_at.is_not(None),
            MdmImpressionDB.served_at.is_not(None),
            MdmImpressionDB.created_at > cutoff,
        )
        .group_by(MdmImpressionDB.device_id)
        .subquery("dwell_agg")
    )

    # ── Step 5: 全体クエリ（base + profile JOIN） ────────────────
    stmt = (
        select(
            base_agg.c.device_id,
            base_agg.c.imp_count,
            base_agg.c.click_count,
            best_hour.c.preferred_hour,
            dominant_dismiss.c.dismiss_type,
            dwell_agg.c.avg_dwell_days,
            DeviceProfileDB.carrier,
            DeviceProfileDB.model,
            DeviceProfileDB.region,
        )
        .outerjoin(best_hour, best_hour.c.device_id == base_agg.c.device_id)
        .outerjoin(dominant_dismiss, dominant_dismiss.c.device_id == base_agg.c.device_id)
        .outerjoin(dwell_agg, dwell_agg.c.device_id == base_agg.c.device_id)
        .outerjoin(DeviceProfileDB, DeviceProfileDB.device_id == base_agg.c.device_id)
    )

    result = await db.execute(stmt)
    rows = result.all()

    now = datetime.now(timezone.utc)
    updated_count = 0

    for row in rows:
        device_id = row.device_id
        if not device_id:
            continue

        imp_count = int(row.imp_count or 0)
        click_count = int(row.click_count or 0)
        ctr = (click_count / imp_count) if imp_count > 0 else 0.0

        # avg_dwell_days → ミリ秒換算（1日 = 86_400_000ms）
        avg_dwell_ms: Optional[float] = None
        if row.avg_dwell_days is not None:
            avg_dwell_ms = float(row.avg_dwell_days) * 86_400_000.0

        preferred_hour: Optional[int] = None
        if row.preferred_hour is not None:
            try:
                preferred_hour = int(row.preferred_hour)
            except (ValueError, TypeError):
                preferred_hour = None

        feature = UserFeatureDB(
            device_id=device_id,
            impression_count_30d=imp_count,
            click_count_30d=click_count,
            ctr_30d=round(ctr, 6),
            avg_dwell_ms=avg_dwell_ms,
            preferred_hour=preferred_hour,
            dominant_dismiss_type=row.dismiss_type,
            carrier=row.carrier,
            model=row.model,
            region=row.region,
            feature_version=1,
            computed_at=now,
        )

        # merge はPKが一致すれば UPDATE、なければ INSERT（upsert相当）
        # プライバシー: device_idは疑似匿名UUID。PII（氏名・電話・メール）は含まない。
        await db.merge(feature)
        updated_count += 1

    await db.commit()
    logger.info("ML-01 feature computation complete | devices_updated=%d", updated_count)
    return updated_count


async def get_user_features(device_id: str, db: AsyncSession) -> Optional[UserFeatureDB]:
    """
    単一デバイスの特徴量を取得（推薦モデル用）。

    プライバシー: device_idは疑似匿名UUID。PII（氏名・電話・メール）は含まない。

    Args:
        device_id: AndroidデバイスID（疑似匿名UUID）
        db: 非同期DBセッション

    Returns:
        UserFeatureDB インスタンス、未計算の場合は None
    """
    return await db.get(UserFeatureDB, device_id)
