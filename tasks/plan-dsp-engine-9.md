# plan: dsp_engine #9 — MMP 署名検証 / PII サニタイズ / アトリビューション窓

Status: 計画確定（2026-05-22 調査完了。Fuji スコープ承認: A+B+C、D/E は #9-2 繰り越し）

## 3行サマリー
- A MMP 署名検証 / B raw_payload の PII サニタイズ / C アトリビューション窓（lookback window）の3部。
- migration なし（config 追加 + router.py / attribution.py のロジック変更のみ）。
- D SKAN / E Privacy Sandbox は #9-2 へ繰り越し。test-first-implement（Red-first）で実装。

## スコープ（Fuji 承認: A+B+C）
| 記号 | 内容 | 対象 |
|---|---|---|
| A | MMP ポストバック署名検証。静的 `asp_postback_secret` 比較を `hmac.compare_digest`（timing-safe）化。加えて汎用 HMAC-SHA256 署名検証経路を追加（`signature` パラメータ = HMAC-SHA256(secret, canonical payload)。canonical は click_token + revenue + dedup_key 等の決定的連結。win notice と同パターン）| router.py, attribution.py, config.py |
| B | `raw_payload` 保存前の PII サニタイズ。IDFA/GAID/device_id/ip/ua/android_id 等の PII キーを除去してから保存。除外キーリストは config 設定化 | attribution.py（サニタイズ関数）, router.py |
| C | アトリビューション窓。`record_conversion` で click_token→spend_log 解決時に `spend_log.logged_at` と現在時刻の差を判定。窓外なら spend_log 由来の campaign_id/impression_id 紐付けをスキップ（CV 自体は記録＝未アトリビュート扱い）| attribution.py, config.py |

## 設計判断
- A: AppsFlyer `X-AppsFlyer-Signature` / Adjust `sign_version` 等のベンダー固有スキームは実テストベクタが無く再現不能。本番は実 MMP 未連携のため、DSP 定義の canonical 文字列に対する汎用 HMAC-SHA256 検証を採用。ベンダー固有対応は実連携が決まった時点で別途。
- A: `asp_postback_secret` / 新規 HMAC secret とも空文字なら従来通り検証スキップ（後方互換）。署名パラメータが付いていれば検証、無ければ静的シークレット経路にフォールバック。
- C: 窓外 CV 用の新カラム（`attributed` boolean 等）は追加しない（migration なし）。窓外は「campaign 未紐付けで記録」＝ROAS に算入されない形で表現。
- B: PII はハッシュ化せず除去（dict からキー削除）。

## 変更ファイル
- `config.py` — `dsp_postback_hmac_secret`（既定空）/ `dsp_attribution_window_days`（既定30）/ `dsp_pii_strip_keys`（既定: idfa,gaid,device_id,ip,user_agent,ua,android_id,appsflyer_id 等カンマ区切り）
- `dsp_engine/attribution.py` — 署名検証ヘルパー / PII サニタイズ関数 / `record_conversion` に窓判定
- `dsp_engine/router.py` — /conversion で署名検証分岐・サニタイズ後の raw_payload 保存

## Red テスト（tests/test_dsp_attribution_privacy.py 新規・~10-12件）
- A: 正しい HMAC 署名で 200 / 不正署名で 401 / 署名なし + 静的シークレット一致で 200 / 静的シークレット不一致で 401 / timing-safe 比較
- B: raw_payload から PII キーが除去される / 非 PII キーは残る / サニタイズ後も dedup・revenue 正規化が機能
- C: 窓内クリックの CV は campaign に紐付く / 窓外クリックの CV は記録されるが campaign 未紐付け（ROAS 非算入）/ 窓境界値

## ガード
- 既存 /conversion テスト（test_dsp_engine.py の normalize/idempotent/attribution 系）を壊さない。
- 後方互換: secret 系設定が空なら従来挙動を維持。
- 削除なし（既存カラム・関数・エンドポイントの削除禁止）。
- 入札パスは変更しない（#9 は CV ポストバック経路のみ）。
- 本番 push・デプロイは #9 完了後に別途 Fuji 判断。

## ステップ
1. Red: tests/test_dsp_attribution_privacy.py に失敗テスト先行・commit
2. Planner: Red から逆算し実装計画
3. Green: config.py / attribution.py / router.py 実装
4. Reviewer: diff レビュー + 削除 guard
5. Quality Gate（Fuji 承認）→ master へ ff-merge
6. handoff / progress / plan 更新
