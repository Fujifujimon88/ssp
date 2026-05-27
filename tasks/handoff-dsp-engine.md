# 引き継ぎ: dsp_engine（広告主向けパフォーマンス DSP）

Status: Verified
最終更新: 2026-05-28

## 3行サマリー
- AppLovin / Moloco 型の ROAS 最適化 DSP を既存リポ内 `dsp_engine/` モジュールとして構築。
- 優先タスク #1〜#10 + セキュリティ修正3件まで実装完了・push 済み (master `6720e0d`)。本番反映は `vercel --prod` の手動実行が必要。直前の本番 deployment は `dpl_BiBA4yh8dB5tyjRARZKzRkTkv1GP` (2026-05-23、#9 まで)。
- 最新は #10 データ基盤・運用堅牢化 3 Phase (複合インデックス 5 本 + migration `dspengine0012` / 管理画面 N+1 解消 / QPS Redis 化 + bidder.py 教訓21違反修正)。残タスクは #9-2 (SKAN・Privacy Sandbox) / #11 (動的フロア最適化) + ビジネス側。詳細は本書セクション6。

進捗管理表は `tasks/progress-dsp-engine.md`、作業ログは `tasks/todo.md`、教訓は `tasks/lessons.md`。

---

## 1. 概要

- **目的**: 広告主に「効果（売上 / ROAS）」を出す DSP。入札・予算ペーシング・クリック計測・
  購入CV計測・ROAS 集計・広告主ダッシュボードを持つ。
- **入札式**: `bid_cpm_jpy = pCTR × pCVR × value × (1 - margin) × 1000`、フロア/キャップでクランプ。
  pCTR/pCVR/value は観測実績と campaign prior を経験ベイズ shrinkage でブレンド（#5。実績ゼロ＝prior）。
- **インベントリ**: 自社 SSP オークション（`main.py` の `auction_engine`）に
  `LocalDspEngineDSP` として参加。外部エクスチェンジからは受信側 OpenRTB 入札も受ける。
- **計測ループ**: 広告内リンク → `/dsp-engine/click` → 広告主LP（`dsp_ct` 付与）
  → 購入 → MMP/広告主が `/dsp-engine/conversion` へポストバック → click_token で紐付け → ROAS。

---

## 2. 全体進捗

| フェーズ | 内容 | 状態 |
|---|---|---|
| Phase 1〜2.6 | ROAS MVP / 外部エクスチェンジ連携 / クリック計測 / レビュー改善 | 完了・本番反映済み |
| 優先 #1 | OpenRTB 2.6 スキーマ拡張（App/Source/Regs/Pmp/Deal/eids/burl・lurl/Video・Device）| 完了・本番反映済み |
| 優先 #2 | first-price auction 対応 + bid shading（P50 分位点）| 完了・本番反映済み |
| 優先 #3 | サプライチェーン検証（schain 構造検証 / sellers.json 突合 / ads.txt・app-ads.txt / 自社 sellers.json）| 完了・本番反映済み |
| 優先 #4 | 入札ログ完全化（nbr 付き `dsp_bid_logs` + Redis 集計）+ 予算 TOCTOU 対策（総予算超過で `budget_exhausted` 自動切替）| 完了・本番反映済み |
| 優先 #5 | ベースライン ML（pCTR×pCVR×value の shrinkage 推定 / WARM_THRESHOLD 設定化 / device セグメント乗数バッチ / win-rate 可視化）| 完了・本番反映済み |
| 優先 #6 | 多次元レポート拡張（creative/publisher/app/placement/geo/deal_id の 6 軸を非正規化記録）| 完了・本番反映済み |
| 優先 #7 | A/B テスト・holdout 基盤（DspCreativeDB で 1:N 化 + weight 振り分け / DspAbExperimentDB / holdout / `bid.crid` 是正 / A/B レポート / admin 管理エンドポイント）| 完了・本番反映済み |
| 優先 #8 | fraud / IVT / brand safety 監視のコア（`fraud.py` / NBR 506・507 / DspCampaignDB に bcat_block・badv_block / migration dspengine0010 / bidder.py の IVT・brand safety no-bid 統合）| 完了・本番反映済み |
| 優先 #8-2 | fraud 監視のエンドツーエンド配線（router.py /click にレート制限配線・実 Redis カウンタ `incr_click_counters` / router.py /conversion に revenue ガード / bidder.py LOW-2 是正）| 完了・本番反映済み |
| 優先 #9 | MMP 署名検証（HMAC-SHA256 + timing-safe）/ PII サニタイズ（raw_payload）/ アトリビューション窓（`attributed` カラム + migration dspengine0011 で窓外 CV を ROAS 集計から除外）| 完了・本番反映済み |
| セキュリティ修正3件 | CV 売上付け替え防止（`record_conversion` で click_token→spend_log の campaign_id を無条件採用・不一致は warning）/ win notice 署名に `crid` を含める（改竄防止）/ daily pacing の DB フォールバック（`can_bid` が Redis 不在時 `daily_spend_jpy` の DB 実績で判定）。スキーマ変更なし | 完了・本番反映済み |
| 優先 #10 | データ基盤・運用堅牢化 3 Phase: 複合インデックス 5 本 + migration `dspengine0012` (Phase 1) / 管理画面 N+1 解消 `compute_roas_from_stats` (Phase 2) / QPS Redis 化 + bidder.py `_incr_nbr_counter` の教訓21違反修正 (Phase 3) | 完了・master 反映済み (本番デプロイは未) |

