# E2E テスト教訓

## DB クエリ・SQLAlchemy 関連

### 1. ORM モデルの属性名を必ずコードで確認する
- `DeviceProfileDB.id` → 実際は `device_id` が PK
- `MdmImpressionDB.impression_id` → 実際は `id` が PK
- `AndroidDeviceDB.created_at` → 実際は `registered_at`
- **教訓**: PK 名がテーブルによって違う。クエリ書く前に db_models.py を必ず確認。

### 2. asyncio.gather / BackgroundTasks で同一 DB セッション共有は禁止
- `asyncio.gather(func_a(db), func_b(db))` → `InvalidRequestError: This session is provisioning a new connection`
- `background_tasks.add_task(func, db)` → リクエスト完了後にセッションが閉じられ、トランザクション不整合・二重課金が発生。
- **修正（gather）**: 逐次 await に変更する。
- **修正（BackgroundTasks）**: 各タスク関数内で `async with AsyncSessionLocal() as db:` で独自セッションを作成する。`db` パラメータは渡さない。
- **適用範囲**: analytics/report.py, main.py の reports/range, mdm/router.py の install_confirmed など全箇所。

### 3. Vercel サーバーレスで workers:1 が必須
- workers:2 以上だと同一 Vercel インスタンスに並列リクエストが届き DB セッション競合が発生。
- `playwright.config.js` に `workers: 1, fullyParallel: false` を設定。

### 4. Starlette ServerErrorMiddleware は text/plain で 500 を返す
- Python 例外が FastAPI の例外ハンドラをすり抜けると `text/plain; charset=utf-8 Internal Server Error` になる。
- JSON `{"detail": "..."}` が返らないので Vercel ログがなくても「Starlette に届いた」と判断できる。
- **対策**: DB クエリを try/except で囲み、エラーをログ出力してフォールバック値を返す。

### 5. Vercel デプロイ遅延への対処
- push 後、デプロイ完了まで 2〜3 分かかることがある（10 分以上かかるケースもあった）。
- `/health` の `version` を変更してデプロイ確認する（`bump version` コミット）。
- テスト前に `sleep 120` してから API を叩く。

## Playwright E2E 関連

### 6. Vercel コールドスタート対策
- global-setup.js に `fetchWithRetry(url, opts, 3, 2000)` を実装して 500/502/503 をリトライ。

### 7. CSS セレクタ `:first-of-type` は期待通りに動かないことがある
- `.kpi-grid:first-of-type .card` → 複数の親要素でそれぞれ「最初」にマッチするため意図と違う結果になる。
- **修正**: `page.locator(".kpi-grid").first().locator(".card")` を使う。

### 8. Playwright の `data: {...}` は JSON で送信される
- `request.post(url, { data: { ... } })` は `Content-Type: application/json` で JSON シリアライズして送る。
- FastAPI の `body: dict` で受け取れる。form-encoded ではない。

### 9. UI テストで pubInfo ロードを待機する
- ページ遷移後すぐに profile/スロット作成ボタンを操作すると `pubInfo` が未ロードで操作が失敗する。
- **修正**: `await expect(page.locator("#pub-name")).not.toHaveText("読み込み中...", { timeout: 8000 })` を先に入れる。

### 10. Android UA テストは browser.newContext を使う
- `setExtraHTTPHeaders({ 'User-Agent': '...' })` は HTTP ヘッダーのみで `navigator.userAgent` は変わらない。
- **修正**: `browser.newContext({ userAgent: '...' })` を使う。

### 12. .vercelignore のパスは必ず `/` 始まりにする
- `tasks/` と書くと `mdm/tasks/` など**全階層の同名ディレクトリ**が除外される。
- `/tasks/` と書けばリポジトリルートの `tasks/` だけに限定される。
- **適用範囲**: `.vercelignore` を編集するとき、常に絶対パス（`/` 始まり）で記述する。
- **発生時の症状**: `ModuleNotFoundError: No module named 'mdm.tasks'` で Vercel 500。

