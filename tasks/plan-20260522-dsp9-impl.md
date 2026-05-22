# Implementation Plan (test-first-implement Stage 3)

## Overview
Red テスト 12 件 (commit db944c2) を Green にするため、`config.py` に 3 設定を追加し、`dsp_engine/attribution.py` に 2 ヘルパー関数を追加し、`dsp_engine/router.py` の `/conversion` ハンドラに署名分岐・サニタイズ・窓判定呼び出しを追加する。DB スキーマ変更・migration なし。入札パス (bidder.py) は変更しない。

## Requirements (Red テストから逆算)

### スコープ A: HMAC 署名検証

- **test_conversion_valid_hmac_signature_returns_200 (A-1)**: `dsp_postback_hmac_secret` が設定されており、`signature` パラメータが正しい HMAC-SHA256 (canonical=`{click_token}|{revenue_jpy}|{dedup_key}`) であれば 200 を返す。
- **test_conversion_invalid_hmac_signature_returns_401 (A-2)**: `dsp_postback_hmac_secret` が設定されており、`signature` が不正な場合は 401 を返す。現状は signature を無視するため 200 が返り Red。
- **test_conversion_no_signature_with_static_secret_match_returns_200 (A-3)**: `signature` パラメータなし + `asp_postback_secret` 一致 → 200 (後方互換)。
- **test_conversion_static_secret_mismatch_returns_401 (A-4)**: `asp_postback_secret` 不一致 → 401。
- **test_conversion_timing_safe_comparison_used (A-5)**: `dsp_engine.attribution.verify_postback_secret` が存在し `hmac.compare_digest` で timing-safe 比較。現状 ImportError で Red。

### スコープ B: PII サニタイズ

- **test_pii_keys_removed_from_raw_payload (B-1)**: `raw_payload` に保存されるデータから PII キー (idfa, gaid, device_id, ip, user_agent, android_id, appsflyer_id 等) が除去されている。現状 Red。
- **test_non_pii_keys_retained_in_raw_payload (B-2)**: 非 PII キー (event_type, revenue_jpy 等) は `raw_payload` に残る。
- **test_sanitize_pii_function_exists_and_works (B-3)**: `dsp_engine.attribution.sanitize_pii_payload(payload, pii_keys=None) -> dict` が存在し PII キーを除去・非 PII は残す。現状 ImportError で Red。
- **test_sanitize_does_not_break_dedup_and_revenue_normalization (B-4)**: `sanitize_pii_payload` 後に `normalize_conversion_payload` を適用してもレベニュー/dedup_key が正しく解決される。現状 ImportError で Red。

### スコープ C: アトリビューション窓

- **test_conversion_within_window_is_attributed_to_campaign (C-1)**: `logged_at` が `now - 5 days` の spend_log は窓内 (30日) → `campaign_id` / `click_token` が CV に紐付く。
- **test_conversion_outside_window_is_recorded_but_not_attributed (C-2)**: `logged_at` が `now - 45 days` の spend_log は窓外 → CV は 200 で記録されるが `impression_id` は None。現状 Red。
- **test_conversion_at_window_boundary_is_attributed (C-3)**: `logged_at` がちょうど `now - 30 days` は窓内扱い → `campaign_id` / `impression_id` が紐付く。境界は `>=` (窓内) として実装。

## Files to Change

- `config.py` — 3 フィールド追加 (`dsp_postback_hmac_secret`, `dsp_attribution_window_days`, `dsp_pii_strip_keys`) — `Settings` クラスに新設定を追加。テストの `_make_mock_settings` が参照するフィールド名と完全一致させる — Risk: Low
- `dsp_engine/attribution.py` — `verify_postback_secret` / `sanitize_pii_payload` の 2 関数を追加、`record_conversion` に窓判定ロジックを追加 — A-5 / B-3 / B-4 / C-2 の直接実装対象 — Risk: Medium
- `dsp_engine/router.py` — `/conversion` ハンドラに HMAC 署名分岐・静的シークレット timing-safe 化・PII サニタイズ呼び出しを追加 — A-1 / A-2 / B-1 の直接実装対象 — Risk: Medium

## Deletions (明示必須)

削除する関数・カラム・ファイルはなし。

ただし `dsp_engine/router.py` の `receive_conversion` ハンドラ内の既存の静的シークレット比較式 (line 112-114 相当) を新しい署名分岐ロジックに**置換**する。元の `if settings.asp_postback_secret: if str(params.get("secret","")) != ...: raise 401` の 3 行は削除されるが、後方互換 (静的シークレット経路) は新ロジック内で `verify_postback_secret` を用いて維持する。

