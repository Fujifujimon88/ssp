# 進捗管理表: dsp_engine（広告主向けパフォーマンス DSP）

最終更新: 2026-05-22
関連: `tasks/handoff-dsp-engine.md`（引き継ぎ詳細） / `tasks/todo.md`（作業ログ） / `tasks/lessons.md`（教訓）

## 0. サマリー（3行）

- DSP MVP の骨格は実装済み。dsp-engine が自社 SSP オークションに参加し、キャンペーン管理・入札・クリック計測・CV ポストバック・ROAS/CPA/CTR 集計・外部 SSP OpenRTB 受信口まで稼働。
- DSP 関連テストは通過済み（dsp_engine 系 + supply_chain / sjcache / adstxt 等、2026-05-22 時点。全体 310 passed、6 failed は既知の MDM 系事前不具合）。
- #1〜#7 は完了・本番デプロイ済み。#8 は fraud / IVT / brand safety のコア部分を実装・master ローカルマージ済み（未 push・未デプロイ）。次フェーズは #8-2（エンドツーエンド配線）。優先タスク表（セクション3）参照。

## 1. 現状（実装済み・稼働中）

| 機能 | 状態 |
|---|---|
| DSP MVP 骨格 | 完了 |
| dsp-engine の SSP オークション参加（`LocalDspEngineDSP`） | 完了・本番稼働 |
| キャンペーン管理（CRUD・予算・入札パラメータ） | 完了・本番稼働 |
| 入札（固定 CTR/CVR + 実績 ROAS ベース） | 完了・本番稼働 |
| クリック計測（`dsp_click_events`・実クリック数集計） | 完了・本番稼働 |
| CV ポストバック受信（MMP 形式正規化・冪等） | 完了・本番稼働 |
| ROAS / CPA / CTR 集計・広告主ダッシュボード | 完了・本番稼働 |
| 外部 SSP / エクスチェンジ OpenRTB 受信口（X-DSP-Secret 認証・QPS 制御） | 完了・本番稼働 |
| Phase 2.6 レビュー改善5件 + マイグレーション dspengine0003 + テスト | 完了・**未コミット / 未デプロイ** |

リリース状況: 優先 #1〜#7 を 2026-05-22 に本番デプロイ済み（`vercel --prod`、deployment `dpl_5Jiw83Hy4jiNQJ4s7y8gbD4VrvrA`、マイグレーション dspengine0003〜0009 適用済み、`/health` 200 で稼働確認）。次の変更も本番反映は `git push` → `vercel --prod` 手動実行。

## 2. 重要な不足

| # | 領域 | 不足内容 |
|---|---|---|
| 1 | OpenRTB | 2.5 相当の最小実装。`app` / `source.ext.schain` / `regs.gpp` / `user.ext.eids` / `burl`・`lurl` / PMP・deal / CTV・video 詳細が不足 |
| 2 | オークション | second-price 前提。first-price 対応・bid shading・floor 最適化が必要 |
| 3 | 入札ロジック | ML 未使用（固定 CTR/CVR + 実績 ROAS）。pCTR・pCVR・LTV・win-rate・fraud/viewability risk が未実装 |
| 4 | サプライチェーン検証 | ads.txt / app-ads.txt / sellers.json / schain の検証が弱い |
| 5 | MMP 連携 | 最小限。署名検証・lookback window・view-through attribution・SKAN 未実装 |
| 6 | レポート粒度 | day/campaign/source/platform 中心。creative・publisher/app/domain・placement・geo・device・deal_id が不足 |
| 7 | 実験・監視 | A/B テスト・holdout・incrementality・model monitoring が未実装 |

## 3. 優先タスク表

優先度: 高 / 中 / 低。状態: 未着手 / 進行中 / 完了。出典: Fuji = 今回指示の優先順位、handoff = 既存引き継ぎの残項目。

