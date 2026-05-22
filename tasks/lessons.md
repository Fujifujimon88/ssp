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

### 18. `git add <path>` は並行セッションの未コミット変更も巻き込む
- Date: 2026-05-22 / Trigger: 別の Claude セッションが #6 を並行作業中、`git add db_models.py` で #6 の未コミット編集を自分の #3 commit に混入。さらに `git reset HEAD~1` を `git log` 未確認で実行し、その間に積まれた並行セッションの #6 commit を branch から外した（reflog で復元）。
- Root Cause: `git add <path>` はその時点の working tree 全体をステージし、自分が編集した行だけを選ばない。ssp_platform は複数 Claude セッションが並行することがあり working tree は単独占有でない。
- Mitigation: commit 直前に必ず `git diff --cached` で差分が自分のものだけか確認。`git reset`/`rebase` 前に `git log --oneline -5` と `git reflog -5` で HEAD の実体を確認してから実行。
- Detection: `git show --stat` の変更行数が想定と乖離 / `git reflog` に身に覚えのない commit が出現。

### 19. マイグレーションの inspector は DDL の後に再取得する
- Date: 2026-05-22 / Trigger: #7 migration（dspengine0009）で `op.create_table("dsp_creatives")` 後の backfill `INSERT...SELECT` が 0 件。`if insp.has_table("dsp_creatives")` が False で skip されていた。
- Root Cause: `insp = inspect(conn)` を upgrade() 冒頭で1度だけ生成し使い回した。inspector は生成時点のスキーマをキャッシュするため、同一 upgrade 内で作成したテーブル/インデックスを認識しない。has_table/has_index/backfill ガードが全て空振りする。
- Mitigation: create_table / add_column 等の DDL の後、index 作成や backfill の前に `insp = inspect(conn)` を再取得する。冒頭の inspector は「DDL 前の状態」専用と割り切る。
- Detection: populated DB コピーで upgrade 後、新テーブルの行数・インデックスを `pragma` で必ず確認（教訓16 と併用）。

### 20. worktree 隔離エージェントの base はセッション初期 commit になりうる
- Date: 2026-05-22 / Trigger: test-first-implement #8-2 で Agent worktree isolation が worktree を session 開始時の commit（422abd7）から作成。session 中に master へ merge した #8（95ddb0a）を含まず、Red エージェントが「#8 が未実装」と誤認してテストを書いた。
- Root Cause: `isolation: worktree` はセッション初期 base から worktree を作る場合があり、セッション中に親 working tree へ重ねた commit を自動では取り込まない。
- Mitigation: worktree 隔離エージェントの初回報告で `git log --oneline` の親 commit を確認。base が現行 master とずれていたら `git -C <worktree> rebase master` で載せ替えてから次段へ進む（新規ファイル中心の commit なら衝突しない）。
- Detection: 完了報告の `git log` 親 commit / 「既存実装が見当たらない」等の想定外報告。

### 21. Redis レート制限カウンタの EXPIRE は初回 INCR 時のみ付与する
- Date: 2026-05-22 / Trigger: #8-2 の `incr_click_counters` が INCR の度に EXPIRE を呼び、連打が続く限り TTL がリセットされ続けて固定ウィンドウが無限延長（本番でレート制限が実質無効）。Reviewer が HIGH 指摘。
- Root Cause: INCR+EXPIRE をセットで毎回呼ぶとウィンドウがスライドし続ける。固定ウィンドウ方式は初回（カウント==1）のみ TTL を付ける必要がある。
- Mitigation: `count = await redis.incr(key); if count == 1: await redis.expire(key, window)`。
- Detection: テストの FakeRedis は TTL 挙動を検証しないため pass しても本番で破綻する。レビュー時に Redis カウンタの EXPIRE 呼び出し位置を目視確認する。