## Implementation Steps

1. **config.py に 3 設定フィールドを追加** — File: `config.py` — `Settings` クラスの `asp_postback_secret` の直後に追記:
   ```
   dsp_postback_hmac_secret: str = ""
   dsp_attribution_window_days: int = 30
   dsp_pii_strip_keys: str = "idfa,gaid,device_id,ip,user_agent,ua,android_id,appsflyer_id"
   ```
   フィールド名はテストの `_make_mock_settings` の属性名と一字一句一致させること。 — Dependencies: なし

2. **attribution.py に `verify_postback_secret` を追加** — File: `dsp_engine/attribution.py` — 冒頭 import に `import hashlib` / `import hmac` を追加、`record_conversion` の前に:
   ```python
   def verify_postback_secret(provided: str, expected: str) -> bool:
       """静的シークレットの timing-safe 比較 (hmac.compare_digest 使用)。"""
       return hmac.compare_digest(provided, expected)
   ```
   — Dependencies: なし

3. **attribution.py に `sanitize_pii_payload` を追加** — File: `dsp_engine/attribution.py` — `verify_postback_secret` の直後に:
   ```python
   def sanitize_pii_payload(payload: dict, pii_keys: list[str] | None = None) -> dict:
       """PII キーを payload dict から除去して新しい dict を返す (元 dict は変更しない)。"""
       _DEFAULT_PII_KEYS = [
           "idfa", "gaid", "device_id", "ip", "user_agent", "ua",
           "android_id", "appsflyer_id",
       ]
       keys_to_strip = set(pii_keys if pii_keys is not None else _DEFAULT_PII_KEYS)
       return {k: v for k, v in payload.items() if k not in keys_to_strip}
   ```
   — Dependencies: Step 2

4. **attribution.py の `record_conversion` に窓判定を追加** — File: `dsp_engine/attribution.py` — `record_conversion` の signature に `window_days: int = 30` を追加、`spend_log` 取得直後の `if spend_log:` ブロックに窓判定を挿入:
   ```python
   if spend_log:
       cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
       log_dt = spend_log.logged_at
       if log_dt.tzinfo is None:
           log_dt = log_dt.replace(tzinfo=timezone.utc)
       if log_dt >= cutoff:  # 窓内 (境界値含む)
           campaign_id = campaign_id or spend_log.campaign_id
           impression_id = spend_log.impression_id
           ... (既存の多次元軸コピーはこのブロック内)
       else:
           spend_log = None  # 窓外: impression_id・多次元軸を紐付けない
   ```
   `timedelta` / `timezone` の import 確認。テスト C-2 が `impression_id is None` を検証、C-3 の境界 (ちょうど30日前) は `>=` で窓内。 — Dependencies: Step 1 (window_days はデフォルト値で動作するが router.py から渡す)

5. **router.py の `/conversion` 署名検証を置換 (A)** — File: `dsp_engine/router.py` — 冒頭 import に `import hashlib` / `import hmac` を追加 (重複確認)、`from dsp_engine.attribution import` に `verify_postback_secret` を追加。既存の静的シークレット比較 3 行を以下に置換:
   ```python
   if settings.dsp_postback_hmac_secret:
       sig = str(params.get("signature", ""))
       if sig:
           ct_sig = str(params.get("click_token", params.get("dsp_ct", "")))
           rev_sig = str(params.get("revenue_jpy", ""))
           dedup_sig = str(params.get("dedup_key", ""))
           canonical = f"{ct_sig}|{rev_sig}|{dedup_sig}"
           expected_sig = hmac.new(
               settings.dsp_postback_hmac_secret.encode("utf-8"),
               canonical.encode("utf-8"), hashlib.sha256,
           ).hexdigest()
           if not hmac.compare_digest(sig, expected_sig):
               raise HTTPException(status_code=401, detail="invalid hmac signature")
       else:
           raise HTTPException(status_code=401, detail="signature required")
   elif settings.asp_postback_secret:
       provided = str(params.get("secret", ""))
       if not verify_postback_secret(provided, settings.asp_postback_secret):
           raise HTTPException(status_code=401, detail="invalid secret")
   ```
   canonical 文字列はテストの署名生成式と完全一致させること (テストファイルを精読)。 — Dependencies: Step 1, Step 2

