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
ADMIN_API_KEY=change-me-admin-key
```

- `BASE_URL` : FastAPI サーバーのベースURL（デフォルト: `http://127.0.0.1:8000`）
- `TEST_DOMAIN` : テスト用パブリッシャーのドメイン（global-setup が自動登録）
- `TEST_PASSWORD` : テスト用パブリッシャーのパスワード
- `ADMIN_API_KEY` : サーバー側 `.env` の `ADMIN_API_KEY` と同じ値（デフォルト: `change-me-admin-key`）

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

### login.spec.js（4テスト）
| テスト | 概要 |
|--------|------|
| ログインページが表示される | タイトル・フォーム確認 |
| 誤認証情報でエラーメッセージ | #error-msg 表示確認 |
| 正しい認証情報でダッシュボードにリダイレクト | ログインフロー確認 |
| ログイン後に ssp_token が保存される | localStorage 確認 |

### dashboard.spec.js（9テスト）- /admin エンドポイント
| テスト | 概要 |
|--------|------|
| ページが表示される | タイトル確認 |
| ヘッダーにSSP Platformロゴ | ヘッダー内容確認 |
| KPIカード4つ | グリッド構造確認 |
| パブリッシャー一覧セクション | #publishers 表示確認 |
| 新規登録ボタン | ボタン表示確認 |
| 新規登録モーダルが開く | モーダル動作確認 |
| モーダルが×で閉じる | モーダルクローズ確認 |
| DSP接続状態表示 | /health API連携確認 |
| /health がokを返す | API疎通確認 |
| /api/admin/stats の必要フィールド確認 | 管理統計API |
| /api/admin/stats hourly は長さ24 | 時間帯別データ |
| キーなしで /api/admin/stats は401 | 認証必須確認 |
| KPIが管理画面に表示される | UI連携確認 |

### portal.spec.js（8テスト）- /dashboard パブリッシャーポータル
| テスト | 概要 |
|--------|------|
| 未ログインで /login にリダイレクト | 認証ガード確認 |
| ログイン済みでダッシュボード表示 | ページ表示確認 |
| ヘッダーにパブリッシャー名 | API連携確認 |
| KPIカード4つ | グリッド確認 |
| スロット管理ナビ切り替え | ナビ動作確認 |
| DSP連携セクション表示 | DSPリスト確認 |
| プロフィールにAPIキー表示 | API確認 |
| ログアウトで /login リダイレクト | ログアウトフロー |
| スロット作成モーダルUI | UI作成フロー |
| /api/dsp/stats が配列を返す | DSP統計API |
| /admin が表示される | 管理画面表示 |

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

### slots.spec.js（7テスト）
| テスト | 概要 |
|--------|------|
| スロット一覧取得 | GET /api/slots |
| トークンなしで401 | 認証必須確認 |
| 新スロット作成 | POST /api/slots |
| 作成スロットが一覧に出る | 作成後確認 |
| Prebid.jsタグ取得 | GET /api/slots/:id/tag |
| 他パブリッシャーへは403 | 権限確認 |
| 全スロットタグ一括取得 | GET /api/tags/full |

### admin.spec.js（8テスト）
| テスト | 概要 |
|--------|------|
| pending → active に変更 | ステータス変更 |
| active → suspended に変更 | ステータス変更 |
| suspended → active に戻す | ステータス変更 |
| 不正なステータスは400 | バリデーション確認 |
| 存在しないパブリッシャーは404 | エラー確認 |
| /api/reports/range が配列を返す | 期間レポートAPI |
| days=14 で 14件返る | レポート件数確認 |
| 各レコードに必要フィールド | フィールド確認 |
| トークンなしで401 | 認証必須確認 |
| レポートセクションUI切り替え | UI確認 |

### slots_advanced.spec.js（8テスト）
| テスト | 概要 |
|--------|------|
| スロット作成して停止 | DELETE /api/slots/:id |
| 停止後はスロット一覧に active=false | 停止確認 |
| 存在しないスロット削除は404 | エラー確認 |
| 本日レポート取得 | GET /api/reports/daily |
| date パラメータで特定日取得 | 日付指定レポート |
| トークンなしで401 | 認証必須確認 |
| 14日切り替えUI | レポートUI |
| 30日切り替えUI | レポートUI |

### bid.spec.js（7テスト）
| テスト | 概要 |
|--------|------|
| 入札リクエストが bids 配列を返す | POST /v1/bid |
| 落札時に winToken が含まれる | winToken 確認 |
| publisherId なしで400 | バリデーション確認 |
| 有効な winToken で ok が返る | GET /v1/win |
| 無効トークンで404 | エラー確認 |
| 有効トークンで HTML が返る | GET /v1/ad/:token |
| 無効トークンで404 | エラー確認 |

### security_validation.spec.js（10テスト）
| テスト | 概要 |
|--------|------|
| DSP連携セクションにDSP名表示 | UI確認 |
| /health がDSPリストを返す | API確認 |
| /api/dsp/stats のフィールド確認 | DSP統計API |
| 他パブリッシャーIDでスロット作成は403 | 権限確認 |
| 他パブリッシャーのスロット削除は404 | 権限確認 |
| トークンなしで /api/slots は401 | 認証確認 |
| トークンなしでDELETE /api/slots/:id は401 | 認証確認 |
| 必須フィールド欠落は422 | バリデーション確認 |
| width に文字列は422 | 型バリデーション確認 |
| /auth/register で必須フィールド欠落は422 | 登録バリデーション確認 |

