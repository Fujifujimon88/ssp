# Implementation Plan (test-first-implement Stage 3)

## Overview
Red テスト 16 件 (commit 5572f83, `tests/test_dsp_fraud.py`) を green にする最小実装。新規モジュール `dsp_engine/fraud.py` を追加し、`nbr.py` / `attribution.py` / `bidder.py` / `db_models.py` / `config.py` に最小限の変更を加え、migration `add_dsp_fraud.py` (dspengine0010) でカラムを追加する。

## Requirements (Red テストから逆算)

- `test_click_rate_limit_token_exceeds_limit`: `check_click_rate_limit(redis, click_token, client_ip, *, token_limit, ip_limit, window_seconds, _override_token_count, _override_ip_count) -> bool`。`_override_token_count > token_limit` のとき True。
- `test_click_rate_limit_ip_exceeds_limit`: 同関数。`_override_ip_count > ip_limit` のとき True。
- `test_click_rate_limit_token_and_ip_independent`: 両方が閾値内なら False。
- `test_click_rate_limited_request_does_not_record_click_event`: `record_click(db, click_token, rate_limited=False/True)` のシグネチャ変更。`rate_limited=True` のとき `DspClickEventDB` を挿入しない（同一 click_token の click_event 件数が 1 のまま）。
- `test_validate_revenue_rejects_negative`: `validate_revenue(-1.0, avg_purchase_value_jpy=10000, revenue_cap_multiplier=10) is False`。
- `test_validate_revenue_rejects_outlier`: `validate_revenue(100001.0, ...)` 上限 `avg * multiplier` 超で False。
- `test_validate_revenue_accepts_normal`: `validate_revenue(5000.0, ...) is True`。
- `test_validate_revenue_accepts_zero`: `validate_revenue(0.0, ...) is True`。
- `test_is_ivt_datacenter_ip_detected`: `is_ivt(client_ip, user_agent, *, datacenter_cidrs) -> bool`。CIDR 内 IP は True。
- `test_is_ivt_bot_user_agent_detected`: bot UA キーワード含む UA は True (datacenter_cidrs=[] でも)。
- `test_is_ivt_normal_request_not_ivt`: CIDR 外 IP + 通常 UA は False。
- `test_handle_bid_request_ivt_strict_no_bid`: `handle_bid_request` が bot UA を持つ `BidRequest` に対して None を返し、`DspBidLogDB.nbr == NBR_IVT_DETECTED (506)` が 1 件以上記録される。`Device` に `ua` フィールドが必要 (openrtb)。
- `test_brand_safety_bcat_match_blocks`: `is_brand_safety_blocked(bid_request, campaign) -> bool`。`DspCampaignDB.bcat_block` (JSON 文字列) の IAB 親カテゴリが `site.cat` の prefix と一致したら True。
- `test_brand_safety_badv_match_blocks`: `DspCampaignDB.badv_block` (JSON 文字列) のドメインが `site.domain` と一致したら True。
- `test_brand_safety_no_match_passes`: bcat/badv 非一致なら False。
- `test_handle_bid_request_all_brand_safety_blocked_no_bid`: 全キャンペーンが brand safety でブロックされたら None を返し、`DspBidLogDB.nbr == NBR_BRAND_SAFETY_BLOCK (507)` が 1 件以上記録される。

## Files to Change

- `dsp_engine/fraud.py` (新規作成) — `check_click_rate_limit` / `validate_revenue` / `is_ivt` / `is_brand_safety_blocked` の 4 関数を実装 — Risk: Low
- `dsp_engine/nbr.py` — `NBR_IVT_DETECTED=506` / `NBR_BRAND_SAFETY_BLOCK=507` と対応ラベルを追加 — Risk: Low
- `dsp_engine/attribution.py` — `record_click` のシグネチャに `rate_limited: bool = False` を追加。`rate_limited=True` のとき `DspClickEventDB` 挿入をスキップして None を返す — Risk: Low
- `dsp_engine/bidder.py` — `handle_bid_request` に IVT チェック (campaigns 取得前) + brand safety チェック (campaign ループ内) を挿入。`NBR_IVT_DETECTED` / `NBR_BRAND_SAFETY_BLOCK` の import 追加 — Risk: Medium
- `db_models.py` — `DspCampaignDB` に `bcat_block: Mapped[Optional[str]]` (nullable Text, default `"[]"`) と `badv_block: Mapped[Optional[str]]` (nullable Text, default `"[]"`) を追加 — Risk: Low
- `config.py` — `dsp_ivt_strict: bool = True` / `dsp_datacenter_cidrs: str = ""` (カンマ区切り) / `dsp_click_token_limit: int = 10` / `dsp_click_ip_limit: int = 50` / `dsp_click_window_seconds: int = 3600` / `dsp_revenue_cap_multiplier: float = 10.0` を `Settings` に追加 — Risk: Low
- `alembic/versions/add_dsp_fraud.py` (新規作成) — revision `dspengine0010` / down_revision `dspengine0009`。`dsp_campaigns` に `bcat_block` / `badv_block` を nullable + server_default='[]' で追加。冪等性: `_has_column` で存在確認してから `add_column` — Risk: Low

