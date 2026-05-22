"""
dsp_engine 入札スコアリング（ベースライン ML / 経験ベイズ shrinkage）。

入札 CPM(円) = pCTR × pCVR × value × (1 - margin_rate) × 1000 をフロア/キャップでクランプ。

pCTR / pCVR / value は「観測実績」と「campaign prior（広告主提供の想定値）」を
サンプル数に応じて重み付けブレンドする shrinkage 推定で求める:

    w = n / (n + K)        K = settings.warm_threshold（prior strength）
    推定値 = w × 観測値 + (1 - w) × prior

n=0 で prior、n=K で 50:50、n→∞ で観測値。実績 50 件で実績ベースへ硬切替する旧方式の
cliff（入札額の不連続なジャンプ）を解消し、実績が貯まるほど滑らかに観測値へ寄せる。

- pCTR : 観測 = clicks / impressions、prior = campaign.base_ctr、n = impressions
- pCVR : 観測 = conversions / clicks、prior = campaign.target_cvr、n = clicks
- value: 観測 = revenue / conversions、prior = campaign.avg_purchase_value_jpy、n = conversions
"""
from config import settings


def _shrink(observed: float, prior: float, n: int, strength: float) -> float:
    """サンプル数 n に応じて観測値と prior をブレンドする経験ベイズ shrinkage。"""
    if n <= 0:
        return prior
    w = n / (n + max(strength, 0.0))
    return w * observed + (1.0 - w) * prior


def predict_ctr(campaign, stats: dict) -> float:
    """pCTR（クリック率）を shrinkage 推定する。"""
    impressions = int(stats.get("impressions", 0) or 0)
    clicks = int(stats.get("clicks", 0) or 0)
    observed = clicks / impressions if impressions > 0 else 0.0
    return _shrink(observed, campaign.base_ctr, impressions, settings.warm_threshold)


def predict_cvr(campaign, stats: dict) -> float:
    """pCVR（コンバージョン率）を shrinkage 推定する。"""
    clicks = int(stats.get("clicks", 0) or 0)
    conversions = int(stats.get("conversions", 0) or 0)
    observed = conversions / clicks if clicks > 0 else 0.0
    return _shrink(observed, campaign.target_cvr, clicks, settings.warm_threshold)


def predict_value(campaign, stats: dict) -> float:
    """1コンバージョンあたりの期待売上（円）を shrinkage 推定する。"""
    conversions = int(stats.get("conversions", 0) or 0)
    revenue = float(stats.get("revenue_jpy", 0.0) or 0.0)
    observed = revenue / conversions if conversions > 0 else 0.0
    return _shrink(observed, campaign.avg_purchase_value_jpy, conversions, settings.warm_threshold)


def expected_value_per_impression(campaign, stats: dict) -> float:
    """1インプレッションあたりの期待売上（円）= pCTR × pCVR × value。"""
    return predict_ctr(campaign, stats) * predict_cvr(campaign, stats) * predict_value(campaign, stats)


def compute_bid_cpm_jpy(campaign, stats: dict, ctr_multiplier: float = 1.0) -> float:
    """入札 CPM（円, 1000インプレッションあたり）を算出する。

    raw = pCTR × ctr_multiplier × pCVR × value × (1 - margin_rate) × 1000
    を bid_floor_jpy / bid_cap_jpy でクランプして返す。

    ctr_multiplier: device セグメント乗数（dsp_engine/segments.py）。pCTR を補正する。
    """
    ev = (
        predict_ctr(campaign, stats) * ctr_multiplier
        * predict_cvr(campaign, stats)
        * predict_value(campaign, stats)
    )
    raw_cpm = ev * (1.0 - campaign.margin_rate) * 1000.0
    return max(campaign.bid_floor_jpy, min(raw_cpm, campaign.bid_cap_jpy))
