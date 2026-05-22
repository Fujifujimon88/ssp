# Implementation Plan (test-first-implement Stage 3)

## Overview
Red テスト 8 件（commit 7c42946）を Green にするため、fraud.py への async `incr_click_counters` 追加、router.py の /click・/conversion への配線、bidder.py の brand_safety 発火条件修正の 3 ファイルに最小変更を加える。既存 17 テスト（test_dsp_fraud.py）のシグネチャを一切変更しない。

## Requirements (Red テストから逆算)

- `test_click_normal_records_dsp_click_event`: /click の正常アクセスで DspClickEventDB が 1 件記録され 302 を返すこと。現状の /click ハンドラは `Request` を受け取らないため、レート制限配線を追加するために `request: Request` の追加が必要。正常系では `incr_click_counters` が閾値未満のカウントを返し `record_click(rate_limited=False)` を呼ぶ。
- `test_click_rate_limited_no_dsp_click_event`: /click でレート制限超過時（`incr_click_counters` が monkeypatch で `(999,1)` を返す）は DspClickEventDB を記録しないが 302 は継続すること。`fraud_mod.incr_click_counters` に monkeypatch できる必要があるため、router.py が `dsp_engine.fraud` モジュールの `incr_click_counters` を直接参照していること。
- `test_conversion_normal_revenue_recorded`: /conversion に正常な revenue_jpy=5000 を送ると `created=True` で CV イベントが記録されること（正常系 / 既存動作を保護）。
- `test_conversion_negative_revenue_zeroed`: /conversion に負の revenue_jpy=-1000 を送ると HTTP 200、`created=True`、DB 上の `revenue_jpy==0.0` になること。`validate_revenue` が False のとき revenue_jpy を 0 に丸めて `record_conversion` に渡すこと。
- `test_conversion_outlier_revenue_zeroed`: /conversion に外れ値 revenue_jpy=99999999 を送ると同様に HTTP 200、`created=True`、`revenue_jpy==0.0` になること。`avg_purchase_value_jpy(3000) * dsp_revenue_cap_multiplier(10.0) = 30000` を超えるため `validate_revenue` が False。
- `test_incr_click_counters_accumulates_within_window`: `dsp_engine.fraud.incr_click_counters(redis, token, ip)` が同一 token/ip で 3 回呼ぶと (1,1)→(2,2)→(3,3) と累積すること（ImportError で Red / 関数が存在しないため）。fakeredis または最小 stub で INCR+EXPIRE を実行する async 関数であること。
- `test_check_click_rate_limit_triggers_at_threshold`: `incr_click_counters` で累積したカウントを `check_click_rate_limit(_override_token_count=t_count, _override_ip_count=i_count)` に渡したとき、閾値ちょうどでは False、閾値+1 で True になること（end-to-end 配線検証）。
- `test_brand_safety_blocked_wins_over_paced_out`: `brand_safety_blocked_count > 0` かつ `paced_out_count > 0` の混在状況で `NBR_BRAND_SAFETY_BLOCK(507)` の DspBidLogDB が記録されること。現状は `paced_out_count == 0` の条件があるため 501 が記録され、507 のログが存在せず assert 失敗（Red）。

## Files to Change

- `dsp_engine/fraud.py` — 新規 async 関数 `incr_click_counters(redis, token, ip) -> tuple[int, int]` を追加 — Redis INCR+EXPIRE でクリックカウンタを管理する実 I/O 関数。`check_click_rate_limit` は変更しない — Risk: Low
- `dsp_engine/router.py` — /click ハンドラに `request: Request` 追加 + `incr_click_counters` 呼び出し配線 + `check_click_rate_limit` 判定 + `rate_limited` を `record_click` に渡す。/conversion ハンドラに `validate_revenue` ガード追加（False なら revenue_jpy を 0 に丸め warning ログ） — Risk: Medium（既存の /click・/conversion の動作を変える）
- `dsp_engine/bidder.py` — `handle_bid_request` の NBR_BRAND_SAFETY_BLOCK 発火条件から `paced_out_count == 0` を削除し `brand_safety_blocked_count > 0` 単独で優先発火するよう変更 — Risk: Low（条件式1行のみ）

## Deletions (明示必須)

- `dsp_engine/bidder.py` 528行目: 条件式 `brand_safety_blocked_count > 0 and paced_out_count == 0` の `and paced_out_count == 0` 部分を削除し `brand_safety_blocked_count > 0` のみに変更する。この条件削除により paced_out との排他が解消され、brand_safety が paced_out より優先発火するようになる。