### 22. 「記録するが集計から除外」系の機能はスキーマ制約を計画段階で確認する
- Date: 2026-05-22 / Trigger: #9 のアトリビューション窓で「窓外 CV は記録しつつ ROAS 非算入・migration なし」を計画前提にしたが、`DspConversionEventDB.campaign_id` が非 nullable + `record_conversion` が campaign_id 必須のため campaign_id を落とす実装が不可能。Reviewer が HIGH 指摘。
- Root Cause: 計画段階で「未アトリビュートで記録する」表現方法（campaign_id を None にする想定）が現スキーマで成立するか、対象カラムの NULL 制約・必須バリデーション・集計クエリの WHERE 条件を確認していなかった。
- Mitigation: 「記録はするが集計から除外する」系の機能は、計画時に対象テーブルの NULL 制約・必須カラム・集計クエリを確認し、除外フラグカラム追加（migration）の要否を先に判断する。
- Detection: Reviewer が「テストは通るが plan の意図と矛盾」を指摘。弱いテスト（impression_id だけ検証）は意図不一致を見逃すため、集計結果そのものを検証するテストを書く。

### 23. タスク着手前に「他セッションが同じ作業をしていないか」を git log で確認する
- Date: 2026-05-23 / Trigger: dsp_engine セキュリティ修正3件を test-first-implement で実装し終えた段で、別の Claude セッションが**同じ3修正を先に master へマージ済み**（commit 842683b/f1f6589/d998006）と判明。worktree の全作業が重複。
- Root Cause: 着手対象を未追跡の plan ファイル（`tasks/plan-dsp-security-fixes.md`）から拾ったが、ssp_platform は複数セッションが並行する（[[feedback-concurrent-sessions]]）のに、着手前に「そのタスクが既に進行中/完了済みでないか」を確認しなかった。タスクの「着手中」マーカーがどこにも無い。
- Mitigation: タスク着手前に `git log --oneline -20 origin/master..master` と handoff/progress の状態を確認。長時間タスクは着手時に handoff へ「進行中」を記録し、worktree マージ直前に再度 `git log` で master の差分を確認する。
- Detection: worktree を master に rebase/merge しようとした時の `git log <base>..master` に同件名 commit が出現。Reviewer の main_unexpected_commits 検知。

### 24. 共有 working tree では `git checkout -b` は隔離にならない（commit 着地先を毎回確認）
- Date: 2026-05-23 / Trigger: run_report バグ修正で `fix/run-report-jst-day` を作成したが、並行セッションが同じ working tree で `git checkout master` したため、以降の Red/Green commit が branch でなく master へ着地した。
- Root Cause: ssp_platform は複数セッションが単一 working tree を共有する（[[feedback-concurrent-sessions]]）。branch は working tree 単位の状態で、他セッションの checkout で自セッションの HEAD が無断移動する。`checkout -b` は隔離を与えない。
- Mitigation: 真の隔離が要るなら Agent の `isolation: worktree`。共有 working tree で作業するなら `git commit` 直前に毎回 `git branch --show-current` で着地先を確認する。
- Detection: `git commit` 出力の `[<branch> <hash>]` が想定 branch と違う / `git log` に並行セッションの commit が割り込む。

### 24. 日付依存テストは UTC で統一する（`date.today()` ローカル日付を使わない）
- Date: 2026-05-23 / Trigger: `test_dsp_reporting.py` の run_report 系2件が、日付をまたいだ実行（JST 早朝＝UTC 前日）で失敗。前日は通っていた。
- Root Cause: テストが `date.today()`（マシンのローカル日付）でレポート期間を絞る一方、`DspSpendLogDB.logged_at` の既定値は `datetime.now(timezone.utc)`。JST と UTC の日付境界でデータが期間外になる。
- Mitigation: 日付依存テストはデータ側のタイムゾーンに合わせる。UTC 既定のデータには `datetime.now(timezone.utc).date()` で絞る。`date.today()`（ローカル）は使わない。
- Detection: 「前日まで通っていたテストが日付変更後に失敗」。CI とローカルでタイムゾーンが違うと再現条件がずれる。
