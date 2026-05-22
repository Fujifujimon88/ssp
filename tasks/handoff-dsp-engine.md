# 引き継ぎ: dsp_engine（広告主向けパフォーマンス DSP）

Status: Verified
最終更新: 2026-05-22

AppLovin / Moloco 型の ROAS 最適化 DSP を既存リポ内 `dsp_engine/` モジュールとして構築した
作業の引き継ぎ。優先タスクの状態管理は `tasks/progress-dsp-engine.md`（進捗管理表）に集約。

---

## 1. 概要

- **目的**: 広告主に「効果（売上 / ROAS）」を出す DSP。入札・予算ペーシング・クリック計測・
  購入CV計測・ROAS 集計・広告主ダッシュボードを持つ。
- **入札式**: `bid_cpm_jpy = pCTR × pCVR × 平均購入額 × (1 - margin) × 1000`、
  フロア/キャップでクランプ。実績50件未満はコールドスタート（広告主提供の想定値）。
- **インベントリ**: 自社 SSP オークション（`main.py` の `auction_engine`）に
  `LocalDspEngineDSP` として参加。外部エクスチェンジからは受信側 OpenRTB 入札も受ける。
- **計測ループ**: 広告内リンク → `/dsp-engine/click` → 広告主LP（`dsp_ct` 付与）
  → 購入 → MMP/広告主が `/dsp-engine/conversion` へポストバック → click_token で紐付け → ROAS。

---

## 2. 進捗

| フェーズ | 内容 | 状態 |
|---|---|---|
| Phase 1〜2.6 | ROAS MVP / 外部エクスチェンジ連携 / クリック計測 / レビュー改善 | 完了・コミット済み（91ddb6f）|
| 優先 #1 | OpenRTB 2.6 スキーマ拡張（App/Source/Regs/Pmp/Deal/eids/burl・lurl/Video・Device）| 完了・push 済み |
| 優先 #2 | first-price auction 対応 + bid shading（P50 分位点）| 完了・push 済み |
| 優先 #3 | サプライチェーン検証（schain 構造検証 / sellers.json 突合 / ads.txt・app-ads.txt / 自社 sellers.json）| 完了・push 済み（6839ab1）|
| 優先 #4 | 入札ログ完全化（nbr 付き `dsp_bid_logs` + Redis 集計）+ 予算 TOCTOU 対策（総予算超過で `budget_exhausted` 自動切替）| 完了・未コミット |
| 優先 #5 | ベースライン ML（pCTR×pCVR×value の shrinkage 推定 / WARM_THRESHOLD 設定化 / device セグメント乗数バッチ / win-rate 可視化）| 完了・未コミット |

**本番デプロイ状況**: Phase 2.5 まで本番反映済み。Phase 2.6 + 優先 #1〜#5 は
**Vercel 未デプロイ**（Git 未連携のため `vercel --prod` の手動実行が必要）。マイグレーション
dspengine0003〜0006 は本番 Postgres 未適用。

---

## 3. 主要ファイル

```
dsp_engine/
  bidder.py          入札ロジック / LocalDspEngineDSP / record_dsp_win（落札記録・冪等）
  scoring.py         入札CPM算出（shrinkage 推定: pCTR×pCVR×value）★#5
  shading.py         bid shading（first-price 時のみ・過去落札 P50 分位点）★#2
  pacing.py          予算ペーシング（日予算 smooth pacing + 総予算チェック）
  campaign_manager.py キャンペーンCRUD / get_all_campaign_stats（一括集計）
  attribution.py     購入CV受信 / record_click / normalize_conversion_payload / ROAS算出
  exchange.py        外部エクスチェンジ識別・QPS制御・認証・統計
  supply_chain.py    schain 構造検証（入札パス内・純粋関数）★#3
  sjcache.py         sellers.json fetch・TTLキャッシュ・突合 ★#3
  adstxt.py          ads.txt / app-ads.txt パース・fetch・検証 ★#3
  batch.py           サプライチェーン定期検証バッチ（lifespan タスク）★#3
  nbr.py             no-bid 理由コード（nbr）定義・ラベル ★#4
  segments.py        device セグメント別CTR乗数バッチ + L1キャッシュ ★#5
  reporting.py       多次元レポート
  currency.py        円/ドルレート
  supply.py          SSP連携接続のCRUD / 外部IDマッピング / parse_allowed_asi_domains
  router.py          全エンドポイント
auction/
  openrtb.py         OpenRTB 2.6 相当スキーマ ★#1 / _compute_clearing_price ★#2
  engine.py          オークションエンジン（at で first/second 決済切替）★#2
```

主な既存ファイル変更: `db_models.py`、`main.py`（auction登録・lifespan・sellers.json・
supply-chain バッチ起動）、`config.py`（jpy_per_usd / ssp_domain 等）。

### DBテーブル（マイグレーション dspengine0001〜0006）
- `dsp_campaigns` / `dsp_spend_logs` / `dsp_click_events` / `dsp_conversion_events`
- `dsp_configs`（拡張）— SSP連携 + schain 検証カラム（dspengine0004 で追加）
- `dsp_bid_logs` — 入札判定ログ（nbr 付き全 bid request 記録。dspengine0005 で追加）★#4
- `dsp_segment_perf` — device セグメント別 CTR 乗数（dspengine0006 で追加）★#5

