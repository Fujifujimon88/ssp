# E2E テスト教訓

## DB クエリ・SQLAlchemy 関連

### 1. ORM モデルの属性名を必ずコードで確認する
- `DeviceProfileDB.id` → 実際は `device_id` が PK
- `MdmImpressionDB.impression_id` → 実際は `id` が PK
- `AndroidDeviceDB.created_at` → 実際は `registered_at`
- **教訓**: PK 名がテーブルによって違う。クエリ書く前に db_models.py を必ず確認。

### 2. asyncio.gather で同一 DB セッション共有は禁止
- `asyncio.gather(func_a(db), func_b(db))` → `InvalidRequestError: This session is provisioning a new connection`
- **修正**: 逐次 await に変更する。
- **適用範囲**: analytics/report.py, main.py の reports/range など全箇所。

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

### 11. MDM ダッシュボードのセクション順序
- 最初の `.section h2` は「リアルタイム（今日）」セクション → index 0。
- 「代理店 Top 5」は index 1、「アフィリエイト案件 Top 5」は index 2、「主要APIエンドポイント」は index 3。