| # | タスク | 優先度 | 状態 | 出典 | 関連ファイル | 備考 |
|---|---|---|---|---|---|---|
| 1 | OpenRTB 2.6 相当へ拡張 | 高 | 完了 | Fuji | `auction/openrtb.py`, `tests/test_openrtb_26.py` | app / schain / gpp / eids / burl・lurl / PMP・deal / video 詳細 / Device 拡張。スキーマ拡張済み（2026-05-22）。検証・活用ロジックは #3/#4/#5 |
| 2 | first-price auction 対応 + bid shading | 高 | 完了 | Fuji | `auction/engine.py`, `dsp_engine/shading.py`, `dsp_engine/bidder.py` | `BidRequest.at` で first/second 決済切替 + P50 分位点 bid shading。完了（2026-05-22）。動的フロア最適化は #11 へ分離 |
| 3 | サプライチェーン検証（schain / sellers.json / ads.txt） | 高 | 完了 | Fuji + handoff #14 | `dsp_engine/supply_chain.py`, `sjcache.py`, `adstxt.py`, `batch.py` | schain 構造検証（入札パス内）+ sellers.json 突合 + ads.txt/app-ads.txt 検証 + 自社 sellers.json INTERMEDIARY 修正。完了（2026-05-22）|
| 4 | 入札ログ完全化 + 予算 TOCTOU 対策 | 高 | 完了 | Fuji + handoff #4・#8 | `dsp_engine/bidder.py`, `dsp_engine/router.py`, `dsp_engine/pacing.py`, `dsp_engine/nbr.py` | no-bid 理由コード `nbr`（拡張 500番台）付き入札ログ `dsp_bid_logs` + Redis nbr 集計。record_dsp_win で総予算超過を検知し `budget_exhausted` 自動切替。完了（2026-05-22）|
| 5 | pCTR / pCVR / value / win-rate のベースライン ML | 中 | 完了 | Fuji + handoff #7・#13 | `dsp_engine/scoring.py`, `dsp_engine/segments.py` | pCTR×pCVR×value を経験ベイズ shrinkage 推定（cliff 廃止）。WARM_THRESHOLD を config 化。device(platform) セグメント乗数を定期バッチ事前計算。win-rate は可視化のみ。完了（2026-05-22）|
| 6 | creative / publisher / app / placement 別レポート | 中 | 完了 | Fuji | `dsp_engine/reporting.py` | geo・device・deal_id 軸も追加。完了（2026-05-22、別セッション）|
| 7 | A/B テスト・holdout 基盤 | 中 | 完了 | Fuji + handoff #16 | `db_models.py`, `dsp_engine/bidder.py`, `reporting.py`, `router.py` | DspCreativeDB で 1:N 化 + weight 振り分け / DspAbExperimentDB / holdout / `bid.crid` 是正。完了（2026-05-22）|
| 8 | fraud / IVT / brand safety 監視（コア）| 中 | 完了（コア・master ローカル）| Fuji + handoff #9 | `dsp_engine/fraud.py`(新規), `bidder.py`, `nbr.py`, `attribution.py`, `db_models.py`, `config.py`, migration | `fraud.py` 4関数 + NBR 506・507 + DspCampaignDB の bcat_block・badv_block + migration dspengine0010 + bidder.py の IVT・brand safety no-bid 統合。test 102 passed。未 push・未デプロイ |
| 8-2 | #8 エンドツーエンド配線 | 中 | 未着手（次着手）| 本セッション分割 | `router.py`, `batch.py`, `fraud.py`, `bidder.py` | router.py の /click・/conversion でレート制限/IVT を実呼び出し / `check_click_rate_limit` の実 Redis カウンタ（現状 no-op stub）/ batch.py の IVT・brand safety L1 キャッシュループ / Reviewer LOW-2 是正。**機能A レート制限は #8-2 完了まで本番未稼働** |
| 9 | MMP 署名検証・SKAN・Privacy Sandbox 対応 | 中 | 未着手 | Fuji + handoff #10・#17 | `dsp_engine/router.py`, `dsp_engine/attribution.py` | raw_payload の PII サニタイズ、アトリビューション窓（計測ウィンドウ）を含む |
| 10 | データ基盤・運用堅牢化 | 中〜低 | 未着手 | handoff #11・#12・#15 | `db_models.py`, `dsp_engine/router.py`, `dsp_engine/exchange.py` | 複合インデックス追加、管理画面 N+1 解消、QPS カウンタの Redis 化（マルチプロセス対応） |
| 11 | 動的フロア最適化 | 中 | 未着手 | #2 から分離 | `main.py`, `auction/engine.py`, （新テーブル） | 落札率・bid density・過去 clearing_price の分位点ベースで動的にフロアを調整。floor_price_history テーブル + 更新バッチ。#2 のスコープから分離 |

ビジネス側（コード外）: 実広告主 1〜2 社のオンボーディング / 外部エクスチェンジの実提携・QPS 審査 / 本番初回 DSP キャンペーン登録（未登録のため本番は現状 inert）。

## 4. 注意点（運用ガード）

- Codex / Claude Code は本番入札 ML そのものではなく、実装・テスト・レビュー補助として使う。
- 本番 RTB bidder は低遅延・高 QPS。生成コードは必ず benchmark / load test を通す。
- PII・広告 ID・CV データをプロンプトに不用意に渡さない。

## 5. 更新履歴