**本番デプロイ状況**: 優先 #1〜#9 + セキュリティ修正3件は **2026-05-23 に本番デプロイ済み**
（deployment `dpl_BiBA4yh8dB5tyjRARZKzRkTkv1GP`、`/health` 200）。**#10 は master `6720e0d` まで実装完了
だが本番未反映** — `vercel --prod` の手動実行が必要。マイグレーション `dspengine0012`
(複合インデックス 5 本) は起動時 lifespan の `alembic upgrade head` で本番 Postgres へ自動適用される。
QPS Redis 化はフォールバック付き実装のため本番 Redis 未接続でも稼働するが、実効化には Upstash 等の接続が別途必要。
Vercel は Git 未連携のため、次回以降の本番反映も `git push` → `vercel --prod` の手動実行が必要。

**本番 Redis 未接続の注意**: `/health` の `redis:false`。#8-2 のクリック連打レート制限
（`incr_click_counters`）と #4 の QPS カウンタは Redis 不在時メモリ/フォールバック動作になる。
レート制限を実効化するには本番 Redis（Upstash 等）の接続が必要。

**本番は現状 inert**: DSP キャンペーンが未登録のため、稼働はしているが実入札は発生しない。

---

## 3. 主要ファイル

```
dsp_engine/
  bidder.py          入札ロジック / LocalDspEngineDSP / record_dsp_win（落札記録・冪等）
                     / 入札判定ログ / win-rate 集計 ★#4★#5
  scoring.py         入札CPM算出（shrinkage 推定: pCTR×pCVR×value）★#5
  shading.py         bid shading（first-price 時のみ・過去落札 P50 分位点）★#2
  pacing.py          予算ペーシング（日予算 smooth pacing + 総予算チェック / Lua 原子化）★#4
  campaign_manager.py キャンペーンCRUD / get_all_campaign_stats（一括集計）
  attribution.py     購入CV受信 / record_click / normalize_conversion_payload / ROAS算出
  exchange.py        外部エクスチェンジ識別・QPS制御・認証・統計
  supply_chain.py    schain 構造検証（入札パス内・純粋関数）★#3
  sjcache.py         sellers.json fetch・TTLキャッシュ・突合 ★#3
  adstxt.py          ads.txt / app-ads.txt パース・fetch・検証 ★#3
  batch.py           サプライチェーン定期検証バッチ（lifespan タスク）★#3
  nbr.py             no-bid 理由コード（nbr）定義・ラベル ★#4
  segments.py        device セグメント別CTR乗数バッチ + L1キャッシュ ★#5
  reporting.py       多次元レポート（#6 の 6 軸 + #7 run_ab_experiment_report）★#6★#7
  currency.py        円/ドルレート
  supply.py          SSP連携接続のCRUD / 外部IDマッピング / parse_allowed_asi_domains
  router.py          全エンドポイント
auction/
  openrtb.py         OpenRTB 2.6 相当スキーマ ★#1 / _compute_clearing_price ★#2
  engine.py          オークションエンジン（at で first/second 決済切替）★#2
```

