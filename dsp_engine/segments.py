"""
dsp_engine device セグメント乗数（入札 ML ベースライン）。

device 特徴量（platform: android / ios / web / unknown）ごとの観測 CTR を、
低遅延・高 QPS の入札パスでは計算できない。このモジュールのバックグラウンド
ループ（lifespan から create_task で起動）が定期的に DspSpendLogDB（imp）と
DspClickEventDB（click）を集計し、全体 CTR に対するセグメント乗数を算出して
DspSegmentPerfDB + L1 メモリキャッシュへ反映する。

入札時は get_segment_multiplier() で L1 キャッシュを参照するだけ（DB I/O なし）。
これは #3 で確立した「入札パスに外部 I/O を入れない」設計原則に従う。
"""
import asyncio
import logging

from sqlalchemy import func, select

from database import AsyncSessionLocal
from db_models import DspClickEventDB, DspSegmentPerfDB, DspSpendLogDB
from utils import utcnow

logger = logging.getLogger(__name__)

SEG_MULT_MIN = 0.5          # セグメント乗数の下限
SEG_MULT_MAX = 2.0          # セグメント乗数の上限
SEG_MIN_SAMPLES = 100       # セグメントの imp 数がこれ未満なら乗数 1.0（信頼不足）
SEGMENT_REFRESH_SEC = 3600  # 1 時間ごとに再計算

# L1 メモリキャッシュ {segment: multiplier}。バッチが更新、入札パスは参照のみ。
_seg_cache: dict[str, float] = {}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))


def platform_of(device) -> str:
    """OpenRTB Device から platform セグメントを導出する（android/ios/web/unknown）。"""
    if device is None:
        return "unknown"
    os_name = (getattr(device, "os", None) or "").lower()
    if "android" in os_name:
        return "android"
    if "ios" in os_name or "iphone" in os_name or "ipad" in os_name:
        return "ios"
    if getattr(device, "devicetype", None) == 1:  # OpenRTB devicetype 1 = PC
        return "web"
    return "unknown"


def get_segment_multiplier(segment: str) -> float:
    """segment の CTR 乗数を L1 キャッシュから返す（未登録セグメントは 1.0=補正なし）。"""
    return _seg_cache.get(segment, 1.0)


async def recompute_segment_multipliers(db) -> dict[str, float]:
    """platform 別 CTR 乗数を集計・算出し、DspSegmentPerfDB と L1 キャッシュへ反映する。

    乗数 = clamp(セグメント CTR / 全体 CTR, SEG_MULT_MIN, SEG_MULT_MAX)。
    imp 数が SEG_MIN_SAMPLES 未満、または全体 CTR が 0 のセグメントは 1.0（補正なし）。
    戻り値は {segment: multiplier}。
    """
    imp_rows = (await db.execute(
        select(DspSpendLogDB.platform, func.count()).group_by(DspSpendLogDB.platform)
    )).all()
    click_rows = (await db.execute(
        select(DspClickEventDB.platform, func.count()).group_by(DspClickEventDB.platform)
    )).all()
    imp_by = {p: int(n) for p, n in imp_rows}
    click_by = {p: int(n) for p, n in click_rows}

    total_imp = sum(imp_by.values())
    total_click = sum(click_by.values())
    overall_ctr = (total_click / total_imp) if total_imp > 0 else 0.0

    multipliers: dict[str, float] = {}
    for seg in set(imp_by) | set(click_by):
        imp = imp_by.get(seg, 0)
        clk = click_by.get(seg, 0)
        seg_ctr = (clk / imp) if imp > 0 else 0.0
        if imp >= SEG_MIN_SAMPLES and overall_ctr > 0:
            mult = _clamp(seg_ctr / overall_ctr, SEG_MULT_MIN, SEG_MULT_MAX)
        else:
            mult = 1.0
        multipliers[seg] = mult

        row = await db.get(DspSegmentPerfDB, seg)
        if row is None:
            row = DspSegmentPerfDB(segment=seg)
            db.add(row)
        row.impressions = imp
        row.clicks = clk
        row.ctr = seg_ctr
        row.multiplier = mult
        row.updated_at = utcnow()

    if multipliers:
        await db.commit()
    # L1 キャッシュを全置換（消えたセグメントを残さない）
    _seg_cache.clear()
    _seg_cache.update(multipliers)
    logger.info(f"dsp-engine segment multipliers recomputed: {multipliers}")
    return multipliers


async def prime_segment_cache(db) -> None:
    """プロセス起動時に DspSegmentPerfDB から L1 キャッシュを温める。"""
    rows = (await db.scalars(select(DspSegmentPerfDB))).all()
    _seg_cache.clear()
    for r in rows:
        _seg_cache[r.segment] = r.multiplier


async def schedule_segment_tasks() -> None:
    """lifespan から create_task で起動するバックグラウンドループ。

    起動時に L1 キャッシュを温め、以降 SEGMENT_REFRESH_SEC ごとに再計算する。
    例外は握りつぶしてループを継続する（バッチ失敗で本体を巻き込まない）。
    """
    try:
        async with AsyncSessionLocal() as db:
            await prime_segment_cache(db)
    except Exception as exc:
        logger.error(f"dsp-engine segment cache prime failed: {exc}")
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await recompute_segment_multipliers(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"dsp-engine segment batch failed: {exc}")
        await asyncio.sleep(SEGMENT_REFRESH_SEC)