## Deletions (明示必須)

なし。既存カラム・テーブル・関数の削除は一切行わない。

## Implementation Steps

1. **nbr.py に 506/507 を追加** — File: `dsp_engine/nbr.py` — `NBR_IVT_DETECTED = 506` / `NBR_BRAND_SAFETY_BLOCK = 507` を定数として追記し、`NBR_LABELS` dict に対応エントリを追加する — Dependencies: なし

2. **config.py に fraud 設定フィールドを追加** — File: `config.py` — `Settings` クラスに `dsp_ivt_strict`, `dsp_datacenter_cidrs`, `dsp_click_token_limit`, `dsp_click_ip_limit`, `dsp_click_window_seconds`, `dsp_revenue_cap_multiplier` を追加。全てデフォルト値あり、`extra="ignore"` の既存設定のため後方互換 — Dependencies: なし

3. **db_models.py に bcat_block / badv_block を追加** — File: `db_models.py` — `DspCampaignDB` クラスに `bcat_block: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="[]")` と `badv_block: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="[]")` を追加。SQLite インメモリテストでは `Base.metadata.create_all` がこのカラムを含むため migration なしでテストが通る — Dependencies: なし

4. **dsp_engine/fraud.py を新規作成** — File: `dsp_engine/fraud.py` — 以下 4 関数を実装する:
   - `check_click_rate_limit(redis, click_token, client_ip, *, token_limit, ip_limit, window_seconds, _override_token_count=None, _override_ip_count=None) -> bool`: `_override_token_count` が渡された場合はその値を使い、`token_count > token_limit or ip_count > ip_limit` なら True。redis=None のとき（テスト / メモリフォールバック）は `_override_*_count` のみで判定し Redis 参照は行わない。
   - `validate_revenue(revenue_jpy, *, avg_purchase_value_jpy, revenue_cap_multiplier) -> bool`: `revenue_jpy < 0` なら False、`revenue_jpy > avg_purchase_value_jpy * revenue_cap_multiplier` なら False、それ以外 True。
   - `is_ivt(client_ip, user_agent, *, datacenter_cidrs) -> bool`: (a) `ipaddress.ip_address(client_ip)` が `datacenter_cidrs` の任意 CIDR に含まれれば True。(b) `user_agent` に bot シグネチャ (大文字小文字無視で `bot`, `crawl`, `spider`, `slurp`, `mediapartners` 等) が含まれれば True。どちらも該当しなければ False。純粋関数・外部 I/O なし。
   - `is_brand_safety_blocked(bid_request, campaign) -> bool`: `campaign.bcat_block` (JSON 文字列) をパースして blocked_cats を取得。`bid_request.site.cat` の各要素が blocked_cats のいずれかの prefix と一致したら True。次に `campaign.badv_block` (JSON 文字列) をパースして `bid_request.site.domain` が blocked_advs に含まれたら True。どちらも一致しなければ False。純粋関数・外部 I/O なし。
   - Dependencies: Step 1 (NBR 定数は fraud.py では不要だが nbr.py は先に完成させる)

5. **attribution.py の record_click を変更** — File: `dsp_engine/attribution.py` — `record_click(db, click_token, rate_limited: bool = False)` にデフォルト引数を追加。関数先頭で `if rate_limited: return None` を挿入し、`DspClickEventDB` 挿入・commit をスキップする。戻り値は `Optional[DspSpendLogDB]` のまま変わらないが、`rate_limited=True` のとき spend_log の `select` も省いて即 None を返してよい（テストは「挿入しない」のみを検証） — Dependencies: Step 3 (DspClickEventDB は既存。変更不要)

6. **bidder.py に IVT / brand safety チェックを挿入** — File: `dsp_engine/bidder.py` — 以下の 2 箇所を変更する:
   - import 追加: `from dsp_engine.fraud import is_ivt, is_brand_safety_blocked` / `from dsp_engine.nbr import ..., NBR_IVT_DETECTED, NBR_BRAND_SAFETY_BLOCK`
   - IVT チェック: `list_active_campaigns(db)` 呼び出しの直前に挿入。`settings.dsp_ivt_strict` が True のとき、`bid_request.device` が存在すれば `is_ivt(device.ip or "", device.ua or "", datacenter_cidrs=_parse_cidrs(settings.dsp_datacenter_cidrs))` を呼び、True なら `_log_bid_decision(..., nbr=NBR_IVT_DETECTED)` して None を返す。`_parse_cidrs` はモジュール内ヘルパー (カンマ split / strip / 空除去)。外部 I/O なし、L1 として settings のカンマ文字列を直接使う。
   - brand safety チェック: キャンペーンループの `can_bid` チェックの前に `is_brand_safety_blocked(bid_request, campaign)` を呼ぶ。True なら当該キャンペーンを candidates から除外 (continue)。全キャンペーンが除外された場合 (`best_campaign is None` のとき `paced_out_count == 0` かつ brand safety 除外があった) を検出するため `brand_safety_blocked_count` カウンタを追加し、`best_campaign is None and brand_safety_blocked_count == candidate_count` なら `NBR_BRAND_SAFETY_BLOCK` で no-bid ログを記録して None を返す。
   - Dependencies: Step 1, Step 4

