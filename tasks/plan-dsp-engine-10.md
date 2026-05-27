# Plan: dsp_engine #10 データ基盤・運用堅牢化

最終更新: 2026-05-28
関連: `tasks/handoff-dsp-engine.md` / `tasks/progress-dsp-engine.md` / `tasks/lessons.md`

## 0. サマリー

DSP #10「データ基盤・運用堅牢化」を 3 Phase で実装する。各 Phase を test-first-implement スキルで独立に Red→Plan→Green→Reviewer する。bidder.py `_incr_nbr_counter` の教訓21違反 (INCR+EXPIRE 毎回) 是正は同じ Redis EXPIRE テーマのため Phase 3 に統合。

## 1. スコープ

| Phase | サブタスク | 主な変更ファイル |
|---|---|---|
| 1 | 複合インデックス追加 (5 本) | `alembic/versions/add_dsp_composite_indexes.py` (新規), `db_models.py` (`__table_args__`), `tests/test_dsp_engine.py` |
| 2 | 管理画面 N+1 解消 (`/admin/campaigns` ループ内 `get_campaign_roas`) | `dsp_engine/router.py`, `dsp_engine/campaign_manager.py`, `tests/test_dsp_engine.py` |
| 3 | QPS カウンタ Redis 化 + bidder.py nbr カウンタの教訓21違反是正 | `dsp_engine/exchange.py`, `dsp_engine/router.py`, `dsp_engine/bidder.py`, `tests/test_dsp_exchange.py` (or 新規) |

