# Plan: dsp_engine #11 動的フロア最適化

最終更新: 2026-05-28
関連: `tasks/handoff-dsp-engine.md` / `tasks/progress-dsp-engine.md` / `tasks/lessons.md`

## 🎯 ゴール

publisher 別の最適フロア CPM を事前計算 → 入札パスは L1 キャッシュ参照のみで動的フロア適用。本番 RTB 稼働時の overbid/underbid を機械的に抑制。

## ⏳ 進捗 & 次アクション

| Phase | 内容 | 状態 |
|---|---|---|
| 1 | テーブル `dsp_floor_price_history` + migration `dspengine0013` | ✅ master local `e109df3` (未 push) |
| **2** | **純粋関数 `compute_dynamic_floor()` (DB I/O ゼロ)** | **⏳ 次やる** |
| 3 | バッチ `floor_batch.py` + main.py lifespan 登録 + retention | ⏳ 未着手 |
| 4 | 入札パス統合 (`bidder.py` の effective_floor) | ⏳ 未着手 |

**次セッション開始時のアクション**:
1. master HEAD が `e109df3` (Phase 1 handoff commit) であることを確認
2. test-first-implement skill を Phase 2 のスコープで起動 (詳細は本書 section 4)
3. Phase 2-4 完了後に handoff/progress 更新 → `git push origin master` → `vercel --prod` 手動 → `/health` 200 確認 → handoff の deployment ID を最新化

---

## 0. サマリー

publisher 粒度の過去落札価格 (clearing_price) 分位点・落札率・bid density を合成して最適フロア CPM(USD) を算出し、`dsp_floor_price_history` テーブルに事前保存。入札パスは L1 キャッシュ参照のみ (外部 I/O ゼロ)。既存の静的 `imp.bidfloor` は温存し、動的フロア未算出 publisher はフォールバック動作。4 Phase 構成で各 Phase を test-first-implement で独立に Red→Green→Reviewer。

## 1. スコープ (Phase 表)

| Phase | サブタスク | 主な変更ファイル |
|---|---|---|
| 1 | `DspFloorPriceHistoryDB` モデル + migration `dspengine0013` | `db_models.py`, `alembic/versions/add_dsp_floor_price_history.py` (新規) |
| 2 | 純粋関数 `compute_dynamic_floor()` (3 要素合成) | `dsp_engine/floor.py` (新規), `tests/test_dsp_floor.py` (新規) |
| 3 | バッチ `dsp_engine/floor_batch.py` (新規) + main.py lifespan 登録 + retention | `dsp_engine/floor_batch.py` (新規), `main.py` |
| 4 | 入札パス統合: L1 キャッシュ参照で `bidder.py` の floor 差し替え | `dsp_engine/bidder.py`, `tests/test_dsp_bid_log.py` 拡張 |

スコープ外 (今回触らない):
- `shading.py` (shading は落札後の過払い防止。dynamic floor は入札前のフロア設定。責任が別)
- `auction/engine.py` の `_compute_clearing_price`
- `auction/openrtb.py` の `Impression.bidfloor` フィールド (SSP 側の静的値として残す)
- geo / ad-format / hour 粒度への細分化 (Open Question 1)
- bid_log への `publisher_id` カラム追加 (Open Question 7、全体 win_rate 近似で進める)
- admin 可視化 endpoint (Open Question 6)

## 2. 設計決定の根拠

### 2-A. floor の粒度: publisher 単位

- `DspSpendLogDB.publisher_id` は #6 で記録済み
- `segments.py` (platform) / `shading.py` (campaign) と直交、publisher 粒度は「インベントリ側価格帯」を捉える自然な切り口
- 細分化 (app/geo/ad-format) すると本番 inert の現状ではほぼ全キーが cold start
- cold start 時は静的 `imp.bidfloor` へフォールバックするため安全

### 2-B. 計算式

```
price_anchor_jpy   = percentile(cleared_prices_jpy, q=FLOOR_PERCENTILE)
win_rate_factor    = clamp(1.0 + (win_rate - TARGET_WIN_RATE) * WIN_RATE_SENSITIVITY, 0.5, 2.0)
density_factor     = clamp(1.0 + max(0, bid_density - 1) * DENSITY_SENSITIVITY, 1.0, 1.5)
optimal_floor_jpy  = price_anchor_jpy * win_rate_factor * density_factor
optimal_floor_usd  = optimal_floor_jpy / jpy_per_usd
```

定数 (ハードコード開始、Open Question 3 で config 化可否を判断):
- `FLOOR_LOOKBACK_DAYS = 7`
- `FLOOR_COLD_START_MIN = 10`
- `FLOOR_PERCENTILE = 50` (P50)
- `TARGET_WIN_RATE = 0.3`
- `WIN_RATE_SENSITIVITY = 0.5`
- `DENSITY_SENSITIVITY = 0.1`
- `FLOOR_REFRESH_SEC = 3600` (1 時間ごと)
- retention: 30 日

