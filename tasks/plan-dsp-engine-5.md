# 計画: #5 pCTR/pCVR/value/win-rate ベースライン ML

## 3行サマリー
- 入札スコアを pCTR×pCVR×value に分解し、各成分を経験ベイズ shrinkage（観測値と campaign prior を
  サンプル数で重み付けブレンド）で推定。WARM_THRESHOLD の硬切替（cliff）を廃し prior strength として config 化。
- device 特徴量（platform）別の CTR 乗数を定期バッチで事前計算し、入札時は L1 キャッシュ参照のみで pCTR に反映。
- win-rate（落札率）を dsp_bid_logs + dsp_spend_logs から算出し admin で可視化（入札へは反映しない）。
- TDD（Red 先行）。統計手法のみ・TensorFlow 不要・入札パスに外部 I/O を入れない。

## Part A: WARM_THRESHOLD 設定化
- `config.py` に `warm_threshold: int = 50` を追加（env `WARM_THRESHOLD`）。shrinkage の prior strength として使う。

## Part B: shrinkage 推定（scoring.py）
- `_shrink(observed, prior, n, strength)` — n=0で prior、n=strength で 50:50、n→∞ で観測値。
- `predict_ctr`（clicks/impressions ↔ base_ctr）/ `predict_cvr`（conversions/clicks ↔ target_cvr）/
  `predict_value`（revenue/conversions ↔ avg_purchase_value_jpy）。
- `expected_value_per_impression = pCTR×pCVR×value`。`compute_bid_cpm_jpy(campaign, stats, ctr_multiplier=1.0)`。
- 後方互換: n=0（コールド）は旧コールド式と同値。warm ケースの既存テスト
  `test_scoring_warm_uses_realized_revenue` は新モデルに合わせ更新（承認済み仕様変更）。

## Part C: device セグメント乗数（定期バッチ事前計算）
- `db_models.py` に `DspSegmentPerfDB`（dsp_segment_perf: segment/impressions/clicks/ctr/multiplier/updated_at）+
  マイグレーション `dspengine0006`（冪等・has_table ガード）。
- `dsp_engine/segments.py` 新規 — `platform_of(device)`（android/ios/web/unknown）/ L1 キャッシュ /
  `get_segment_multiplier` / `recompute_segment_multipliers`（DspSpendLog imp + DspClickEvent click から
  platform 別 CTR 乗数を算出、[0.5, 2.0] でクランプ、低サンプルは 1.0）/ `schedule_ml_batch_tasks`（1h ループ）。
- `main.py` lifespan に ml バッチタスクを登録（supply-chain バッチと同パターン）。
- `bidder.py` handle_bid_request: `bid_request.device` から segment を導出し乗数を `compute_bid_cpm_jpy` に渡す。
- セグメント粒度は現状 spend/click ログが持つ `platform` のみ（devicetype/os/geo の細分化は別タスク）。

## Part D: win-rate 可視化
- `get_campaign_win_rates(db)` — campaign 別 wins(DspSpendLog 件数)/bids(DspBidLog outcome=bid 件数)。
- `get_bid_log_summary` の戻りに `campaign_win_rates` を追加（admin/bid-logs/api で参照可）。入札へは反映しない。

## Part E: テスト（TDD）
- `tests/test_ml_scoring.py` 新規（Red 先行）— shrinkage 境界 / predict_* / EV 合成 / ctr_multiplier /
  platform_of / recompute_segment_multipliers（クランプ・キャッシュ）/ get_campaign_win_rates / config 反映。
- 既存 scoring テストの warm ケースを更新。マイグレーションは populated DB コピーで検証（lesson #16）。

## スコープ外
- two_tower TFLite の入札接続 / devicetype・os・geo の細分セグメント / win-rate 連動入札 / 学習パイプライン。

## 完了条件
- `pytest tests/` 全 green（既知 MDM 6 件以外 fail なし）。本番デプロイは Fuji さん判断。
