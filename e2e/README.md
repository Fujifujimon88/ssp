# E2E テスト (Playwright)

## 前提条件

- Node.js 18+
- FastAPI サーバーが `http://localhost:8000` で起動していること

## セットアップ

```bash
# .env.test を作成（初回のみ）
cp .env.test.example .env.test   # または手動で作成
# npm依存のインストール（初回のみ）
npm install
npx playwright install chromium
```

### .env.test の書き方

```env
BASE_URL=http://localhost:8000
TEST_DOMAIN=e2e-test.example.com
TEST_PASSWORD=e2eTestPass123
```

> `#` を含むパスワードはダブルクォートで囲む: `TEST_PASSWORD="pass#123"`

## テスト実行

```bash
# ヘッドレス（CI向け）
npm run test:e2e

# ブラウザ表示あり（デバッグ向け）
npm run test:e2e:headed

# Playwright UI モード（インタラクティブ）
npm run test:e2e:ui

# 特定ファイルのみ
npx playwright test e2e/dashboard.spec.js
```

## テスト一覧

### dashboard.spec.js（10テスト）
| テスト | 概要 |
|--------|------|
| ページが表示される | タイトル確認 |
| ヘッダーにSSP Platformロゴ | ヘッダー内容確認 |
| ナビゲーションリンク | 3メニュー項目確認 |
| KPIカード4つ | グリッド構造確認 |
| パブリッシャー一覧セクション | #publishers 表示確認 |
| 新規登録ボタン | ボタン表示確認 |
| 新規登録モーダルが開く | モーダル動作確認 |
| モーダルが×で閉じる | モーダルクローズ確認 |
| DSP接続状態表示 | /health API連携確認 |
| /health がokを返す | API疎通確認 |

### publisher.spec.js（7テスト）
| テスト | 概要 |
|--------|------|
| JWTトークン取得 | 正常ログイン |
| 誤パスワードで401 | 認証エラー確認 |
| 存在しないドメインで401 | 認証エラー確認 |
| /api/publishers/me 取得 | JWT認証付きAPI |
| トークンなしで401 | 認証必須確認 |
| 重複ドメインでエラー表示 | UIエラー確認 |
| 新規パブリッシャー登録 | UI登録フロー |

### slots.spec.js（6テスト）
| テスト | 概要 |
|--------|------|
| スロット一覧取得 | GET /api/slots |
| トークンなしで401 | 認証必須確認 |
| 新スロット作成 | POST /api/slots |
| 作成スロットが一覧に出る | 作成後確認 |
| Prebid.jsタグ取得 | GET /api/slots/:id/tag |
| 他パブリッシャーへは403 | 権限確認 |
| 全スロットタグ一括取得 | GET /api/tags/full |

## 追加すべきテスト（TODO）

- [ ] 日次レポートAPI (`GET /api/reports/daily`)
- [ ] 入札エンドポイント (`POST /v1/bid`)
- [ ] 落札通知 (`GET /v1/win`)
- [ ] パブリッシャーのステータス変更
- [ ] スロット無効化 (active=false)
- [ ] レスポンシブ表示（モバイルビューポート）

## グローバルセットアップについて

`global-setup.js` が自動的に以下を行います：
1. `TEST_DOMAIN` でログイン試行
2. 失敗した場合は新規パブリッシャーを登録
3. JWTトークンを `e2e/.auth/user.json` に保存

`e2e/.auth/user.json` は `.gitignore` で除外されています。