7. **migration add_dsp_fraud.py を新規作成** — File: `alembic/versions/add_dsp_fraud.py` — `revision = "dspengine0010"` / `down_revision = "dspengine0009"`. `upgrade()` で `insp = inspect(conn)` → `dsp_campaigns` に `bcat_block` / `badv_block` が無ければ `add_column(Text, nullable=True, server_default="[]")` を実行。`downgrade()` で `drop_column`。冪等性: `_has_column` ガード付き (add_dsp_ab_test.py と同じパターン) — Dependencies: Step 3

## Testing Strategy

- Red テスト (commit 5572f83 済み): `tests/test_dsp_fraud.py` の 16 テスト
  1. `test_click_rate_limit_token_exceeds_limit`
  2. `test_click_rate_limit_ip_exceeds_limit`
  3. `test_click_rate_limit_token_and_ip_independent`
  4. `test_click_rate_limited_request_does_not_record_click_event`
  5. `test_validate_revenue_rejects_negative`
  6. `test_validate_revenue_rejects_outlier`
  7. `test_validate_revenue_accepts_normal`
  8. `test_validate_revenue_accepts_zero`
  9. `test_is_ivt_datacenter_ip_detected`
  10. `test_is_ivt_bot_user_agent_detected`
  11. `test_is_ivt_normal_request_not_ivt`
  12. `test_handle_bid_request_ivt_strict_no_bid`
  13. `test_brand_safety_bcat_match_blocks`
  14. `test_brand_safety_badv_match_blocks`
  15. `test_brand_safety_no_match_passes`
  16. `test_handle_bid_request_all_brand_safety_blocked_no_bid`
- Green 目標: 上記 16 件が全 pass する最小実装。既存 dsp テスト群 (`test_dsp_engine.py` 等) が引き続き pass することを確認する。
- migration 検証 (本番 DB には upgrade しない): `alembic upgrade dspengine0010` を populated DB コピーで実行し、冪等性 (2 回 upgrade が無害) と `downgrade` の両方を確認する。

## Risks & Mitigations

| リスク | 内容 | 対策 |
|---|---|---|
| `record_click` シグネチャ変更の呼び出し元互換 | `router.py` が `record_click(db, ct)` を呼んでいる箇所はデフォルト引数 `rate_limited=False` のため無変更で互換 | 変更前に `router.py` で `record_click` 呼び出しを Grep 確認。デフォルト引数で後方互換を保証する |
| bidder.py の brand safety ロジックで paced_out との混同 | 全 paced_out と全 brand safety blocked が同時発生したとき NBR 判定が誤る | `brand_safety_blocked_count` と `paced_out_count` を独立カウンタとして分離し、`brand_safety_blocked_count > 0 and best_campaign is None` でも NBR_BRAND_SAFETY_BLOCK を使う (paced_out との OR 判定は避ける) |
| bcat prefix マッチングの範囲 | テストは `bcat_block='["IAB25"]'` に対し `site.cat=["IAB25-3"]` が prefix 一致でブロックされることを期待 | `cat.startswith(blocked_cat)` で判定。`"IAB25-3".startswith("IAB25")` は True |
| is_ivt の bot UA シグネチャ | `"Googlebot/2.1 (..."` を検出する必要がある。正規表現を使わず `lower()` 後に `"bot"` を `in` 判定すれば十分 | シンプルに `"bot" in ua.lower()` で対応。誤検知リスクは許容範囲（テストが通れば合格） |
| IVT チェックで `bid_request.device` が None の場合 | `Device` が無い BidRequest は IVT チェックをスキップして通常フローに進む | `if bid_request.device is None: pass` で skip |

## Success Criteria

- `python -m pytest tests/test_dsp_fraud.py -v` が 16/16 pass。
- `python -m pytest tests/test_dsp_engine.py -v` (既存テスト群) が引き続き全 pass。
- `alembic upgrade dspengine0010` が populated DB コピーで成功し、2 回目実行でもエラーなし (冪等)。
- `alembic downgrade dspengine0009` が成功し、`bcat_block` / `badv_block` カラムが消える。
- 本番 Postgres DB への `alembic upgrade` は実行しない。
