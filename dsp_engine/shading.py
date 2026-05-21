"""
dsp_engine bid shading。

first-price オークションでは入札額がそのまま決済額になるため、フルプライス入札は
過払いを招く。過去の落札価格（dsp_spend_logs.cleared_price_jpy）の分位点を
「次回も勝てる最低入札額」の推定値として使い、入札額を勝てる範囲で割り引く。

ML は使わず、過去落札分布の P50 分位点という単純な統計量で shading する。
second-price では shading 不要なため、呼び出し側で first-price(at=1) 時のみ適用する。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import DspSpendLogDB

SHADING_PERCENTILE = 50    # 過去落札価格のこの分位点を「勝てる最低入札額」の推定に使う
COLD_START_THRESHOLD = 10  # 過去落札がこの件数未満なら shading せずフルプライス入札


def compute_shaded_bid(
    raw_bid_cpm_jpy: float,
    past_cleared_jpy: list[float],
    bidfloor_jpy: float,
) -> float:
    """bid shading 後の入札 CPM(円) を返す。

    Args:
        raw_bid_cpm_jpy: shading 前の入札 CPM（scoring の算出値）。
        past_cleared_jpy: 当該キャンペーンの過去落札価格 CPM(円) のリスト。
        bidfloor_jpy: 入札枠のフロアプライス CPM(円)。

    過去落札が COLD_START_THRESHOLD 未満ならフルプライス(raw)をそのまま返す。
    十分な履歴があれば P50 分位点を target とし、フロア下限・raw 上限でクランプする
    （shading で入札額が増額しないことを保証する）。
    """
    if len(past_cleared_jpy) < COLD_START_THRESHOLD:
        return raw_bid_cpm_jpy
    sorted_prices = sorted(past_cleared_jpy)
    idx = int(len(sorted_prices) * SHADING_PERCENTILE / 100)
    idx = min(idx, len(sorted_prices) - 1)
    target = sorted_prices[idx]
    shaded = max(bidfloor_jpy, target)
    return min(shaded, raw_bid_cpm_jpy)


async def fetch_past_cleared_prices(
    db: AsyncSession, campaign_id: str, limit: int = 200
) -> list[float]:
    """当該キャンペーンの過去落札価格 CPM(円) を新しい順で最大 limit 件取得する。"""
    rows = await db.scalars(
        select(DspSpendLogDB.cleared_price_jpy)
        .where(DspSpendLogDB.campaign_id == campaign_id)
        .order_by(DspSpendLogDB.logged_at.desc())
        .limit(limit)
    )
    return [float(p) for p in rows.all()]