主な既存ファイル変更: `db_models.py`、`main.py`（auction登録・lifespan・supply-chain /
segment バッチ起動）、`config.py`（jpy_per_usd / warm_threshold / ssp_domain 等）。

### DBテーブル（マイグレーション dspengine0001〜0009）
- `dsp_campaigns` / `dsp_spend_logs` / `dsp_click_events` / `dsp_conversion_events`
- `dsp_configs`（拡張）— SSP連携 + schain 検証カラム（dspengine0004）
- `dsp_bid_logs` — 入札判定ログ（nbr 付き全 bid request 記録。dspengine0005）★#4
- `dsp_segment_perf` — device セグメント別 CTR 乗数（dspengine0006）★#5
- 3 イベントテーブルへ多次元軸カラム（creative/publisher/app/placement/geo/deal_id）追加（dspengine0007〜0008）★#6
- `dsp_creatives` — クリエイティブ 1:N（weight 振り分け）/ `dsp_ab_experiments` — A/B 実験管理。
  `dsp_campaigns.holdout_rate` 追加（dspengine0009）★#7
- `dsp_campaigns.bcat_block` / `badv_block` 追加（dspengine0010）★#8
- `dsp_conversion_events.attributed` 追加（dspengine0011・窓外 CV を ROAS 集計から除外するフラグ）★#9

### 主要エンドポイント
- `POST /v1/bid` — SSPヘッダービディング（dsp-engine 参加）
- `POST /dsp-engine/exchange/{name}/bid` — 外部エクスチェンジ受信入札（schain 検証付き）
- `GET /dsp-engine/win` / `click` / `conversion` — 落札通知・クリック計測・CVポストバック
- `GET /dsp-engine/advertiser/*` — 広告主向け / `GET /dsp-engine/admin/*` — 運用者向け
- `GET /dsp-engine/admin/bid-logs/api` — 入札判定ログ + no-bid 理由(nbr)内訳 + win-rate ★#4★#5
- `GET /sellers.json`（SSP INTERMEDIARY 記載）/ `GET /api/publishers/me/{ads-txt,app-ads-txt}`

---

## 4. 検証状況（Verified の根拠）

- 全体: `pytest tests/` → **310 passed**, 6 failed, 1 skipped。
  6 failed は `test_android_mdm.py` / `test_mdm_profile_resilience.py` の**事前不具合**で
  dsp_engine と無関係（誤って「壊した」と判断しないこと）。
- dsp_engine 系テスト: test_dsp_engine 54 (Phase 1+2+3 で +4) / test_auction 14 / test_shading 7 /
  test_openrtb_26 13 / test_supply_chain 10 / test_sjcache 9 / test_adstxt 10 /
  test_dsp_bid_log 12 / test_ml_scoring 13。
- 各優先タスクは TDD（Red 先行）で実装。後方互換も明示テストで担保。
- マイグレーションは「populated DB のコピー + stamp + upgrade」で検証（教訓16）。
- 本番 `/health` 200・`/sellers.json` 200 で稼働確認済み。

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
- Vercel と Git 未連携。`git push` では本番反映されない。`vercel --prod` の手動実行が必須
  （`--yes` フラグは使わない＝確認ステップを残す）。
- 起動時 lifespan が本番 Postgres へ `alembic upgrade head` を自動実行。
- 本番URL: `https://ssp-platform.vercel.app`。デプロイ後は `/health` 200 を確認。

---

## 6. 次やること（残タスク・優先順）