### 2-C. 入札パスでの適用

`bidder.py` の `imp.bidfloor` 参照箇所 (L557, L573, L577) を以下に置き換え:

```python
publisher_id = _extract_publisher_id(bid_request)
dynamic_floor = get_dynamic_floor(publisher_id)
effective_floor_usd = max(dynamic_floor, imp.bidfloor) if dynamic_floor is not None else imp.bidfloor
```

`imp.bidfloor` には**代入しない** (OpenRTB オブジェクトのミュート禁止)。`get_dynamic_floor` が None なら既存挙動を完全保持。

### 2-D. bid_log の publisher_id 不在問題

`DspBidLogDB` には publisher_id カラムが無い。Phase 3 のバッチは:
- `win_rate` = 全体 `DspSpendLogDB` 件数 / `DspBidLogDB outcome='bid'` 全件数 (全体近似)
- `bid_density` = `DspBidLogDB.candidate_count` の全行中央値

publisher 別 win_rate は Open Question 7 へ。初版は全体近似で実装。

## 3. Phase 1: テーブル + migration

### 3-1. ORM モデル (`db_models.py`)

```python
class DspFloorPriceHistoryDB(Base):
    __tablename__ = "dsp_floor_price_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    publisher_id: Mapped[str] = mapped_column(String(64), index=True)
    floor_usd: Mapped[float] = mapped_column(Float)
    floor_jpy: Mapped[float] = mapped_column(Float)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    bid_density: Mapped[float] = mapped_column(Float, default=1.0)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    __table_args__ = (
        Index("ix_dsp_floor_hist_pub_computed", "publisher_id", "computed_at"),
    )
```

### 3-2. Migration

- ファイル: `alembic/versions/add_dsp_floor_price_history.py`
- revision: `dspengine0013` (着手時に `python -m alembic heads` で head = `dspengine0012` 確認、`grep -rhn "^revision" alembic/versions/*.py` で 0013 未採番確認。教訓15・17)
- down_revision: `dspengine0012`
- 冪等化: `insp.has_table` + `CREATE TABLE IF NOT EXISTS` 同等、Index 各々 `_has_index` ガード (教訓14・19)
- inspector は DDL 後に再取得 (教訓19)
- downgrade: `DROP TABLE IF EXISTS dsp_floor_price_history`

### 3-3. Red テスト

`tests/test_dsp_floor.py` (新規) に `test_floor_price_history_table_exists`。`PRAGMA table_info` + `PRAGMA index_list` で table とインデックスの存在検証。

### 3-4. Done

- table と `ix_dsp_floor_hist_pub_computed` index 存在
- 既存 dsp 系 54 件 regression なし

## 4. Phase 2: 純粋関数 `compute_dynamic_floor`

### 4-1. 新規ファイル `dsp_engine/floor.py`

- `compute_dynamic_floor(cleared_prices_jpy, win_rate, bid_density, jpy_per_usd, config=None) -> float | None`
  - sample 数 < `FLOOR_COLD_START_MIN` → `None`
  - 戻り値は USD CPM
- `DEFAULT_FLOOR_CONFIG` dataclass/dict で定数群を公開

### 4-2. Red テスト

| テスト名 | 検証内容 |
|---|---|
| `test_compute_dynamic_floor_cold_start` | sample < COLD_START_MIN → None |
| `test_compute_dynamic_floor_p50` | 10 件 → P50 が price_anchor |
| `test_compute_dynamic_floor_high_win_rate` | win_rate > TARGET → floor 上振れ |
| `test_compute_dynamic_floor_low_win_rate` | win_rate < TARGET → floor 下振れ (clamp あり) |
| `test_compute_dynamic_floor_high_density` | density 大 → floor 上振れ |
| `test_compute_dynamic_floor_returns_usd` | jpy_per_usd で割った USD |
| `test_compute_dynamic_floor_clamp_upper` | win_rate + density の clamp が効く |

### 4-3. Done

- 7 テスト Green
- 純粋関数 (async/DB なし)

## 5. Phase 3: バッチ + lifespan 登録

### 5-1. `dsp_engine/floor_batch.py`

```python
_floor_cache: dict[str, float] = {}

def get_dynamic_floor(publisher_id: str | None) -> float | None: ...
async def recompute_floor_prices(db) -> dict[str, float]: ...
async def prime_floor_cache(db) -> None: ...
async def schedule_floor_tasks() -> None: ...
```

クエリ設計 (SQLite/Postgres 共通):
1. `DspSpendLogDB` を publisher_id で GROUP、過去 7 日の `cleared_price_jpy` を Python リストに集約
2. 全体 win_rate = spend 件数 / `DspBidLogDB outcome='bid'` 件数
3. bid_density = `DspBidLogDB.candidate_count` の中央値 (Python `statistics.median`)
4. publisher 毎に `compute_dynamic_floor()` を呼ぶ
5. 結果を `DspFloorPriceHistoryDB` に INSERT
6. `_floor_cache` 全置換
7. 30 日超レコードを DELETE (retention)