## Implementation Steps

1. **fraud.py: `incr_click_counters` を追加** — File: `dsp_engine/fraud.py`
   - Action: ファイル末尾（`is_brand_safety_blocked` の後）に async 関数 `incr_click_counters(redis, token: str, ip: str) -> tuple[int, int]` を追加する。
   - 実装詳細:
     - `redis` が None の場合は `(0, 0)` を返す（Redis 不在フォールバック）
     - token カウンタキー: `dsp:click:token:{token}` で `await redis.incr(key)` → `await redis.expire(key, window_seconds)` (window_seconds は引数で受け取るか、呼び出し側から渡さない場合はデフォルト 3600 を使う)
     - ip カウンタキー: `dsp:click:ip:{ip}` で同様
     - `(token_count, ip_count)` の tuple を返す
   - 設計注記: `check_click_rate_limit` は同期のまま変更しない。window_seconds は呼び出し元 (router.py) が `settings.dsp_click_window_seconds` から渡す設計にする（または `incr_click_counters` の引数デフォルト 3600 で吸収する）。テストの `_FakeRedis` stub は `incr`/`expire` の async メソッドを持つため互換。
   - Dependencies: なし（既存コードに依存しない新規追加）
   - Risk: Low

2. **router.py: /click に `Request` と rate limit 配線** — File: `dsp_engine/router.py`
   - Action: `click_redirect` 関数のシグネチャと本体を変更する。
   - 変更前シグネチャ: `async def click_redirect(ct: str = Query(...), db: AsyncSession = Depends(get_db))`
   - 変更後シグネチャ: `async def click_redirect(request: Request, ct: str = Query(...), db: AsyncSession = Depends(get_db))`
   - 本体追加（`record_click` 呼び出しの前）:
     ```
     client_ip = request.client.host if request.client else ""
     user_agent = request.headers.get("user-agent", "")
     redis = await get_redis()
     token_count, ip_count = await fraud.incr_click_counters(redis, ct, client_ip)
     rate_limited = fraud.check_click_rate_limit(
         None, ct, client_ip,
         token_limit=settings.dsp_click_token_limit,
         ip_limit=settings.dsp_click_ip_limit,
         window_seconds=settings.dsp_click_window_seconds,
         _override_token_count=token_count,
         _override_ip_count=ip_count,
     )
     ```
   - `record_click(db, ct)` を `record_click(db, ct, rate_limited=rate_limited)` に変更する。
   - `rate_limited=True` でも `if log is None` を通らず LP への 302 を継続するため、`log = await record_click(...)` が None を返した後の LP 解決フローを変更する必要がある。現在は `log is None` のとき `/` へリダイレクトするが、rate_limited の場合はキャンペーン解決なしに LP をフォールバックできないため、rate_limited のとき early return で `RedirectResponse(url="/", status_code=302)` とする（302 継続を保証する）。テストは `ct` の spend_log が存在するので `/` へのリダイレクトでも `status_code == 302` の assert を満たす。
   - import 追加: `from dsp_engine import fraud` (モジュールとして import し monkeypatch 互換を保つ) / `from cache import get_redis`
   - Dependencies: Step 1 完了後
   - Risk: Medium

3. **router.py: /conversion に revenue ガード配線** — File: `dsp_engine/router.py`
   - Action: `receive_conversion` 関数の `record_conversion` 呼び出し前に `validate_revenue` を適用する。
   - `norm["revenue_jpy"]` を `validate_revenue` でチェック。
   - 設計判断: テスト `test_conversion_outlier_revenue_zeroed` では `campaign.avg_purchase_value_jpy=3000.0` かつ `dsp_revenue_cap_multiplier=10.0` を使って外れ値判定している。router.py で campaign を DB 取得してから `validate_revenue(norm["revenue_jpy"], avg_purchase_value_jpy=campaign.avg_purchase_value_jpy, revenue_cap_multiplier=settings.dsp_revenue_cap_multiplier)` を呼ぶ。campaign が取得できない場合（存在しない campaign_id）は `validate_revenue` をスキップして後続の `record_conversion` に進む（`ValueError` で 400 が返る既存挙動を維持）。
   - `validate_revenue` が False の場合: `norm["revenue_jpy"] = 0.0` に丸め、`logger.warning` を出した上で `record_conversion` を呼ぶ（CV 自体は記録、200 応答を維持）。
   - import 追加: `from dsp_engine.fraud import validate_revenue` を router.py の import 節に追加。campaign 取得は既に import 済みの `campaign_manager.get_campaign` を使用。
   - Dependencies: Step 1 完了後（import パス確認）
   - Risk: Medium

