"""
dsp_engine no-bid 理由コード（OpenRTB BidResponse.nbr 相当）。

OpenRTB 標準コード（0-21）はスパイダー・非人間トラフィック等のエクスチェンジ視点
の理由であり、DSP 側の「入札しなかった理由」はカバーしない。OpenRTB は
500 以上を exchange-specific 領域として開放しているため、dsp_engine 固有の
no-bid 理由は 500 番台で定義する。

入札ログ（DspBidLogDB）と Redis 集計カウンタの両方でこのコードを使う。
"""

# ── OpenRTB 標準コード（このモジュールで使う分のみ） ──
NBR_UNKNOWN_ERROR = 0
NBR_TECHNICAL_ERROR = 1
NBR_INVALID_REQUEST = 2

# ── dsp_engine 拡張コード（500+ = exchange-specific 領域） ──
NBR_NO_ACTIVE_CAMPAIGNS = 500   # 配信中（status=active かつ期間内）のキャンペーンが無い
NBR_ALL_BUDGET_PACED = 501      # 候補は居たが全て予算ペース/総予算で除外された
NBR_BELOW_FLOOR = 502           # 最高入札がフロアプライス(USD CPM)未達
NBR_SHADED_BELOW_FLOOR = 503    # bid shading 適用後にフロア未達（first-price）
NBR_NO_IMPRESSION = 504         # BidRequest に imp が無い

# 人間可読ラベル（admin レポート・ログ用）
NBR_LABELS: dict[int, str] = {
    NBR_UNKNOWN_ERROR: "Unknown error",
    NBR_TECHNICAL_ERROR: "Technical error",
    NBR_INVALID_REQUEST: "Invalid request",
    NBR_NO_ACTIVE_CAMPAIGNS: "No active campaigns",
    NBR_ALL_BUDGET_PACED: "All campaigns budget-paced out",
    NBR_BELOW_FLOOR: "Best bid below floor price",
    NBR_SHADED_BELOW_FLOOR: "Shaded bid below floor price",
    NBR_NO_IMPRESSION: "No impression in bid request",
}


def nbr_label(code: int | None) -> str:
    """nbr コードの人間可読ラベルを返す。未知コードはコード番号を文字列化して返す。"""
    if code is None:
        return "(bid)"
    return NBR_LABELS.get(code, f"nbr-{code}")
