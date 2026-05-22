# plan: dsp_engine #8 — fraud / IVT / brand safety 監視

Status: 計画済み・未着手（2026-05-22 調査完了。次セッションが着手）

## 3行サマリー
- A クリック連打レート制限 / B IVT 入札フィルタ / C 異常 CV ガード / D brand safety の4本柱。
- IVT・brand safety は入札パス内で no-bid ブロック（config `dsp_ivt_strict` 等で切替可）。
- Red-first TDD（失敗テスト ~16件先行）→ 実装 → migration dspengine0010 検証の順。

## スコープ（Fuji 承認: フル・ブロック有効）
| 記号 | 内容 | 処理位置 |
|---|---|---|
| A | クリック連打レート制限（Redis カウンタ）| `/dsp-engine/click` エンドポイント（入札パス外）|
| B | IVT 判定（datacenter IP / bot UA）| 入札パス内・純粋関数 + L1 キャッシュ |
| C | 異常 CV ガード（負値・revenue 異常値・IP/UA 記録）| `record_conversion`（後段）|
| D | brand safety（bcat/badv/site.cat/app.cat 照合）| 入札パス内・純粋関数 + L1 キャッシュ |

## 設計判断（採用案）
- レート制限キー = `click_token` + `client_ip` の両方を並行チェック（時間バケット 1h）。
- レート制限時は LP へリダイレクトは継続し DspClickEventDB は記録しない（`rate_limited=True` で可視化）。
- IVT/brand safety は flag + no-bid。`config.dsp_ivt_strict`（既定 True）で no-bid/log-only 切替。
- brand safety は global（settings）+ campaign（DspCampaignDB.bcat_block/badv_block）の和集合。
- datacenter IP は `config` の CIDR リスト（既定空。初期は bot UA シグネチャ判定のみ）。
- nbr 追加: `NBR_IVT_DETECTED=506` / `NBR_BRAND_SAFETY_BLOCK=507`。

## 変更ファイル
- 新規 `dsp_engine/fraud.py` — rate limit / IVT 判定 / brand safety / revenue 検証 / L1 キャッシュ
- `dsp_engine/nbr.py` — 506/507 追加
- `dsp_engine/attribution.py` — record_click / record_conversion に IP・UA・ガード追加
- `dsp_engine/router.py` — /click・/conversion で IP・UA 取得、レート制限分岐
- `dsp_engine/bidder.py` — handle_bid_request に IVT・brand safety チェック挿入
- `dsp_engine/batch.py` — IVT/brand safety L1 キャッシュ更新ループ
- `db_models.py` — DspClickEventDB / DspConversionEventDB に client_ip・user_agent 等、
  DspCampaignDB に bcat_block・badv_block
- `config.py` — レート制限閾値・revenue 上限倍率・datacenter CIDR・dsp_ivt_strict
- 新規 migration `add_dsp_fraud.py`（revision `dspengine0010`、down_revision は実 `alembic heads`
  で確認。新規テーブルなし・カラム追加のみ。教訓14 冪等 / 教訓19 DDL 後 re-inspect）

## Red テスト（tests/test_dsp_fraud.py・~16件）
クリック連打レート制限（token/IP/独立性/DB 非記録）/ revenue 検証（負値・外れ値・正常）/
IVT 判定（datacenter IP・bot UA・正常）/ handle_bid_request の IVT no-bid /
brand safety（bcat・badv 一致でブロック・非一致で通過）/ 全キャンペーンブロック時の no-bid。

## ガード
- 入札パスに外部 I/O 禁止（教訓6）: B/D は純粋関数 + L1 キャッシュ参照のみ。
  HTTP fetch・DB・Redis は batch.py / エンドポイント層に置く。A は click エンドポイント
  内のため Redis 参照可（RTB 入札パスではない）。
- 本番 RTB bidder は低遅延・高 QPS。IVT/brand safety はナノ秒オーダーの set 演算に留める。
- migration 検証は populated DB コピーで upgrade/冪等/downgrade（教訓16）。本番 Postgres へは
  upgrade しない。

## ステップ
1. Red: tests/test_dsp_fraud.py に失敗テスト ~16件先行・checkpoint commit
2. db_models.py カラム追加 / config.py 設定追加 / nbr.py 506・507
3. migration add_dsp_fraud.py（dspengine0010）
4. dsp_engine/fraud.py 新規（rate limit / IVT / brand safety / revenue 検証）
5. attribution.py / router.py / bidder.py / batch.py へ統合
6. Green 検証: dsp 系テスト全 pass / migration を populated DB コピーで検証
7. code-reviewer レビュー → 指摘対応
8. handoff・progress・lessons・plan 更新