| 日付 | 内容 |
|---|---|
| 2026-05-22 | 進捗管理表を新規作成。現状・重要な不足7点・優先タスク10項目を整理。 |
| 2026-05-22 | #1 OpenRTB 2.6 スキーマ拡張を完了。`auction/openrtb.py` を 2.6 相当へ拡張（App/Source/Regs/Pmp/Deal/eids/burl・lurl/Video 詳細/Device 拡張）。`tests/test_openrtb_26.py` 13件 PASS、既存 38件非破壊。 |
| 2026-05-22 | #2 first-price auction 対応 + bid shading を完了。`auction/engine.py` を `BidRequest.at` で first/second 決済切替、`dsp_engine/shading.py` 新規（P50 分位点 bid shading）、`bidder.py` 統合。テスト 14件追加（auction 5 / shading 7 / dsp_engine 2）全 PASS、既存非破壊。動的フロア最適化は #11 へ分離。 |
| 2026-05-22 | #3 サプライチェーン検証（フルスコープ）を完了。Phase A schain 構造検証（入札パス内）/ B sellers.json 突合（TTL キャッシュ + バッチ）/ C ads.txt・app-ads.txt 検証 / D 自社 sellers.json INTERMEDIARY 修正。新規モジュール supply_chain / sjcache / batch / adstxt + マイグレーション dspengine0004。テスト 29件追加・全 PASS（全体 246 passed）。入札パスに外部 fetch を入れない設計。 |
| 2026-05-22 | #4 入札ログ完全化 + 予算 TOCTOU 対策を完了。新規 `nbr.py`（no-bid 理由コード・拡張 500番台）/ `DspBidLogDB`（dsp_bid_logs）+ マイグレーション dspengine0005。`handle_bid_request` 全分岐で判定ログ（DB 全行 + Redis nbr 集計）。`record_dsp_win` で総予算超過を検知し `budget_exhausted` 自動切替（TOCTOU 抑止）。`pacing.record_spend` の INCRBYFLOAT+EXPIRE を Lua で原子化。admin `GET /dsp-engine/admin/bid-logs/api` 追加。テスト 12件追加・全 PASS（全体 258 passed、6 failed は既知 MDM 事前不具合）。 |
| 2026-05-22 | #6 多次元レポート拡張を完了（別セッション）。creative/publisher/app/placement/geo/deal_id の 6 軸を 3 イベントテーブルへ非正規化記録。マイグレーション dspengine0007〜0008。|
| 2026-05-22 | #7 A/B テスト・holdout 基盤を完了。`DspCreativeDB`（クリエイティブ 1:N・weight 振り分け）/ `DspAbExperimentDB`（実験管理）/ `campaign.holdout_rate`（NBR_HOLDOUT=505）/ `bid.crid` 是正（click_token を `bid.ext` で運搬）/ `run_ab_experiment_report` / admin クリエイティブ・実験エンドポイント。マイグレーション dspengine0009。テスト 17 件追加・全 PASS（全体 310 passed）。レビュー HIGH 指摘（run_report の campaign_id フィルタ）も対応。|
| 2026-05-22 | #8 fraud / IVT / brand safety 監視の**コア**を完了（test-first-implement パイプライン、Reviewer 判定 Approve）。新規 `dsp_engine/fraud.py`（`check_click_rate_limit` / `validate_revenue` / `is_ivt` / `is_brand_safety_blocked`）/ NBR 506・507 / `DspCampaignDB` に `bcat_block`・`badv_block` / migration dspengine0010（カラム追加のみ・本番未適用）/ `bidder.py` の IVT・brand safety no-bid 統合。`record_click` に `rate_limited` 引数追加。test_dsp_fraud.py 17件 + 既存 dsp 85件 = 102 passed。レビュー LOW-1（bcat prefix 兄弟カテゴリ誤ブロック）は修正済み。master へ ff-merge 済み（HEAD `f77bbd7`）だが**未 push・未デプロイ**。router.py 配線・実 Redis・batch.py ループ・LOW-2 是正は #8-2 へ繰り越し。 |
| 2026-05-22 | #5 ベースライン ML を完了。`scoring.py` を pCTR×pCVR×value の経験ベイズ shrinkage 推定に刷新（実績 50 件硬切替の cliff を廃止）。`WARM_THRESHOLD` を `config.warm_threshold`（prior strength）へ設定化。新規 `segments.py` + `DspSegmentPerfDB`（dspengine0006）で device(platform) 別 CTR 乗数を定期バッチ事前計算し、入札時は L1 キャッシュ参照のみで pCTR 補正。win-rate は `get_campaign_win_rates` で算出し admin/bid-logs/api に追加（入札へは非反映）。テスト 13件追加・全 PASS（全体 271 passed、6 failed は既知 MDM 事前不具合）。 |