**完了済み（#1〜#10）**: OpenRTB 2.6 拡張 / first-price + bid shading / サプライチェーン検証 /
入札ログ + TOCTOU 対策 / ベースライン ML / 多次元レポート / A/B テスト・holdout 基盤 /
fraud・IVT・brand safety 監視（#8 + #8-2 配線）/ MMP 署名検証・PII サニタイズ・
アトリビューション窓（#9）/ データ基盤・運用堅牢化（#10）。すべて test-first-implement
パイプライン（最終 Reviewer Approve）で実装。**dsp 系テスト 54 passed**（master `6720e0d`）。
#1〜#9 は本番デプロイ済み (2026-05-23)、**#10 は本番未反映** (`vercel --prod` 手動実行待ち)。

**残タスク（優先順）**:

| # | やること | 優先度 | 状態 | 関連ファイル |
|---|---|---|---|---|
| 11 | 動的フロア最適化（落札率・bid density ベース。#2 から分離）| 中 | 未着手 | `main.py`, `auction/engine.py`, 新テーブル |
| 9-2 | SKAN（SKAdNetwork ポストバック・Apple ECDSA 検証）/ Privacy Sandbox（Attribution Reporting・PAAPI）対応。#9 でスコープ外にした分。iOS 実入札・Web 枠展開が具体化してから着手 | 低 | 未着手 | `router.py`, `auction/openrtb.py`, 新テーブル |

**次セッションの着手対象 = #11**（動的フロア最適化）。#9-2（SKAN/Privacy Sandbox）は
DSP が iOS 実トラフィック・Web 枠を扱うまで実価値が薄く優先度低。
**インフラ申し送り**: 本番 Redis 未接続（`/health` redis:false）。#8-2 のクリック連打レート制限を
実効化するには本番 Redis 接続が必須（#10 の QPS Redis 化と併せて検討）。
`check_click_rate_limit` の `redis is not None` 分岐は #8 由来の dead code（#8-2 配線では常に
`redis=None` + `_override_*` で呼ぶ）。実害なし、整理は任意。

**ビジネス側（コード外）**: 実広告主 1〜2 社のオンボーディング / 外部エクスチェンジの実提携・
QPS 審査 / 本番初回 DSP キャンペーン登録（未登録のため本番は現状 inert）。

**小タスク**: device セグメントの細分化（現状 `platform` 粒度。devicetype/os/geo 別にするには
spend/click ログへの該当カラム記録追加が前提 → #6 または #10 と合わせて検討）。

---

## 7. 既知の制約・注意点（次セッションへの申し送り）

1. **#1〜#7 は本番デプロイ済み（2026-05-22）**: master HEAD `faf0777` は push 済み・
   deployment `dpl_5Jiw83Hy4jiNQJ4s7y8gbD4VrvrA`。次の変更も本番反映は `git push` →
   `vercel --prod` 手動（マイグレーションは起動時 lifespan の `alembic upgrade head` で自動適用）。
2. **本番 inert**: DSP キャンペーン未登録のため実入札は発生しない。実稼働はビジネス側
   オンボーディング後。
3. **lifespan の Alembic がローカルSQLiteでデッドロック**: ローカル起動時は
   `SKIP_LIFESPAN_ALEMBIC=1` を付ける（本番 Vercel では未設定＝従来動作）。
4. **Alembic チェーンは fresh SQLite で通らない**: マイグレーション検証は
   「populated DB のコピー + stamp + upgrade」で行う（教訓16）。新規 revision ID は
   `dspengineNNNN` 形式で衝突確認してから採番（教訓15）。
5. **MDM系テスト6件は事前不具合**: dsp_engine と無関係。回帰と誤認しないこと。
6. **入札パスに外部 I/O を入れない**: #3 で確立した設計原則。schain 検証・sellers.json 突合・
   device セグメント乗数は入札パス内では純粋関数 + L1 キャッシュ参照のみ。HTTP fetch や
   重い再計算は `batch.py` / `segments.py` のバックグラウンドループで実施。
7. **scoring は shrinkage 推定**: 実績を持つキャンペーンは観測値が prior をブレンドで
   引き寄せる。実績を seed したテストは入札 CPM がコールド式と一致しない（#5 で是正済み）。
8. **本番 RTB bidder は低遅延・高 QPS**: 生成コードは benchmark / load test を通す。
   PII・広告 ID・CV データをプロンプトへ渡さない。
