# 計画: #6 多次元レポート拡張（creative/publisher/app/placement/geo/deal_id）

## 3行サマリー
- レポート軸に creative / publisher / app / placement / geo / deal_id の 6 軸を追加する。
- 6 軸は 3 イベントテーブル（spend/click/conversion）に非正規化記録: spend は落札時に
  BidRequest + campaign から、click/conversion は click_token 経由で spend からコピー。
- reporting.py は `_DIM_COLUMNS` / `AVAILABLE_DIMENSIONS` への追加のみで既存構造を保つ。TDD（Red 先行）。

## Part A: スキーマ
- `DspCampaignDB` に `creative_id`（String(36), default uuid）追加。
- `DspSpendLogDB` / `DspClickEventDB` / `DspConversionEventDB` に 6 軸カラム追加
  （`creative_id` / `publisher_id` / `app_id` / `placement` / `geo` / `deal_id`、全て nullable String）。
- マイグレーション `dspengine0007`（冪等・inspector で列存在チェック）。既存 campaign の
  `creative_id` は `UPDATE ... SET creative_id = id` で backfill（本番は campaign 0 件で実質 no-op）。

## Part B: 記録（書き込み側）
- `reporting.extract_report_dims(bid_request)` 新規 — BidRequest から
  publisher（site.publisher.id）/ app（app.id）/ placement（imp.tagid）/ geo（device.geo.country）/
  deal_id（imp.pmp.deals）を抽出して dict で返すヘルパー。
- `record_dsp_win` に `bid_request` 引数追加 — spend log に 6 軸を記録
  （creative_id は campaign から、他は extract_report_dims から）。
- `main.py` `/v1/bid` — `record_dsp_win(..., bid_request=bid_request)` を渡す。
- `record_click` / `record_conversion` — 既に click_token で引いている spend log から
  6 軸をコピーして DspClickEventDB / DspConversionEventDB に記録。

## Part C: レポート（reporting.py）
- `AVAILABLE_DIMENSIONS` に 6 軸を追加。`_DIM_COLUMNS` に各軸のエントリ追加
  （3 テーブルとも同名カラムなので素直にマップ）。`admin/report` 画面は
  `AVAILABLE_DIMENSIONS` を自動描画するため UI 変更は最小（必要なら report.html 微修正）。

## Part D: テスト（TDD）
- `tests/test_dsp_reporting.py` 新規（Red 先行）— extract_report_dims / record_dsp_win が
  6 軸を spend log に記録 / record_click・record_conversion が spend からコピー /
  run_report が新軸で GROUP BY 集計 / 既存 4 軸の非破壊。
- マイグレーションは populated DB コピーで検証（教訓16）。

## 既知の制約（計画段階で明示）
- 外部エクスチェンジ落札パス（`win_notice`）は BidRequest を持たないため
  publisher/app/placement/geo/deal_id は null 記録（creative_id は campaign から解決可）。
  外部エクスチェンジは未提携（ビジネス側タスク）のため現状実害なし。実提携時に nurl 経由の
  dim 伝搬を別タスクで対応。
- 自社 SSP web ヘッダービディングは site ベースのため app/geo/deal_id は通常 null
  （publisher/placement は populate される）。カラムは将来のモバイル/PMP トラフィック用に用意。
- creative 軸は 1 campaign:1 creative のため当面 campaign 軸と実質同値。#7 の 1:N 化で本来の意味を持つ。

## スコープ外
- creative の 1:N 化（#7）/ geo の IP ジオロケーション導出 / 外部パスの dim 伝搬。

## 完了条件
- `pytest tests/` 全 green（既知 MDM 6 件以外 fail なし）。本番デプロイは Fuji さん判断。
