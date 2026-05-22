# plan: dsp_engine #7 — A/B テスト・holdout 基盤（フルスコープ）

## 3行サマリー
- クリエイティブを 1:N 化（新 `DspCreativeDB`）+ `bid.crid` の click_token 流用を是正。
- holdout（impression_id ハッシュ・純粋関数）+ 専用 `DspAbExperimentDB` で実験を管理。
- Red-first TDD（失敗テスト15件先行）→ 実装 → migration 検証の順。

## スコープ（Fuji 承認: フル）
DspCreativeDB(1:N weight 振り分け) / bid.crid 是正 / holdout / dsp_ab_experiments テーブル
/ admin クリエイティブ管理エンドポイント / レポート `ab_group` 軸。

## 設計判断（採用案）
- weight 方式（整数の相対重み。N 素材に自然拡張）
- holdout = best_campaign 選定後に `campaign.holdout_rate` で判定（request id ハッシュ・
  純粋関数）。holdout no-bid は `NBR_HOLDOUT=505` で bid log に記録（観測可能化）。
- `Bid` に `ext` 欄が無いため新設。bid.crid = 実クリエイティブID に是正、click_token は
  `bid.ext.dsp_click_token` で運搬。`main.py` は `ext` 優先・`crid` フォールバック。
  外部エクスチェンジ経路は win notice URL に `crid` を追加して creative_id を伝達。
- レポートは汎用 `ab_group` 軸ではなく専用 `run_ab_experiment_report()`（既存 `creative`
  軸で variant 比較 + bid log から holdout 件数）。
- 新規 migration: `dspengine0009`（down_revision=`dspengine0008`、採番前に grep 衝突確認）

## ステップ
1. Red: `tests/test_dsp_ab_test.py` に失敗テスト15件を先行作成・checkpoint commit
2. db_models.py: `DspCreativeDB` + `DspAbExperimentDB` + `DspCampaignDB.holdout_rate`
3. alembic: `add_dsp_ab_test.py`（冪等・既存キャンペーンの creative backfill 込み）
4. campaign_manager.py: creative / experiment の CRUD
5. bidder.py: クリエイティブ weight 選択（純粋関数）+ holdout 判定 + crid 是正
6. router.py: admin クリエイティブ管理エンドポイント
7. main.py: click_token 取得を `bid.ext` 優先へ
8. reporting.py: `ab_group`（holdout / exposed）ディメンション追加
9. Green 検証: dsp 系テスト全 pass / migration を populated DB コピーで検証（教訓16）
10. handoff・progress・lessons・todo を更新

## 後方互換・ガード
- 既存 1:1 キャンペーンの creative インライン列は削除しない（migration で backfill）
- 入札パスに外部 I/O 禁止: creative は入札前一括取得→純粋関数で選択（N+1 回避）
- MDM の既存 `creatives`/`CreativeExperimentDB` には触らない（別系統）
- migration 検証は本番 Postgres へ upgrade しない（read-only `alembic current` のみ）

## レビュー結果（完了時）
- code-reviewer: Critical 0 / HIGH 1 / MEDIUM 3 / LOW 2。HIGH（run_ab_experiment_report が
  全キャンペーン集計後に Python フィルタ）は `run_report` に `campaign_id` フィルタを
  追加して対応（commit b7c0415）。
- MEDIUM 残（許容・既知の制約として handoff へ）: 外部 win_notice の `crid` は HMAC 署名外
  （改竄でレポート creative 軸のみ汚染、spend 影響なし）。`update_*` の `value is not None`
  パターンはフィールドの NULL クリア不可（#7 スコープ外・既存共通仕様）。
- 教訓19 を追加（migration の inspector は DDL 後に再取得）。

## 実装結果
完了。テスト 17 件追加・全 PASS（全体 310 passed、6 failed は既知 MDM 事前不具合）。
migration dspengine0009 を populated DB コピーで upgrade / 冪等 re-apply / downgrade 検証済み。
コミット: 35a54e0(Red) / d6911ff(Green) / b7c0415(review fix)。
