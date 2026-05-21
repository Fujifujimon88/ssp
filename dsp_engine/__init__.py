"""
dsp_engine — 広告主向けパフォーマンス DSP モジュール（AppLovin / Moloco 型）。

ROAS 最適化: bid = pCTR × pCVR × 平均購入額 × (1 - margin) × 1000（CPM, 円）。
インベントリは自社 SSP オークション（main.py の auction_engine）に
LocalDspEngineDSP として参加して取得する。

詳細設計: ~/.claude/plans/https-www-applovin-com-ja-https-www-molo-proud-snowflake.md
"""