### 5-2. main.py の lifespan 登録

`schedule_supply_chain_tasks` / `schedule_segment_tasks` と同じパターン:
```python
from dsp_engine.floor_batch import schedule_floor_tasks
floor_task = asyncio.create_task(schedule_floor_tasks())
# yield 後に floor_task.cancel()
```

Alembic upgrade コードは触らない (教訓13)。

### 5-3. Red テスト

| テスト名 | 検証内容 |
|---|---|
| `test_recompute_floor_writes_history` | spend_log 10 件 seed → INSERT 1 行 |
| `test_recompute_floor_cache_updated` | recompute 後 `get_dynamic_floor()` が float |
| `test_prime_floor_cache_from_db` | seed → prime → cache に反映 |
| `test_recompute_floor_cold_start_no_write` | sample 不足は DB 保存しない |
| `test_old_records_deleted` | 31 日前レコードが削除される |

### 5-4. Done

- 5 テスト Green
- main.py に `floor_task` + cancel
- 既存 dsp 系 regression なし

## 6. Phase 4: 入札パス統合

### 6-1. `dsp_engine/bidder.py` 変更

- `from dsp_engine.floor_batch import get_dynamic_floor` を import
- `_extract_publisher_id(bid_request) -> str | None` を新設 (純粋関数。site.publisher / app.publisher の id 解決)
- `handle_bid_request` 冒頭で `effective_floor_usd = max(dynamic, imp.bidfloor) if dynamic is not None else imp.bidfloor`
- `imp.bidfloor` 参照箇所 (L557, L573, L577) を `effective_floor_usd` に置換
- `imp.bidfloor` フィールドへの代入はしない
- `_log_bid_decision` の `bidfloor_usd` 引数は `imp.bidfloor` のまま (静的値をログに残して比較可能に)

### 6-2. Red テスト

| テスト名 | 検証内容 | ファイル |
|---|---|---|
| `test_dynamic_floor_applied_no_bid` | get_dynamic_floor mock で高 floor → NBR_BELOW_FLOOR | `test_dsp_bid_log.py` |
| `test_dynamic_floor_applied_bid` | mock で低 floor → imp.bidfloor フォールバック動作 | `test_dsp_bid_log.py` |
| `test_extract_publisher_id_from_site` | site.publisher.id 解決 | `test_dsp_floor.py` |
| `test_extract_publisher_id_none` | publisher 無し → None | `test_dsp_floor.py` |

`test_dynamic_floor_none_fallback`: 既存テストが全て `get_dynamic_floor()=None` で動くため自動検証。

### 6-3. Done

- 4 新規テスト Green
- 既存 dsp 系 (54+Phase 1-3 で +12-14 件 = 〜70 件想定) regression なし

## 7. 進め方 (test-first-implement)

各 Phase 独立に Red→Plan→Green→Reviewer→merge。各 Phase の commit 前に:
- `git branch --show-current` で着地先確認 (教訓24)
- `git diff --cached` で混入確認 (教訓18)
- Phase 1: `python -m alembic heads` で head 確認 (教訓15・17)

Phase 1 の migration 検証: `ssp_local.db` コピー + `stamp dspengine0012` + `upgrade dspengine0013` で table・index 確認 (教訓16・19)。

全 Phase 完了後に handoff/progress 更新 + git push → `vercel --prod` 手動 (Fujiさん) で本番反映。

## 8. Deletions

なし — 削除する関数・カラム・UI 要素はゼロ。新規作成のみ (`floor.py`, `floor_batch.py`, migration、テスト)。

## 9. Risks / Open Questions

| # | 項目 | planner default | 影響 |
|---|---|---|---|
| 1 | floor 粒度 | publisher 単位 | 細分化は別 issue。本番 inert ではデータ不足 |
| 2 | 分位点 | P50 | `FLOOR_PERCENTILE` config 化で変更可 |
| 3 | sensitivity 初期値 | ハードコード開始 | 本番稼働後に config 昇格 |
| 4 | バッチ頻度 | 1 時間 (3600s) | 本番 inert では差なし |
| 5 | retention | 30 日 | トレンド分析重視なら 90 日 |
| 6 | admin 可視化 endpoint | 今回なし | 必要なら Phase 3 に追加 |
| 7 | bid_log publisher_id | 全体 win_rate 近似 | publisher 別は別 migration |

## 10. 参照ファイル

実装エージェントが着手前に Read するファイル:
- `dsp_engine/segments.py` (L1 cache + バッチパターン)
- `dsp_engine/bidder.py` (L556-577 周辺)
- `dsp_engine/shading.py` (責任分担確認)
- `db_models.py` (DspSpendLogDB / DspBidLogDB のカラム)
- `main.py` (lifespan の create_task パターン)
- `alembic/versions/add_dsp_composite_indexes.py` (migration 冪等化パターン)
- `tests/test_dsp_bid_log.py` (既存 bidfloor 使用パターン)
