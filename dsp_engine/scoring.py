"""
dsp_engine 入札スコアリング。

入札 CPM(円) = 期待売上/imp × (1 - margin_rate) × 1000 をフロア/キャップでクランプ。

期待売上/imp:
  - 実績期 (落札 impression が WARM_THRESHOLD 以上): 実測の revenue_jpy / impressions
  - コールドスタート期: base_ctr × target_cvr × avg_purchase_value_jpy
    （広告主が提供する想定 CTR/CVR/購入単価を初期値に使う）
"""

WARM_THRESHOLD = 50  # この件数以上の落札実績があれば実績ベースへ切替


def expected_value_per_impression(campaign, stats: dict) -> float:
    """1インプレッションあたりの期待売上（円）を返す。

    Args:
        campaign: DspCampaignDB（base_ctr / target_cvr / avg_purchase_value_jpy を参照）
        stats:    {"impressions": int, "conversions": int, "revenue_jpy": float}
    """
    impressions = int(stats.get("impressions", 0) or 0)
    if impressions >= WARM_THRESHOLD:
        revenue = float(stats.get("revenue_jpy", 0.0) or 0.0)
        return revenue / impressions
    # コールドスタート: 広告主提供の想定値
    return campaign.base_ctr * campaign.target_cvr * campaign.avg_purchase_value_jpy


def compute_bid_cpm_jpy(campaign, stats: dict) -> float:
    """入札 CPM（円, 1000インプレッションあたり）を算出する。

    raw = 期待売上/imp × (1 - margin_rate) × 1000
    を bid_floor_jpy / bid_cap_jpy でクランプして返す。
    """
    ev = expected_value_per_impression(campaign, stats)
    raw_cpm = ev * (1.0 - campaign.margin_rate) * 1000.0
    return max(campaign.bid_floor_jpy, min(raw_cpm, campaign.bid_cap_jpy))
