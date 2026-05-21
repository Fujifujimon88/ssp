# 引き継ぎ: dsp_engine（広告主向けパフォーマンス DSP）

Status: Verified
最終更新: 2026-05-22

AppLovin / Moloco 型の ROAS 最適化 DSP を既存リポ内 `dsp_engine/` モジュールとして構築した
作業の引き継ぎ。設計の元計画は `~/.claude/plans/https-www-applovin-com-ja-https-www-molo-proud-snowflake.md`。

---

## 1. 概要

- **目的**: 広告主に「効果（売上 / ROAS）」を出す DSP。入札・予算ペーシング・クリック計測・
  購入CV計測・ROAS 集計・広告主ダッシュボードを持つ。
- **入札式**: `bid_cpm_jpy = pCTR × pCVR × 平均購入額 × (1 - margin) × 1000`、
  フロア/キャップでクランプ。実績50件未満はコールドスタート（広告主提供の想定値）、
  以降は実測の revenue/impression。
- **インベントリ**: 自社 SSP オークション（`main.py` の `auction_engine`）に
  `LocalDspEngineDSP` として参加。外部エクスチェンジからは受信側 OpenRTB 入札も受ける。
- **計測ループ**: 広告内リンク → `/dsp-engine/click`（クリック記録）→ 広告主LP（`dsp_ct` 付与）
  → 購入 → MMP/広告主が `/dsp-engine/conversion` へポストバック → click_token で紐付け → ROAS。

---

## 2. 進捗（完了済み・検証済み）

| フェーズ | 内容 | 状態 |
|---|---|---|
| Phase 1 | ROAS最適化 MVP（入札・ペーシング・CV計測・4画面・DB3テーブル） | 完了・本番稼働 |
| Phase 2 | 外部エクスチェンジ連携（受信側OpenRTB入札・落札通知nurl・QPS・通貨レート） | 完了・本番稼働 |
| Phase 2.5 | クリック計測（dsp_click_events）・実MMP形式対応（AppsFlyer/Adjust正規化） | 完了・本番稼働 |
| Phase 2.5 | Codexレビュー指摘3件修正（実クリック数・クリック日集計・source明示） | 完了・本番稼働 |
| Phase 2.6 | アドテク技術レビュー High5件修正（認証・総予算・N+1・冪等・期間） | 完了・**未コミット** |
| デプロイ | `vercel --prod` で本番反映、マイグレーション適用確認 | Phase 2.5 まで本番反映済み |

注意: **Phase 2.6（直近のレビュー改善）はテスト検証済みだが未コミット・未デプロイ**。
コミット → master マージ → push → `vercel --prod` が次の手順。

---

## 3. 主要ファイル

```
dsp_engine/
  bidder.py          入札ロジック / LocalDspEngineDSP / record_dsp_win（落札記録・冪等）
  scoring.py         入札CPM算出（コールドスタート / 実績ベース）
  pacing.py          予算ペーシング（日予算 smooth pacing + 総予算チェック）
  campaign_manager.py キャンペーンCRUD / get_all_campaign_stats（一括集計）
  attribution.py     購入CV受信 / record_click / normalize_conversion_payload / ROAS算出
  exchange.py        外部エクスチェンジ識別・QPS制御・認証(verify_exchange_secret)・統計
  reporting.py       多次元レポート（消化/クリック/CVを各イベント日で集計しマージ）
  currency.py        円/ドルレート（settings.jpy_per_usd 駆動・動的更新可）
  supply.py          SSP連携接続のCRUD / 外部IDマッピング
  router.py          全エンドポイント
  templates/         advertiser_dashboard / campaigns / ssp_integration / report
```

主な既存ファイル変更: `db_models.py`（DSPテーブル定義）、`main.py`（auction登録・/v1/bid落札フック・
`SKIP_LIFESPAN_ALEMBIC` ガード）、`config.py`（jpy_per_usd）、`auction/openrtb.py`（Bid.nurl）。

### DBテーブル（マイグレーション dspengine0001〜0003）
- `dsp_campaigns` — 広告主キャンペーン（予算・入札パラメータ・インラインクリエイティブ）
- `dsp_spend_logs` — 落札（消化）ログ。click_token unique
- `dsp_click_events` — クリックイベント（実クリック数・clicked_at基準集計）
- `dsp_conversion_events` — 購入CV（dedup_key で冪等）
- `dsp_configs`（既存拡張）— SSP連携。platform_mapping / qps_limit / api_secret 等

### 主要エンドポイント
- `POST /v1/bid` — SSPヘッダービディング。dsp-engine が参加
- `POST /dsp-engine/exchange/{name}/bid` — 外部エクスチェンジ受信側入札（X-DSP-Secret認証）
- `GET /dsp-engine/win` — 落札通知（nurl）
- `GET /dsp-engine/click` — クリック計測トラッカー（記録→LPへ302）
- `GET|POST /dsp-engine/conversion` — 購入CVポストバック受信
- `GET /dsp-engine/advertiser/{login,dashboard,api/stats}` — 広告主向け（cookie認証）
- `GET /dsp-engine/admin/{campaigns,supply,report}` — 運用者向け（IP制限）

---

## 4. 検証状況（Verified の根拠）