### 主要エンドポイント
- `POST /v1/bid` — SSPヘッダービディング（dsp-engine 参加）
- `POST /dsp-engine/exchange/{name}/bid` — 外部エクスチェンジ受信入札（schain 検証付き）
- `GET /dsp-engine/win` / `click` / `conversion` — 落札通知・クリック計測・CVポストバック
- `GET /dsp-engine/advertiser/*` — 広告主向け / `GET /dsp-engine/admin/*` — 運用者向け
- `GET /dsp-engine/admin/bid-logs/api` — 入札判定ログ + no-bid 理由(nbr)内訳（運用者向け）★#4
- `GET /sellers.json`（SSP INTERMEDIARY 記載）/ `GET /api/publishers/me/{ads-txt,app-ads-txt}`

---

## 4. 検証状況（Verified の根拠）

- 全体: `pytest tests/` → **271 passed**, 6 failed, 1 skipped。
  6 failed は `test_android_mdm.py` / `test_mdm_profile_resilience.py` の**事前不具合**で
  dsp_engine と無関係（誤って「壊した」と判断しないこと）。
- dsp_engine 系テスト: test_dsp_engine 40 / test_auction 14 / test_shading 7 /
  test_openrtb_26 13 / test_supply_chain 10 / test_sjcache 9 / test_adstxt 10 /
  test_dsp_bid_log 12 / test_ml_scoring 13。
- 各優先タスクは TDD（Red 先行）で実装。後方互換も明示テストで担保。
- マイグレーション dspengine0004 はローカル fresh SQLite では検証不可（既知制約）。
  本番は lifespan の `alembic upgrade head` で適用される。

---

## 5. 開発・運用手順

### ローカル起動
```
python tests/_local_demo_setup.py
DATABASE_URL="sqlite+aiosqlite:///./ssp_local.db" python -m alembic stamp <head>
DATABASE_URL="sqlite+aiosqlite:///./ssp_local.db" APP_ENV=development \
  ADMIN_ALLOWED_IPS=127.0.0.1 SKIP_LIFESPAN_ALEMBIC=1 \
  python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

### 本番デプロイ
- Vercel と Git 未連携。`git push` では本番反映されない。`vercel --prod` の手動実行が必須。
- 起動時 lifespan が本番 Postgres へ `alembic upgrade head` を自動実行。
- 本番URL: `https://ssp-platform.vercel.app`

---

## 6. やること（残タスク・優先順）

完了: #1・#2・#3・#4・#5。状態詳細・該当ファイルは `tasks/progress-dsp-engine.md` の優先タスク表参照。

| # | やること | 優先度 |
|---|---|---|
| 6 | creative / publisher / app / placement / geo / device / deal_id 別レポート | **中（次着手）** |
| 7 | A/B テスト・holdout 基盤（複数クリエイティブ 1:N 化が前提）| 中 |
| 8 | fraud / IVT / brand safety 監視（クリック連打レート制限を含む）| 中 |
| 9 | MMP 署名検証・SKAN・Privacy Sandbox 対応（PII サニタイズ・アトリビューション窓）| 中 |
| 10 | データ基盤・運用堅牢化（複合インデックス・管理画面 N+1 解消・QPS カウンタ Redis 化）| 中〜低 |
| 11 | 動的フロア最適化（落札率・bid density ベース。#2 から分離）| 中 |

ビジネス側（コード外）: 実広告主オンボーディング / 外部エクスチェンジ実提携 /
本番初回 DSPキャンペーン登録（未登録のため本番は現状 inert）。

運用ガード: 本番 RTB bidder は低遅延・高 QPS。入札パスに外部 HTTP fetch を入れない
（#3 で確立。sellers.json / ads.txt の fetch は `batch.py` のバックグラウンドのみ）。
生成コードは benchmark / load test を通す。PII・広告 ID・CV データをプロンプトへ渡さない。

---

## 7. 既知の制約・注意点（次セッションへの申し送り）

1. **#1〜#5 は本番未デプロイ**: `vercel --prod` 未実行。dspengine0003〜0006
   マイグレーションも本番未適用。本番反映時は `vercel --prod`。#4・#5 は未コミット。
2. **lifespan の Alembic がローカルSQLiteでデッドロック**: ローカル起動時は
   `SKIP_LIFESPAN_ALEMBIC=1` を付ける（本番 Vercel では未設定＝従来動作）。
3. **Alembic チェーンは fresh SQLite で通らない**: マイグレーション検証は
   「populated DB のコピー + stamp + upgrade」で行う。
4. **Vercel は Git 未連携**: `git push` では本番デプロイされない。`vercel --prod` 必須。
5. **MDM系テスト6件は事前不具合**: dsp_engine と無関係。
6. **入札パスに外部 I/O を入れない**: #3 の設計原則。schain 検証・sellers.json 突合は
   入札パス内では純粋関数 + L1 キャッシュ参照のみ。HTTP fetch は `batch.py` のループで実施。

詳細な作業ログは `tasks/todo.md`、進捗管理表は `tasks/progress-dsp-engine.md`、
教訓は `tasks/lessons.md` を参照。