スコープ外 (handoff #11):
- 動的フロア最適化 (`#11`)
- SKAN/Privacy Sandbox (`#9-2`)
- 本番 Redis 接続のインフラ作業 (Upstash 設定等)

## 2. Phase 1: 複合インデックス追加

### 2.1 追加対象 (5 本)

| # | テーブル | カラム | 用途 |
|---|---|---|---|
| 1 | `dsp_spend_logs` | `(campaign_id, logged_at)` | `reporting.run_report`, `campaign_manager.get_campaign_stats` の campaign 別期間集計 |
| 2 | `dsp_click_events` | `(campaign_id, clicked_at)` | 同上 (click 集計) |
| 3 | `dsp_conversion_events` | `(campaign_id, attributed, received_at)` | ROAS 集計の `attributed=True` フィルタ + 期間 |
| 4 | `dsp_bid_logs` | `(outcome, campaign_id)` | `get_campaign_win_rates` の outcome='bid' + campaign_id 集計 |
| 5 | `dsp_bid_logs` | `(campaign_id, nbr, logged_at)` | `run_ab_experiment_report` の holdout COUNT |

### 2.2 Migration

- ファイル: `alembic/versions/add_dsp_composite_indexes.py`
- revision: `dspengine0012` (着手時に `python -m alembic heads` で head が `dspengine0011` であることと `dspengine0012` の衝突がないことを再確認。教訓17)
- down_revision: `dspengine0011`
- 冪等化: `CREATE INDEX IF NOT EXISTS` (教訓14)
- downgrade: `DROP INDEX IF EXISTS`

### 2.3 ORM 同期

`db_models.py` の対象テーブル 4 つ (`DspSpendLogDB`, `DspClickEventDB`, `DspConversionEventDB`, `DspBidLogDB`) に `__table_args__ = (Index(...), ...)` で追記。既存 `__table_args__` がある場合は tuple を拡張。autogenerate との整合のため。

### 2.4 Red テスト

- `tests/test_dsp_engine.py` に `test_composite_indexes_exist` を追加。
- `PRAGMA index_list('<table>')` で 5 インデックスの存在を検証。
- migration 適用前 FAIL → 適用後 PASS。

### 2.5 Done

- 5 インデックスが存在
- 既存 dsp 系テスト (40 件) regression なし

## 3. Phase 2: 管理画面 N+1 解消

### 3.1 対象

`dsp_engine/router.py` の `admin_campaigns_page` (`GET /dsp-engine/admin/campaigns`):

```python
campaigns = await campaign_manager.list_campaigns(db)
for c in campaigns:
    roas = await get_campaign_roas(db, c.id)   # ← N+1
```

`get_campaign_roas` は内部で 3 クエリ (spend/click/conv 各 1) → N キャンペーンで 3N クエリ。

### 3.2 解消方針

既存の `campaign_manager.get_all_campaign_stats` (IN クエリ 3 本で全 campaign を一括集計) を活用。`compute_roas_from_stats(stats: dict) -> dict` を新設して ROAS/CPA/CTR を純粋関数化し、ループ内で stats から計算。

### 3.3 Red テスト

`tests/test_dsp_engine.py` に `test_admin_campaigns_no_n_plus_1` を追加。3 キャンペーン seed → エンドポイント呼び出し → `get_campaign_roas` が呼ばれないこと (or `get_all_campaign_stats` が 1 回だけ) を `mock.patch` で検証。

### 3.4 注意

- HTML テンプレート (`templates/dsp_admin_campaigns.html` 想定) が使う dict キーを Phase 2 着手時に grep で確認。既存 `get_campaign_roas` の return 構造 (`{impressions, clicks, spend_jpy, conversions, revenue_jpy, roas, cpa, ctr}`) を `compute_roas_from_stats` で再現する必要あり。
- 他の admin エンドポイント (`admin_list_creatives`, `admin_report_api` 等) は調査済みで N+1 なし。

### 3.5 Done

- N キャンペーンで `get_all_campaign_stats` 呼び出しが 1 回固定
- 既存 admin/campaigns の HTML レンダリング regression なし

## 4. Phase 3: QPS カウンタ Redis 化 + bidder.py nbr 修正

### 4.1 QPS Redis 化

対象: `dsp_engine/exchange.py` の `check_qps` (現状 sync `def`、`_qps_window: dict` in-memory)。

設計:
```python
async def check_qps(exchange_name: str, qps_limit: int, redis=None) -> bool:
    if qps_limit <= 0:
        return True
    if redis is None:
        return _check_qps_inmemory(exchange_name, qps_limit)  # 既存ロジック保持
    now_sec = int(time.time())
    key = f"dsp:qps:{exchange_name}:{now_sec}"
    count = await redis.incr(key)
    if count == 1:                       # 教訓21
        await redis.expire(key, 2)
    return count <= qps_limit
```

`router.py:657` 側 (`inbound_bid`) は `redis = await get_redis()` を try/except で取得し `await exchange.check_qps(..., redis=redis)` を呼ぶ。`get_redis()` 失敗時は `redis=None` で in-memory フォールバック (リクエストを落とさない)。

**既存テスト 3 件の async/await 化 (signature 更新)**:
- `tests/test_dsp_engine.py::test_check_qps_under_limit` (line 289-)
- `tests/test_dsp_engine.py::test_check_qps_blocks_over_limit` (line 295-)
- `tests/test_dsp_engine.py::test_check_qps_unlimited` (line 303-)

これらは sync で `check_qps(name, limit)` を呼んでいるので、`async def` + `await check_qps(name, limit)` (redis 省略で in-memory フォールバック) に書き換える必要あり。テスト関数の削除ではなく signature 更新なので削除 guard には抵触しない。Plan の Deletions セクションには「なし」と記載。

### 4.2 bidder.py `_incr_nbr_counter` 修正 (教訓21違反)

現状 (L81-82):
```python
await r.incr(key)
await r.expire(key, _NBR_TTL_SEC)   # ← 毎回呼ぶと固定ウィンドウ延長
```

修正:
```python
count = await r.incr(key)
if count == 1:
    await r.expire(key, _NBR_TTL_SEC)
```

### 4.3 Red テスト

`tests/test_dsp_exchange.py` (新規 or 既存拡張) に:
- `test_check_qps_redis_fixed_window`: FakeRedis で qps_limit=2 のとき 1, 2 は True / 3 は False / 1 秒後 (キー期限切れ) でリセット
- `test_check_qps_redis_expire_only_on_first_incr`: EXPIRE が count==1 のときのみ呼ばれること (FakeRedis call 履歴)
- `test_check_qps_fallback_no_redis`: `redis=None` で in-memory フォールバック動作
- `tests/test_dsp_engine.py` (or test_dsp_fraud_wiring.py) に bidder `_incr_nbr_counter` の EXPIRE が count==1 のみ呼ばれることのテスト

### 4.4 Done

- QPS が Redis で固定ウィンドウ動作 + Redis 不在時 in-memory フォールバック
- bidder.py `_incr_nbr_counter` が教訓21準拠
- 既存 dsp 系テスト regression なし

## 5. 進め方

各 Phase ごとに `test-first-implement` スキルを起動:
- Phase 1 → reviewer Approve → commit
- Phase 2 → reviewer Approve → commit
- Phase 3 → reviewer Approve → commit
- 全 Phase 完了後に `handoff-dsp-engine.md` と `progress-dsp-engine.md` を更新

## 6. Risks / Open Questions

| # | 項目 | 対処 |
|---|---|---|
| 1 | 本番 Redis 未接続 (`/health` redis:false) | Phase 3 のフォールバック実装で本番動作に影響なし。実効化は別タスク |
| 2 | `admin_campaigns_page` のテンプレートが期待する dict キー | Phase 2 着手時に template を grep して `compute_roas_from_stats` の return 構造を合わせる |
| 3 | `dspengine0012` 採番衝突 | Phase 1 着手時に `python -m alembic heads` と `grep -rhn "^revision" alembic/versions/*.py` で再確認 (教訓17) |
| 4 | 共有 working tree で並行セッション干渉 | 各 commit 前に `git branch --show-current` と `git diff --cached` を確認 (教訓18, 24) |