### auction_logic.spec.js（6テスト）
| テスト | 概要 |
|--------|------|
| フロアプライス超過（999）は bids 空 | オークションロジック確認 |
| フロアプライス 0.01 で高確率落札 | 落札確率確認 |
| 落札CPMはフロアプライス以上 | 価格ロジック確認 |
| winToken は /v1/win 使用後に再利用不可 | トークン使い捨て確認 |
| mock-slow でも /v1/bid が500ms以内 | タイムアウト確認 |
| /api/reports/range?days=1 は1件返す | レポート件数確認 |
| 未来日付のレポートは impressions=0 | エッジケース確認 |

### mdm_affiliate.spec.js（5テスト）- GTM LP・アフィリエイト案件
| テスト | 概要 |
|--------|------|
| 存在しないキャンペーンIDで404 | GET /mdm/lp/:id エラー確認 |
| アフィリエイト案件一覧取得 | GET /mdm/admin/affiliate/campaigns |
| 認証なしで401 | 認証必須確認 |
| 新規案件登録で200 + id返る | POST /mdm/admin/affiliate/campaigns |
| 認証なし POST で401 | 認証必須確認 |

### mdm_dashboard.spec.js（10テスト）- MDM管理ダッシュボード
| テスト | 概要 |
|--------|------|
| 認証なしで401 | GET /mdm/admin/dashboard 認証確認 |
| 有効な管理者キーでダッシュボード表示 | UI確認 |
| KPIカードが8枚表示される | UI確認 |
| 代理店 Top 5 テーブルセクション表示 | UI確認 |
| アフィリエイト案件 Top 5 セクション表示 | UI確認 |
| 主要APIエンドポイント一覧セクション表示 | UI確認 |
| アフィリエイトレポートAPIで200 + フィールド確認 | GET /mdm/admin/affiliate/report |
| year/month 指定で period 確認 | クエリパラメータ確認 |
| 認証なしで401 | 認証必須確認 |
| CV一覧取得 | GET /mdm/admin/affiliate/conversions |

### p2_endpoints.spec.js（38テスト）- P2 新規エンドポイント群
| テスト | 概要 |
|--------|------|
| game_start イベントを記録（印象IDなし） | ADT-02: ゲームイベント記録 |
| game_complete イベントを記録 | ADT-02: ゲームイベント記録 |
| game_converted イベントを記録 | ADT-02: ゲームイベント記録 |
| 不正なイベント名は400 | ADT-02: バリデーション確認 |
| event 空文字は400 | ADT-02: バリデーション確認 |
| 有効な DSP APIキーで入札が通る | ADT-03: POST /openrtb/bid |
| APIキーなしで401 | ADT-03: 認証確認 |
| 不正な APIキーで401 | ADT-03: 認証確認 |
| 2つ目の許可済み DSP キーで入札 | ADT-03: 複数キー対応確認 |
| Win notice を受け付ける | ADT-03: GET /openrtb/win/:id |
| price=0 でも Win notice が成功 | ADT-03: エッジケース確認 |
| 管理者キーで代理店を登録できる | BKD-11: POST /mdm/admin/agencies |
| 管理者キーなしで401 | BKD-11: 認証確認 |
| name フィールドなしで400 | BKD-11: バリデーション確認 |
| 管理者キーで代理店一覧取得 | BKD-11: GET /mdm/admin/agencies |
| 管理者キーなしで401 | BKD-11: 認証確認 |
| 登録した代理店が一覧に含まれる | BKD-11: 作成後確認 |
| 有効な代理店キーでデバイス一覧取得 | BKD-11: GET /mdm/agency/devices |
| 代理店キーなしで401 | BKD-11: 認証確認 |
| 不正な代理店キーで403 | BKD-11: 認証確認 |
| 有効な代理店キーで収益レポート取得 | BKD-11: GET /mdm/agency/revenue |
| month クエリパラメータで特定月指定 | BKD-11: 月指定確認 |
| 代理店キーなしで401 | BKD-11: 認証確認 |
| 管理者キーで精算を開始できる | BKD-12: POST /mdm/admin/settlement/run |
| period_month 省略時は前月が自動設定 | BKD-12: デフォルト挙動 |
| 管理者キーなしで401 | BKD-12: 認証確認 |
| 管理者キーで精算一覧取得 | BKD-12: GET /mdm/admin/settlement/invoices |
| period_month フィルタが動作する | BKD-12: フィルタ確認 |
| 管理者キーなしで401 | BKD-12: 認証確認 |
| 管理者キーで学習をキューできる | ML-02: POST /mdm/admin/ml/train |
| 管理者キーなしで401 | ML-02: 認証確認 |
| 管理者キーでモデル一覧取得 | ML-02: GET /mdm/admin/ml/models |
| 管理者キーなしで401 | ML-02: 認証確認 |
| 管理者キーでコホート計算をキュー | ML-03: POST /mdm/admin/ml/compute_cohorts |
| 管理者キーなしで401 | ML-03: 認証確認 |
| 管理者キーでコホート統計取得 | ML-03: GET /mdm/admin/ml/cohort_stats |
| コホートが存在する場合は各フィールドを持つ | ML-03: フィールド確認 |
| 管理者キーなしで401 | ML-03: 認証確認 |

## グローバルセットアップについて

`global-setup.js` が自動的に以下を行います：
1. `TEST_DOMAIN` でログイン試行
2. 失敗した場合は新規パブリッシャーを登録
3. JWTトークンを `e2e/.auth/user.json` に保存

`e2e/.auth/user.json` は `.gitignore` で除外されています。