### 13. Alembic 自動マイグレーション（lifespan）を絶対に消すな
- `main.py` の `lifespan` に Alembic upgrade コードがあり、Vercel デプロイ時に自動実行される。
- 機能追加でコードを書き換えた際、lifespan 全体を上書きしてこのコードが消えた。
- **症状**: DB にカラムが追加されず、ORM SELECT が 500 を返す（`column does not exist`）。
- **修正**: lifespan に Alembic upgrade + health_check スケジューラーを必ず残す。
- **確認**: 新機能追加後は `alembic current` で DB が head にいるか確認する。

### 14. Alembic マイグレーションは冪等に書く（`IF NOT EXISTS`）
- `op.create_index()` / `op.create_table()` は既存オブジェクトに対して `DuplicateTableError` を投げる。
- DB が部分的に構築されている（前回の失敗など）と、マイグレーションが途中で止まる。
- **修正**: `conn = op.get_bind()` + `sa.text("CREATE INDEX IF NOT EXISTS ...")` で冪等に書く。
- **適用範囲**: 全ての新規マイグレーション。特に index 作成・table 作成操作。

### 11. MDM ダッシュボードのセクション順序
- 最初の `.section h2` は「リアルタイム（今日）」セクション → index 0。
- 「代理店 Top 5」は index 1、「アフィリエイト案件 Top 5」は index 2、「主要APIエンドポイント」は index 3。

### 15. 新規 Alembic マイグレーションの revision ID は衝突確認してから決める
- Date: 2026-05-21 / Trigger: dsp_engine マイグレーション追加で `Revision X is present more than once` + `Multiple head revisions`。
- Root Cause: 既存マイグレーションが手書きの短い revision ID（例 `e1f2a3b4c5d6`）を複数ファイルで使い回しており、同じ ID を選んでしまった。
- Mitigation: 採用前に `grep -rln "revision.*=.*'<id>'" alembic/versions/*.py` で衝突確認。記述的なユニーク slug（例 `dspengine0001`）を使う。`down_revision` は `alembic heads` の単一 head に合わせる。
- Detection: `python -m alembic heads` が単一 head を返すか確認する。

### 16. 新規マイグレーションは「populated DB のコピー」で検証する（fresh SQLite 不可）
- Date: 2026-05-21 / Trigger: 空 SQLite に `alembic upgrade head` すると古い `ALTER TABLE ... ADD COLUMN` 系で `no such table` 失敗。
- Root Cause: 既存マイグレーション群は `Base.metadata.create_all` でテーブルが先に存在する前提で書かれており、base からの全チェーンが SQLite で通らない。
- Mitigation: `ssp.db`（全テーブルあり・未スタンプ）をコピー → 直前の head で `alembic stamp` → `alembic upgrade head` で新規分のみ検証。本番 Postgres には `upgrade` しない（read-only の `alembic current` のみ）。
- Detection: テーブル・カラム・index を `pragma table_info` 等で確認。

### 17. 新規マイグレーション採番は handoff でなく `alembic heads` を信じる
- Date: 2026-05-22 / Trigger: handoff が「migration 0001〜0006」と書いていたため `dspengine0007` を採番したが、`alembic heads` は `dspengine0008` を返した。
- Root Cause: 別タスク #6 の未コミット WIP migration（`add_dsp_report_dimensions.py` = 0008）が `alembic/versions/` に置かれていた。alembic は untracked ファイルも走査する。`Glob "dspengine*.py"` は revision ID とファイル名が別物（記述的 slug 命名）のため空振りした。
- Mitigation: migration 作成前に必ず `python -m alembic heads` と `grep -rhn "^revision" alembic/versions/*.py` で実 revision を確認。handoff の記述は信用しない。
- Detection: `alembic heads` が単一 head か / 期待した revision か。検証時は WIP を巻き込まないよう `upgrade <自分のrevision>` で範囲限定（`upgrade head` にしない）。