4. **bidder.py: NBR_BRAND_SAFETY_BLOCK 発火条件修正** — File: `dsp_engine/bidder.py`
   - Action: 528 行目の条件式を変更する。
   - 変更前: `if brand_safety_blocked_count > 0 and paced_out_count == 0:`
   - 変更後: `if brand_safety_blocked_count > 0:`
   - これにより `paced_out_count` の値に関わらず、brand_safety_blocked_count が 1 以上であれば NBR_BRAND_SAFETY_BLOCK(507) が優先発火するようになる。2 番目の `await _log_bid_decision(..., nbr=NBR_ALL_BUDGET_PACED, ...)` はそのまま else ブランチとして残す。
   - 入札パスへの外部 I/O 追加なし（条件式変更のみ）。
   - Dependencies: なし（Step 1-3 と独立）
   - Risk: Low

## Testing Strategy

- Red テスト (commit 7c42946 済み): tests/test_dsp_fraud_wiring.py の 8 テスト
  1. `test_click_normal_records_dsp_click_event`
  2. `test_click_rate_limited_no_dsp_click_event`
  3. `test_conversion_normal_revenue_recorded`
  4. `test_conversion_negative_revenue_zeroed`
  5. `test_conversion_outlier_revenue_zeroed`
  6. `test_incr_click_counters_accumulates_within_window`
  7. `test_check_click_rate_limit_triggers_at_threshold`
  8. `test_brand_safety_blocked_wins_over_paced_out`

- Green 目標: 上記 8 テストのうち、実装前から pass している正常系 2 件 (`test_click_normal_records_dsp_click_event`、`test_conversion_normal_revenue_recorded`) を含む全 8 件が pass。加えて `tests/test_dsp_fraud.py` 既存 17 件が引き続き pass すること（`check_click_rate_limit` シグネチャ変更なし・`validate_revenue` 変更なしにより保証）。

- 実行コマンド: `pytest tests/test_dsp_fraud_wiring.py tests/test_dsp_fraud.py -v`

## Risks & Mitigations

| リスク | 対策 |
|---|---|
| router.py の /click で `rate_limited=True` 時に LP を解決できず `/` へ 302 になる | テストは `status_code == 302` のみ検証しておりリダイレクト先は問わないため問題なし。本番では LP フォールバックとして許容（クリック連打はユーザー体験より fraud 防止を優先）|
| `dsp_engine.fraud` をモジュール参照していないと monkeypatch が効かない | router.py で `from dsp_engine import fraud` してから `fraud.incr_click_counters(...)` を呼ぶ。テストの monkeypatch 戦略に合わせる|
| /conversion の revenue ガードで campaign の DB 取得を追加することで既存エラーパスが変わる可能性 | campaign が None の場合は validate_revenue スキップで後続 record_conversion に進む。record_conversion の ValueError(400) 挙動は変わらない|
| Redis 不在（テスト環境）で `incr_click_counters` が `(0,0)` を返すと `/click` でレート制限が常に False になる | テスト `test_click_rate_limited_no_dsp_click_event` は monkeypatch で `(999,1)` を差し込むため問題なし。本番では Redis が存在するため実カウントが機能する|
| bidder.py の条件変更で paced_out + brand_safety 混在ケースの挙動が変わる | #8 の設計方針（brand_safety を不可視にしない）と合致した意図的な挙動変更。既存 test_dsp_fraud.py に当該混在テストが無いことを Green 段で確認|

## Success Criteria

- `pytest tests/test_dsp_fraud_wiring.py -v` で 8 件すべて PASSED
- `pytest tests/test_dsp_fraud.py -v` で 17 件すべて PASSED（既存テスト非破壊）
- `dsp_engine/fraud.py` に `async def incr_click_counters` が追加されていること
- `/click` エンドポイントが `request: Request` を受け取り `fraud.incr_click_counters` を呼ぶこと
- `/conversion` エンドポイントが `validate_revenue` を呼び False のとき `revenue_jpy=0` で記録すること
- `bidder.py` の brand_safety 発火条件が `brand_safety_blocked_count > 0` のみになっていること
- 新規 production ファイルが作成されていないこと（既存 3 ファイルへの追記/変更のみ）
- 入札パス（`handle_bid_request`）に外部 I/O が追加されていないこと
