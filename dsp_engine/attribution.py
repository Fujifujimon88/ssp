"""
dsp_engine 購入CVの取り込みと ROAS 計算。

広告主は AppsFlyer/Adjust の purchase ポストバック先を /dsp-engine/conversion に
設定する。ad markup に埋め込んだ click_token（dsp_ct）を使って
DspSpendLogDB → campaign_id / impression_id を解決しアトリビューションする。
dedup_key（appsflyer_event_id 等）で重複ポストバックを冪等に排除する。
"""
import hashlib
import hmac
import logging
from datetime import timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import DspClickEventDB, DspConversionEventDB, DspSpendLogDB
from dsp_engine.campaign_manager import get_campaign_stats
from dsp_engine.currency import usd_to_jpy
from utils import utcnow

logger = logging.getLogger(__name__)


def verify_postback_secret(provided: str, expected: str) -> bool:
    """静的シークレットの timing-safe 比較 (hmac.compare_digest 使用)。"""
    return hmac.compare_digest(provided, expected)


def sanitize_pii_payload(payload: dict, pii_keys: list = None) -> dict:
    """PII キーを payload dict から除去して新しい dict を返す (元 dict は変更しない)。"""
    _DEFAULT_PII_KEYS = [
        "idfa", "gaid", "device_id", "ip", "user_agent", "ua",
        "android_id", "appsflyer_id",
    ]
    keys_to_strip = set(pii_keys if pii_keys is not None else _DEFAULT_PII_KEYS)
    return {k: v for k, v in payload.items() if k not in keys_to_strip}


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
    window_days: int = 30,
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
    spend_log: Optional[DspSpendLogDB] = None

    # click_token から campaign_id / impression_id を解決
    if click_token:
        spend_log = await db.scalar(
            select(DspSpendLogDB).where(DspSpendLogDB.click_token == click_token)
        )
        if spend_log:
            from datetime import datetime as _dt
            now_utc = _dt.now(timezone.utc)
            cutoff = now_utc - timedelta(days=window_days)
            log_dt = spend_log.logged_at
            # naive datetime を aware UTC に正規化して比較
            if log_dt.tzinfo is None:
                log_dt = log_dt.replace(tzinfo=timezone.utc)
            # campaign_id は窓内外問わず解決する（CV の記録自体は必須）
            campaign_id = campaign_id or spend_log.campaign_id
            if log_dt >= cutoff:  # 窓内 (境界値含む)
                impression_id = spend_log.impression_id
                if platform == "unknown":
                    platform = spend_log.platform
            else:
                spend_log = None  # 窓外: impression_id・多次元軸を紐付けない

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
        # レポート多次元軸（#6）: click_token 経由で spend log からコピー。
        creative_id=spend_log.creative_id if spend_log else None,
        publisher_id=spend_log.publisher_id if spend_log else None,
        app_id=spend_log.app_id if spend_log else None,
        placement=spend_log.placement if spend_log else None,
        geo=spend_log.geo if spend_log else None,
        deal_id=spend_log.deal_id if spend_log else None,
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
        {impressions, clicks, spend_jpy, conversions, revenue_jpy,
         roas(%), cpa(円), ctr(%)}
        ROAS(%) = 売上 / 消化 × 100、CPA = 消化 / CV数、CTR(%) = クリック / imp × 100。
    """
    stats = await get_campaign_stats(db, campaign_id)
    spend = stats["spend_jpy"]
    revenue = stats["revenue_jpy"]
    conversions = stats["conversions"]
    impressions = stats["impressions"]
    clicks = stats["clicks"]
    roas = (revenue / spend * 100.0) if spend > 0 else 0.0
    cpa = (spend / conversions) if conversions > 0 else 0.0
    ctr = (clicks / impressions * 100.0) if impressions > 0 else 0.0
    return {
        **stats,
        "roas": round(roas, 2),
        "cpa": round(cpa, 2),
        "ctr": round(ctr, 2),
    }


async def record_click(
    db: AsyncSession,
    click_token: str,
    rate_limited: bool = False,
) -> Optional[DspSpendLogDB]:
    """クリックトラッカー経由のクリックをクリックイベントとして記録する。

    click_token に対応する落札ログ（DspSpendLogDB）を引き、DspClickEventDB を
    1件追加する。同一トークンの再クリックも毎回 1 件記録する（= 実クリック数）。
    対応する落札ログが無い場合（未知トークン）は None を返し、記録しない。

    rate_limited=True の場合は DspClickEventDB の挿入をスキップして即 None を返す
    （#8: クリック連打レート制限）。

    Returns:
        対応する DspSpendLogDB（クリックエンドポイントが LP 解決に使う）/ None。
    """
    if rate_limited:
        return None

    log = await db.scalar(
        select(DspSpendLogDB).where(DspSpendLogDB.click_token == click_token)
    )
    if log is None:
        return None
    db.add(DspClickEventDB(
        campaign_id=log.campaign_id,
        click_token=click_token,
        impression_id=log.impression_id,
        platform=log.platform,
        source=log.source,
        # レポート多次元軸（#6）: spend log からコピー。
        creative_id=log.creative_id,
        publisher_id=log.publisher_id,
        app_id=log.app_id,
        placement=log.placement,
        geo=log.geo,
        deal_id=log.deal_id,
        clicked_at=utcnow(),
    ))
    await db.commit()
    return log


# ── 実MMP（AppsFlyer / Adjust）ポストバック形式の正規化 ─────────
# 各 MMP は独自のパラメータ名を使う。広告主が当社の標準名にマッピングするのが基本だが、
# 設定ミスを減らすため代表的な別名も受け付ける。

_CLICK_TOKEN_KEYS = ["dsp_ct", "click_token", "click_id", "clickid", "af_click_id"]
_REVENUE_KEYS = ["revenue_jpy", "revenue", "event_revenue", "af_revenue", "eventRevenue"]
_DEDUP_KEYS = ["dedup_key", "event_id", "appsflyer_event_id", "af_event_id", "transaction_id"]
_EVENT_KEYS = ["event_type", "event_name", "af_event_name", "event"]
_CURRENCY_KEYS = ["revenue_currency", "event_revenue_currency", "currency", "af_currency"]
_CAMPAIGN_KEYS = ["campaign_id", "cid"]
_PLATFORM_KEYS = ["platform", "os", "device_os", "platform_name"]
_SOURCE_KEYS = ["source", "mmp", "partner"]

# MMP 自動判定用のシグネチャキー
_APPSFLYER_KEYS = {"event_revenue", "af_event_name", "appsflyer_id", "af_click_id"}
_ADJUST_KEYS = {"adid", "gps_adid", "activity_kind", "adjust_id"}


def _first(params: dict, keys: list[str]):
    for key in keys:
        value = params.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_conversion_payload(params: dict) -> dict:
    """MMP のポストバックパラメータを当社の標準形に正規化する。

    AppsFlyer / Adjust / 当社標準名のいずれの形式でも受け付け、
    通貨が USD の場合は JPY に換算する。MMP 種別を source に自動判定する。
    """
    raw_revenue = _first(params, _REVENUE_KEYS)
    try:
        revenue = float(raw_revenue) if raw_revenue is not None else 0.0
    except (TypeError, ValueError):
        revenue = 0.0

    currency = str(_first(params, _CURRENCY_KEYS) or "JPY").upper()
    revenue_jpy = usd_to_jpy(revenue) if currency == "USD" else revenue

    # source は明示指定を最優先。無ければ MMP 固有キーから自動判定する。
    explicit_source = _first(params, _SOURCE_KEYS)
    keys = set(params.keys())
    if explicit_source:
        source = str(explicit_source)
    elif keys & _APPSFLYER_KEYS:
        source = "s2s_appsflyer"
    elif keys & _ADJUST_KEYS:
        source = "s2s_adjust"
    else:
        source = "direct"

    return {
        "click_token": _first(params, _CLICK_TOKEN_KEYS),
        "campaign_id": _first(params, _CAMPAIGN_KEYS),
        "revenue_jpy": revenue_jpy,
        "dedup_key": _first(params, _DEDUP_KEYS),
        "event_type": _first(params, _EVENT_KEYS) or "purchase",
        "platform": str(_first(params, _PLATFORM_KEYS) or "unknown").lower(),
        "source": source,
    }