- ユニット: `pytest tests/test_dsp_engine.py` → **38 passed**（TDD: 各機能で失敗テスト先行）
- 全体: `pytest` → **190 passed**, 6 failed, 1 skipped。
  6 failed は `test_android_mdm.py` / `test_mdm_profile_resilience.py` で、**コミット 3e2ef82 (HEAD) 時点でも同一に失敗する事前不具合**（worktree で確認済み）。dsp_engine と無関係。
- E2Eスモーク: `python tests/_smoke_dsp_engine.py` → 全項目 PASS（入札→クリック→CV→レポート→外部エクスチェンジ）
- マイグレーション: `dspengine0001〜0003` を populated DB コピーで適用確認（冪等・inspector ガード）
- 本番: `https://ssp-platform.vercel.app/health` が `dsps` に `dsp-engine` を含み、
  `/dsp-engine/advertiser/login` が 200、DB照会プローブが 400（テーブル存在＝マイグレーション適用済）。
  ※ 本番反映は Phase 2.5 まで。Phase 2.6 は未デプロイ。

---

## 5. 開発・運用手順

### ローカル起動
```
python tests/_local_demo_setup.py                          # ssp_local.db 構築 + デモデータ投入
DATABASE_URL="sqlite+aiosqlite:///./ssp_local.db" python -m alembic stamp <head>
DATABASE_URL="sqlite+aiosqlite:///./ssp_local.db" APP_ENV=development \
  ADMIN_ALLOWED_IPS=127.0.0.1 SKIP_LIFESPAN_ALEMBIC=1 \
  python -m uvicorn main:app --host 127.0.0.1 --port 8000
```
広告主ログイン: demo / demo1234。管理画面は IP 127.0.0.1 許可で閲覧可。

### 本番デプロイ
- このプロジェクトは **Vercel と Git 未連携**。`git push` では本番デプロイされない。
- 本番反映は `vercel --prod` の手動実行のみ。起動時 lifespan が本番Postgresへ
  `alembic upgrade head` を自動実行（教訓13）。
- 本番URL: `https://ssp-platform.vercel.app`

---

## 6. 今後の改善内容

詳細な優先タスク表・状態管理は **`tasks/progress-dsp-engine.md`（進捗管理表）** に集約。
ここでは要点のみ記す。

優先順位（progress-dsp-engine.md の優先タスク表より）:

1. OpenRTB 2.6 相当へ拡張（app / schain / gpp / eids / burl・lurl / PMP・deal / CTV・video）— 高
2. first-price auction 対応（bid shading・floor 最適化を含む）— 高
3. schain / ads.txt / app-ads.txt / sellers.json 検証 — 高
4. 入札ログ完全化 + 予算 TOCTOU 対策（no-bid 理由コード `nbr` を含む）— 高
5. pCTR / pCVR / value / win-rate のベースライン ML（device 特徴量・WARM_THRESHOLD 設定化を含む）— 中
6. creative / publisher / app / placement 別レポート（geo・device・deal_id 軸も）— 中
7. A/B テスト・holdout 基盤（複数クリエイティブ 1:N 化が前提）— 中
8. fraud / IVT / brand safety 監視（クリック連打レート制限を含む）— 中
9. MMP 署名検証・SKAN・Privacy Sandbox 対応（PII サニタイズ・アトリビューション窓を含む）— 中
10. データ基盤・運用堅牢化（複合インデックス・管理画面 N+1 解消・QPS カウンタ Redis 化）— 中〜低

運用ガード: 本番 RTB bidder は低遅延・高 QPS。生成コードは必ず benchmark / load test を
通す。PII・広告 ID・CV データをプロンプトへ不用意に渡さない。Codex / Claude Code は
本番入札 ML そのものではなく、実装・テスト・レビュー補助に使う。

ビジネス側（コード外）:
- 実広告主 1〜2 社のオンボーディング（LP・購入計測連携）
- 外部エクスチェンジの実提携・QPS 審査（契約マター）
- 本番での初回 DSPキャンペーン登録（未登録のため本番は現状 inert）

---

## 7. 既知の制約・注意点（次セッションへの申し送り）

1. **Phase 2.6 未コミット**: 直近のレビュー改善5件 + マイグレーション dspengine0003 +
   テストが未コミット。`git status` で確認し、コミット→master マージ→push→`vercel --prod`。
2. **lifespan の Alembic がローカルSQLiteでデッドロック**: aiosqlite 環境固有の既存バグ。
   ローカル起動時は `SKIP_LIFESPAN_ALEMBIC=1` を付ける（本番Vercelでは未設定＝従来動作）。
3. **Alembic チェーンは fresh SQLite で通らない**: 古い `ALTER TABLE ADD COLUMN` 系が
   テーブル存在前提。マイグレーション検証は「populated DB のコピー + stamp + upgrade」で行う。
   本番 Postgres には read-only の `alembic current` のみ（`upgrade` は許可必須）。
4. **Vercel は Git 未連携**: `git push` では本番デプロイされない。`vercel --prod` 必須。
5. **MDM系テスト6件は事前不具合**: dsp_engine と無関係。誤って「壊した」と判断しないこと。
6. **計測の到達点**: 配線・内部ロジックは実MMP形式まで対応しテスト済み。ただし実MMP往復・
   アトリビューション窓・iOS SKAdNetwork は未対応（手順書6章に明記）。

詳細な作業ログは `tasks/todo.md` の dsp_engine 各 Phase セクション、教訓は `tasks/lessons.md`
（15・16）を参照。
