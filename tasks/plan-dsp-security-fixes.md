# dsp_engine セキュリティ修正 3 件 (TDD)

3 行サマリー:
- レビュー指摘 3 件 (売上付け替え / crid 改竄 / daily pacing の DB 無視) を Red-first TDD で修正。
- スキーマ変更なし・migration 不要・金額発生操作なし。
- 既存テストの破壊は sign/verify 周りのみ、最小限で追従更新。

## Fix 1 (高) — CV 売上付け替え防止
- `dsp_engine/attribution.py` `record_conversion`: click_token から spend_log が引けたら
  `campaign_id = spend_log.campaign_id` を無条件採用。現状の `campaign_id or spend_log.campaign_id` を置換。
- リクエスト側 campaign_id が spend_log と不一致なら `logger.warning` を出す (CV は正しい campaign へ
  記録し続け、400 にはしない = CV を取りこぼさない)。

## Fix 2 (中) — win notice 署名に crid を含める
- `dsp_engine/bidder.py`: `_win_notice_message` / `sign_win_notice` / `verify_win_notice` に
  `crid: str = ""` を追加。署名対象を `ct|cid|src|bid|crid` に拡張。
- `win_notice_url`: `sign_win_notice(..., crid=creative_id or "")`。
- `dsp_engine/router.py` `win_notice`: `verify_win_notice(sig, ..., crid=crid)`。
- crid 既定 "" のため crid 無し経路は従来どおり。

## Fix 3 (中) — daily pacing の DB フォールバック
- `campaign_manager`: `get_campaign_stats` / `get_all_campaign_stats` に `daily_spend_jpy`
  (当日 UTC の `DspSpendLogDB.spend_jpy` 合計、`logged_at >= 当日0時UTC` で絞る) を追加。
- `pacing.BudgetPacer.can_bid` に `daily_spend_jpy: float = 0.0` を追加し、
  `spent = max(await get_spend(...), daily_spend_jpy)` で判定。
- `bidder.py` の `can_bid` 呼び出し箇所 (現状 518 行目付近): `can_bid(campaign, lifetime_spend_jpy=..., daily_spend_jpy=stats["daily_spend_jpy"])`。
- Redis flush / プロセス再起動 / 複数 worker でも DB 実績で当日消化を回復。

## TDD 手順
1. Red: `tests/test_dsp_engine.py` に失敗テスト 3 本追加
   (売上付け替え拒否 / crid 改竄で verify=False / Redis カウンタ 0 でも DB 実績で can_bid=False)。
2. `python -m pytest tests/test_dsp_engine.py` で Red 確認。
3. Green: 上記 source を修正。
4. 既存テスト (`test_sign_verify_win_notice_roundtrip` 等) を crid 引数に最小追従。
5. `python -m pytest tests/test_dsp_engine.py tests/test_dsp_bid_log.py tests/test_dsp_reporting.py`
   が全 green まで。MDM 系の無関係失敗は対象外 (memory 既知)。
