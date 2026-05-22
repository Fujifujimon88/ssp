# plan: dsp_engine #8-2 — fraud 監視のエンドツーエンド配線

Status: 完了（2026-05-22・master ローカル HEAD 17e8132・未 push・未デプロイ）。
A/B/C/D すべて実装・テスト済み。batch.py ループは不要と調査確定。詳細は handoff セクション6。

## 3行サマリー
- #8 で実装した fraud コア（純粋関数）を実エンドポイント・実 Redis に配線する。
- 3部構成: A /click レート制限配線 / B /conversion revenue ガード配線 / C 実 Redis カウンタ実装。+ Reviewer LOW-2 是正。
- batch.py の L1 ループは不要（調査で確定）。test-first-implement（Red-first TDD）で実装。

## スコープ
| 記号 | 内容 | 対象 |
|---|---|---|
| A | /click にレート制限を配線。`Request` から client IP / User-Agent を取得、`check_click_rate_limit` を呼び `rate_limited` を `record_click` へ。レート制限時も LP リダイレクト(302)は継続 | router.py |
| B | /conversion に revenue ガードを配線。`validate_revenue` が False なら `revenue_jpy` を 0 に丸めて `record_conversion`、warning ログを残す。CV 件数はカウント、200 応答を維持 | router.py |
| C | `check_click_rate_limit` の Redis 経路（現 no-op stub）を実装。`cache.get_redis()` で取得し INCR+EXPIRE で token/IP カウンタ。Redis 不在時はカウント省略（既存メモリフォールバック踏襲）| fraud.py |
| D | Reviewer LOW-2 是正: bidder.py の NBR 507 発火条件を `brand_safety_blocked_count > 0` で優先発火に変更（paced_out との排他をやめ、brand safety ブロックを不可視にしない）| bidder.py |

## 設計判断
- /conversion 異常 revenue（負値・外れ値）: Fuji 承認 = CV イベントは記録するが `revenue_jpy=0` に丸める（ROAS を汚さず CV 件数は残す）。
- Redis I/O は外部 I/O のためエンドポイント層（router.py）側で行い、`check_click_rate_limit` は純粋な判定関数に保つ案を優先。実 Redis カウンタの具体 API（async helper 分離 等）は Red/Planner ステージで確定。
- batch.py は変更なし（IVT/brand safety は外部 fetch 不要のため L1 更新ループ不要）。

## 変更ファイル
- `dsp_engine/router.py` — /click・/conversion の配線
- `dsp_engine/fraud.py` — `check_click_rate_limit` の実 Redis 実装
- `dsp_engine/bidder.py` — LOW-2 是正
- Red テスト — `tests/test_dsp_fraud.py` 追記または `tests/test_dsp_fraud_wiring.py` 新規

## Red テスト（~8-10件）
- /click: 正常クリックは記録 + 302 / レート制限超過時は DspClickEventDB 非記録 + 302 継続
- /conversion: 正常 revenue は記録 / 負値・外れ値は `revenue_jpy=0` で記録（CV 件数は残る）
- `check_click_rate_limit`: 実 Redis（fakeredis 等）で INCR がウィンドウ内累積 / 閾値超過で True
- bidder.py LOW-2: brand safety ブロック + paced_out 混在ケースで NBR 507 が記録される

## ガード
- 既存 17 テスト（test_dsp_fraud.py）を壊さない。`check_click_rate_limit` のシグネチャを変える場合は後方互換を保つかテストを併せて更新する。
- /click のレート制限時も必ず LP へ 302（ユーザー体験を壊さない）。
- 削除なし（既存関数・カラム・エンドポイントの削除禁止）。
- 入札パス（bidder.py）に外部 I/O を持ち込まない（教訓6）。
- 本番 push・デプロイ・migration dspengine0010 の本番適用は #8-2 完了後に別途 Fuji 判断。

## ステップ
1. Red: エンドポイント配線 + 実 Redis カウンタ + LOW-2 の失敗テストを先行 commit
2. Planner: Red テストから逆算し実装計画を作成
3. Green: router.py / fraud.py / bidder.py を実装
4. Reviewer: diff レビュー + 削除 guard + Red-first 順序検証
5. Quality Gate（Fuji 承認）→ master へ ff-merge
6. handoff / progress / plan 更新
