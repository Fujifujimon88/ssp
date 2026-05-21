# 進捗管理表: dsp_engine（広告主向けパフォーマンス DSP）

最終更新: 2026-05-22
関連: `tasks/handoff-dsp-engine.md`（引き継ぎ詳細） / `tasks/todo.md`（作業ログ） / `tasks/lessons.md`（教訓）

## 0. サマリー（3行）

- DSP MVP の骨格は実装済み。dsp-engine が自社 SSP オークションに参加し、キャンペーン管理・入札・クリック計測・CV ポストバック・ROAS/CPA/CTR 集計・外部 SSP OpenRTB 受信口まで稼働。
- DSP 関連テストは通過済み（`pytest tests/test_dsp_engine.py` → 47 passed in 4.45s、2026-05-22 時点）。
- 次フェーズは「OpenRTB 2.6 相当への拡張」「first-price 対応」「サプライチェーン検証」が最優先。優先タスク表（セクション3）参照。

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

リリース状況: 本番反映は Phase 2.5 まで。Phase 2.6 はテスト検証済みだが未コミット・未デプロイ（`vercel --prod` は Git 未連携のため手動実行）。

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
| 1 | OpenRTB 2.6 相当へ拡張 | 高 | 未着手 | Fuji | `auction/openrtb.py`, `dsp_engine/bidder.py` | app / schain / gpp / eids / burl・lurl / PMP・deal / CTV・video |
| 2 | first-price auction 対応 | 高 | 未着手 | Fuji | `dsp_engine/bidder.py`, `dsp_engine/scoring.py` | bid shading・floor 最適化を含む |
| 3 | schain / ads.txt / app-ads.txt / sellers.json 検証 | 高 | 未着手 | Fuji + handoff #14 | `dsp_engine/exchange.py`, `dsp_engine/supply.py` | 外部エクスチェンジ接続時の SupplyChain 検証 |
| 4 | 入札ログ完全化 + 予算 TOCTOU 対策 | 高 | 未着手 | Fuji + handoff #4・#8 | `dsp_engine/bidder.py`, `dsp_engine/router.py`, `dsp_engine/pacing.py` | no-bid 理由コード `nbr`（フロア未達=300 等）。`can_bid`→`record_spend` を Redis Lua で原子化 |
| 5 | pCTR / pCVR / value / win-rate のベースライン ML | 中 | 未着手 | Fuji + handoff #7・#13 | `dsp_engine/scoring.py`, `mdm/ml/two_tower.py` | device 特徴量の入札反映、WARM_THRESHOLD(50件固定) の設定化を含む |
| 6 | creative / publisher / app / placement 別レポート | 中 | 未着手 | Fuji | `dsp_engine/reporting.py` | geo・device・deal_id 軸も追加 |
| 7 | A/B テスト・holdout 基盤 | 中 | 未着手 | Fuji + handoff #16 | （新規 `DspCreativeDB` 等） | 複数クリエイティブ 1:N 化が前提。`bid.crid` への click_token 流用も是正 |
| 8 | fraud / IVT / brand safety 監視 | 中 | 未着手 | Fuji + handoff #9 | `dsp_engine/attribution.py` | クリック連打レート制限（Redis カウンタ）を含む |
| 9 | MMP 署名検証・SKAN・Privacy Sandbox 対応 | 中 | 未着手 | Fuji + handoff #10・#17 | `dsp_engine/router.py`, `dsp_engine/attribution.py` | raw_payload の PII サニタイズ、アトリビューション窓（計測ウィンドウ）を含む |
| 10 | データ基盤・運用堅牢化 | 中〜低 | 未着手 | handoff #11・#12・#15 | `db_models.py`, `dsp_engine/router.py`, `dsp_engine/exchange.py` | 複合インデックス追加、管理画面 N+1 解消、QPS カウンタの Redis 化（マルチプロセス対応） |

ビジネス側（コード外）: 実広告主 1〜2 社のオンボーディング / 外部エクスチェンジの実提携・QPS 審査 / 本番初回 DSP キャンペーン登録（未登録のため本番は現状 inert）。

## 4. 注意点（運用ガード）

- Codex / Claude Code は本番入札 ML そのものではなく、実装・テスト・レビュー補助として使う。
- 本番 RTB bidder は低遅延・高 QPS。生成コードは必ず benchmark / load test を通す。
- PII・広告 ID・CV データをプロンプトに不用意に渡さない。

## 5. 更新履歴

| 日付 | 内容 |
|---|---|
| 2026-05-22 | 進捗管理表を新規作成。現状・重要な不足7点・優先タスク10項目を整理。 |
