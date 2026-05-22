# 計画: #4 入札ログ完全化 + 予算 TOCTOU 対策

## 3行サマリー
- bid request 毎に no-bid 理由コード `nbr` 付きの判定ログ(`dsp_bid_logs` 全行 + Redis nbr 集計)を記録する。
- `can_bid`→`record_dsp_win`→`record_spend` の TOCTOU を、win 時の atomic INCRBYFLOAT + 累計超過判定で抑え、超過時はキャンペーンを `budget_exhausted` に自動切替。
- TDD(Red 先行)。既存 dsp_engine 系テスト非破壊・本番未デプロイ前提。

## Part A: 入札ログ完全化
- A1 `dsp_engine/nbr.py` 新規 — NoBidReason 定数。標準(0/1/2) + 拡張(500 No active campaigns / 501 All budget-paced / 502 Below floor / 503 Shaded below floor / 504 No impression) + ラベル表。
- A2 `db_models.py` — `DspBidLogDB`(dsp_bid_logs) 追加。request_id/source/imp_id/bidfloor_usd/outcome/nbr/campaign_id/bid_price_usd/bid_cpm_jpy/shaded/candidate_count/paced_out_count/logged_at。
- A3 `alembic/versions/dspengine0005_*.py` — down_revision=dspengine0004、冪等(CREATE TABLE IF NOT EXISTS、lesson #14/#15/#16 準拠)。
- A4 `dsp_engine/bidder.py` — `handle_bid_request` の全分岐(no-imp/no-campaign/all-paced/below-floor/shaded-below-floor/bid成立)で `_log_bid_decision()` を呼ぶ。DB INSERT 1 行 + Redis nbr カウンタ INCR(`dsp:nbr:{YYYYMMDD}:{nbr}` TTL 2日)。戻り型は `Optional[BidResponse]` のまま(caller 変更なし)。ログ失敗は warning 出力して握りつぶし(入札を巻き込まない)。
- A5 `dsp_engine/router.py` — `GET /dsp-engine/admin/bid-logs/api`(JSON、直近 N 件 + nbr 別集計)。フル UI は作らない(#6 スコープ)。

## Part B: 予算 TOCTOU 対策
- B1 `dsp_engine/pacing.py` — Redis パスの INCRBYFLOAT+EXPIRE を Lua スクリプトで1往復・原子化。`record_spend` は加算後の新累計を返す(既存仕様維持)。mem fallback も維持。
- B2 `dsp_engine/bidder.py` `record_dsp_win` — win 記録(commit)後、`SUM(spend_jpy)` で lifetime spend を取得。`total_budget_jpy>0 かつ lifetime>=total_budget` なら `status='budget_exhausted'` に自動切替 + warning ログ。次 bid 以降は `list_active_campaigns`(status='active' フィルタ)から除外され入札停止。

## Part C: テスト(TDD)
- C1 `tests/test_dsp_bid_log.py` 新規(Red 先行) — 各 nbr ケース記録 / bid 成立記録 / candidate・paced_out 件数 / Redis カウンタ / record_dsp_win 超過→budget_exhausted / 超過後 no-bid / record_spend atomic(Redis有無) / admin/bid-logs/api。
- C2 既存 test_dsp_engine 40 / test_auction / test_shading の非破壊を確認。
- C3 マイグレーション検証は populated DB コピー + `stamp dspengine0004` + `upgrade head`(lesson #16)。

## スコープ外
- bid log のフル admin UI(#6) / 入札パス DB write の batching(#10) / nbr を OpenRTB no-bid を 204→200 に変える件(標準 204 維持)。

## 完了条件
- `pytest tests/` で dsp_engine 系全 green、新規テスト green、既知 MDM 6 件以外 fail 無し。
- 本番デプロイは #4 完了後に Fuji さん判断(`vercel --prod` 手動)。