6. **router.py の `/conversion` に PII サニタイズを追加 (B)** — File: `dsp_engine/router.py` — `from dsp_engine.attribution import` に `sanitize_pii_payload` を追加。署名検証ブロック直後・`normalize_conversion_payload` 呼び出し前に:
   ```python
   pii_keys = [k.strip() for k in settings.dsp_pii_strip_keys.split(",") if k.strip()]
   sanitized_params = sanitize_pii_payload(params, pii_keys=pii_keys)
   norm = normalize_conversion_payload(sanitized_params)
   ```
   `record_conversion` の `raw_payload=str(params)[:2000]` を `raw_payload=str(sanitized_params)[:2000]` に変更。 — Dependencies: Step 1, Step 3

7. **router.py の `record_conversion` 呼び出しに `window_days` を渡す (C)** — File: `dsp_engine/router.py` — `record_conversion(...)` の呼び出しに `window_days=settings.dsp_attribution_window_days` を追加。 — Dependencies: Step 4, Step 5, Step 6

8. **attribution.py の多次元軸コピーの spend_log None 安全性確認** — File: `dsp_engine/attribution.py` — Step 4 で窓外時に `spend_log = None` とするため、多次元軸 (creative_id/publisher_id 等) のコピーが `if spend_log:` ブロック内にあることを確認。窓外で `spend_log=None` の場合は impression_id・多次元軸が初期値 None のまま (正しい動作)。追加変更不要。 — Dependencies: Step 4

## Testing Strategy

- Red テスト (commit db944c2 済み): `tests/test_dsp_attribution_privacy.py` の 12 テスト
  1. `test_conversion_valid_hmac_signature_returns_200`
  2. `test_conversion_invalid_hmac_signature_returns_401`
  3. `test_conversion_no_signature_with_static_secret_match_returns_200`
  4. `test_conversion_static_secret_mismatch_returns_401`
  5. `test_conversion_timing_safe_comparison_used`
  6. `test_pii_keys_removed_from_raw_payload`
  7. `test_non_pii_keys_retained_in_raw_payload`
  8. `test_sanitize_pii_function_exists_and_works`
  9. `test_sanitize_does_not_break_dedup_and_revenue_normalization`
  10. `test_conversion_within_window_is_attributed_to_campaign`
  11. `test_conversion_outside_window_is_recorded_but_not_attributed`
  12. `test_conversion_at_window_boundary_is_attributed`

- Green 目標: 12 テスト中 6 件 Red (A-2, A-5, B-1, B-3, B-4, C-2) が pass に転換 + 残り 6 件が pass 維持
- 後方互換: `tests/test_dsp_engine.py` の `test_conversion_*` / `test_normalize_*` が pass を維持
- 実行: `python -m pytest tests/test_dsp_attribution_privacy.py tests/test_dsp_engine.py -v`

## Risks & Mitigations

| リスク | 深刻度 | 緩和策 |
|---|---|---|
| router.py の HMAC canonical 文字列がテストの署名生成式と不一致 | High | テストファイルの署名生成ヘルパーを精読し canonical (`{click_token}\|{revenue}\|{dedup_key}`) と revenue の文字列化形式 (`str(float)`) を完全一致させる |
| `dsp_postback_hmac_secret` 設定時に `signature` なしの 401 挙動が既存テストを壊す | Medium | 既存 test_dsp_engine.py は `dsp_postback_hmac_secret=""` 相当なので HMAC 経路に入らない。新ロジックは hmac_secret 空なら静的経路 (従来挙動) |
| 窓判定での timezone-naive datetime | Medium | `if log_dt.tzinfo is None: log_dt = log_dt.replace(tzinfo=timezone.utc)` で対処 |
| `sanitize_pii_payload` が `params` を破壊的変更 | Low | 新 dict を返す実装で元 params は不変 |
| router.py の import エラーで test_dsp_engine.py が全滅 | Medium | `import hashlib`/`import hmac` の重複追加を避ける (現状 router.py に無いことを確認済み) |

## Success Criteria

1. `python -m pytest tests/test_dsp_attribution_privacy.py -v` で 12 件全て pass
2. `python -m pytest tests/test_dsp_engine.py -v` で既存テストが pass を維持
3. `config.py` に 3 フィールドが追加されている
4. `dsp_engine.attribution.verify_postback_secret` / `sanitize_pii_payload` が importable・callable
5. `record_conversion` が `window_days` を受け取り窓外 spend_log に対し `impression_id=None` で CV を記録
6. DB スキーマ変更・migration なし
7. `dsp_engine/bidder.py` に一切の変更なし
