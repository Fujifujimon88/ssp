# SSP Platform — セットアップ要件一覧

> iOS・Android 両対応、本番運用・テスト環境を含む全外部サービス・認証情報リスト

---

## 1. iOS MDM

### 1-1. Apple Developer Program

| 項目 | 内容 |
|------|------|
| アカウント種別 | Apple Developer Program（個人 or 法人） |
| 費用 | $99 / 年 |
| 取得先 | https://developer.apple.com/programs/ |
| 用途 | APNs証明書の発行権限 |

### 1-2. APNs MDM Push 証明書

| 項目 | 内容 |
|------|------|
| 発行先 | https://identity.apple.com/pushcert |
| 必要ファイル | `mdm_push.pem`（証明書）、`mdm_push_key.pem`（秘密鍵） |
| 有効期限 | 1年（毎年更新が必要） |
| 置き場所 | `/home/deploy/secrets/` |

証明書は **NanoMDMサーバー**と**FastAPI（APNs直送）**の両方で使用する。

```env
APNS_CERT_PATH=/home/deploy/secrets/mdm_push.pem
APNS_KEY_PATH=/home/deploy/secrets/mdm_push_key.pem
APNS_TOPIC=com.apple.mgmt.External.XXXXXXXX   # 証明書から自動取得
APNS_PRODUCTION=false                          # 本番は true（デフォルト: false = サンドボックス）
```

### 1-3. NanoMDM サーバー

| 項目 | 内容 |
|------|------|
| 種別 | OSS（Go製）/ 無料 |
| リポジトリ | https://github.com/micromdm/nanomdm |
| ビルド要件 | Go 1.22+ |
| 起動ポート | 9000（デフォルト） |
| 実行方式 | systemd サービスとして常駐 |

**ビルド:**
```bash
git clone https://github.com/micromdm/nanomdm.git /home/deploy/nanomdm
cd /home/deploy/nanomdm
go build -o nanomdm ./cmd/nanomdm
```

**起動コマンド（正しいフラグ）:**
```bash
./nanomdm \
  -storage file \
  -storage-path /home/deploy/nanomdm/db \
  -listen :9000 \
  -api-key <NANOMDM_API_KEY> \
  -cert /home/deploy/secrets/mdm_push.pem \
  -key /home/deploy/secrets/mdm_push_key.pem
```

> `-cert` / `-key` はNanoMDM自身がAPNsプッシュを送るために必要。`-dsn` / `-api` は古い構文なので使用しないこと。

```env
NANOMDM_URL=http://localhost:9000
NANOMDM_API_KEY=<ランダム長文字列>
MDM_SERVER_URL=https://your-domain.com/nanomdm/mdm
```

> **注意:** iOSのMDM登録にはHTTPSが必須。ドメイン + SSL証明書が先に必要。

---

## 2. Android MDM

### 2-1. Firebase Cloud Messaging (FCM)

| 項目 | 内容 |
|------|------|
| コンソール | https://console.firebase.google.com |
| 費用 | 無料枠あり（100万通/月まで無料） |
| 用途 | DPC APKへのサイレントプッシュ（コマンドポーリング起動） |

**手順:**
1. Firebaseプロジェクトを作成
2. 「プロジェクトの設定」→「サービスアカウント」→「新しい秘密鍵を生成」でJSONをダウンロード
3. JSONを `/home/deploy/secrets/firebase-service-account.json` に配置

```env
FCM_PROJECT_ID=your-firebase-project-id
FCM_SERVICE_ACCOUNT_PATH=/home/deploy/secrets/firebase-service-account.json
```

### 2-2. Android APK 署名キーストア

| 項目 | 内容 |
|------|------|
| ファイル | `dpc-release.keystore` |
| 形式 | RSA 2048-bit |
| 用途 | DPC APKのリリース署名 |

```bash
# 生成コマンド
keytool -genkey -v \
  -keystore dpc-release.keystore \
  -alias dpc \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000
```

---

## 3. LINE メッセージング

### 3-1. LINE 公式アカウント（Messaging API）

| 項目 | 内容 |
|------|------|
| 費用 | 月額 ¥5,000〜（メッセージ数による） |
| 取得先 | https://business.line.me |
| 用途 | 登録完了通知・ディーラー向け通知 |
| Webhook URL | `POST https://your-domain.com/mdm/line/webhook` |

```env
LINE_CHANNEL_ACCESS_TOKEN=<コンソールで発行した長期アクセストークン>
LINE_CHANNEL_SECRET=<チャンネルシークレット（Webhook署名検証用）>
LINE_OFFICIAL_ACCOUNT_ID=@xxxxx
```

### 3-2. エル投げ API

| 項目 | 内容 |
|------|------|
| 用途 | LINEメッセージ配信パートナーサービス |
| エンドポイント | `ERU_NAGE_API_URL/api/external/users`、`/send-message` |
| 認証 | `x-api-key` ヘッダー |

```env
ERU_NAGE_API_URL=https://insurance-recommend-eosin.vercel.app
ERU_NAGE_API_KEY=<APIキー>
```

---

## 4. 計測・アトリビューション（キャンペーンごとに設定）

### 4-1. AppsFlyer

| 項目 | 内容 |
|------|------|
| 用途 | CPI（インストール課金）S2Sポストバック |
| 設定箇所 | キャンペーン作成時に `appsflyer_dev_key` を入力 |
| エンドポイント | `https://s2s.appsflyer.com/api/v2/installs` |

### 4-2. Adjust

| 項目 | 内容 |
|------|------|
| 用途 | AppsFlyerの代替アトリビューション |
| 設定箇所 | キャンペーン作成時に `adjust_app_token` + `adjust_event_token` を入力 |

### 4-3. Google Tag Manager（オプション）

| 項目 | 内容 |
|------|------|
| 用途 | アフィリエイトLP上のコンバージョン計測 |
| 設定箇所 | キャンペーンの `gtm_container_id`（GTM-XXXXXX形式） |

---

## 5. インフラ

### 5-1. サーバー（VPS）

| 項目 | 内容 |
|------|------|
| OS | Ubuntu 22.04 LTS 推奨 |
| スペック | 2CPU+ / 4GB RAM+ |
| 参考費用 | ¥2,000〜10,000 / 月 |
| 参考サービス | さくらVPS、ConoHa、AWS、DigitalOcean |

### 5-2. ドメイン + SSL

| 項目 | 内容 |
|------|------|
| ドメイン | `mdm.example.com` 形式（A レコードをVPS IPに向ける） |
| SSL | Let's Encrypt（certbot / 無料） |
| **重要** | iOSのMDM登録・mobileconfig配信にHTTPSが**必須** |

### 5-3. データベース・キャッシュ

| 項目 | 内容 | ローカル開発 |
|------|------|-------------|
| PostgreSQL | 本番DB | SQLiteで代替可 |
| Redis | セッション・キャッシュ | 未設定でも起動可（インメモリにフォールバック） |

```env
DATABASE_URL=postgresql+asyncpg://ssp:PASSWORD@localhost:5432/ssp_platform
REDIS_URL=redis://localhost:6379
```

---

## 6. 秘密鍵ファイル構成

```
/home/deploy/secrets/
├── mdm_push.pem                   # Apple APNs証明書
├── mdm_push_key.pem               # Apple APNs秘密鍵
├── firebase-service-account.json  # Google サービスアカウント
└── dpc-release.keystore           # Android APK署名キーストア
```

---

## 7. .env 完全テンプレート

```env
# ── データベース & キャッシュ ──────────────────────────────
DATABASE_URL=postgresql+asyncpg://ssp:PASSWORD@localhost:5432/ssp_platform
REDIS_URL=redis://localhost:6379

# ── セキュリティ ──────────────────────────────────────────
SECRET_KEY=<openssl rand -hex 32 で生成 / 64文字以上>
ADMIN_API_KEY=<ランダム長文字列 / 32文字以上>

# ── アプリ設定 ────────────────────────────────────────────
APP_ENV=production
SSP_ENDPOINT=https://your-domain.com
AUCTION_TIMEOUT_MS=80
FLOOR_PRICE_DEFAULT=0.5
REVENUE_SHARE_RATE=0.70
OPENRTB_API_KEYS=key_dsp1,key_dsp2

# ── Firebase / FCM（Android プッシュ）────────────────────
FCM_PROJECT_ID=your-firebase-project-id
FCM_SERVICE_ACCOUNT_PATH=/home/deploy/secrets/firebase-service-account.json

# ── NanoMDM（iOS MDMサーバー）────────────────────────────
NANOMDM_URL=http://localhost:9000
NANOMDM_API_KEY=<ランダム長文字列>
MDM_SERVER_URL=https://your-domain.com/nanomdm/mdm

# ── APNs（iOS プッシュ証明書）────────────────────────────
APNS_CERT_PATH=/home/deploy/secrets/mdm_push.pem
APNS_KEY_PATH=/home/deploy/secrets/mdm_push_key.pem
APNS_TOPIC=com.apple.mgmt.External.XXXXXXXX
APNS_PRODUCTION=false   # 本番環境では true に変更

# ── LINE メッセージング ───────────────────────────────────
LINE_CHANNEL_ACCESS_TOKEN=<LINEコンソールで発行>
LINE_CHANNEL_SECRET=<チャンネルシークレット>
LINE_OFFICIAL_ACCOUNT_ID=@xxxxx

# ── エル投げ API ──────────────────────────────────────────
ERU_NAGE_API_URL=https://insurance-recommend-eosin.vercel.app
ERU_NAGE_API_KEY=<APIキー>
```

---

## 8. テスト環境（外部サービス不要）

以下はローカルのみで完結します：

| テスト種別 | コマンド | 外部サービス |
|-----------|---------|------------|
| E2Eテスト（Playwright） | `npm test` | 不要 |
| Unitテスト（pytest） | `pytest tests/` | 不要 |
| FCM未設定 | — | 自動スキップ（エラーなし） |
| NanoMDM未設定 | — | iOS MDM操作のみ失敗、他は正常 |

### E2Eテスト用 `.env` 最小設定

```env
BASE_URL=http://localhost:8000
ADMIN_API_KEY=change-me-admin-key
DATABASE_URL=sqlite+aiosqlite:///./test.db
```

---

## 9. 優先度別チェックリスト

### P0（必須 / これがないと動かない）

- [ ] VPS プロビジョニング（Ubuntu 22.04、2CPU+、4GB RAM+）
- [ ] ドメイン取得 + DNS設定（A レコード → VPS IP）
- [ ] Let's Encrypt SSL証明書（certbot）
- [ ] PostgreSQL インストール + DB作成
- [ ] `SECRET_KEY` / `ADMIN_API_KEY` 生成

### P1（iOS MDM）

- [ ] Apple Developer Program 登録（$99/年）
- [ ] APNs MDM Push証明書発行（`.pem` × 2）
- [ ] NanoMDM バイナリビルド（Go 1.22+）+ systemd 設定

### P1（Android MDM）

- [ ] Firebase プロジェクト作成
- [ ] サービスアカウント JSON ダウンロード
- [ ] APK 署名キーストア生成

### P2（LINE通知）

- [ ] LINE 公式アカウント（Messaging API）作成
- [ ] Webhook URL を LINE コンソールに登録
- [ ] エル投げ API キー設定

### P3（計測 / オプション）

- [ ] AppsFlyer / Adjust（キャンペーン開始時に設定）
- [ ] Google Tag Manager（LP計測が必要な場合）
- [ ] Google Play Console（DPC APK 配布時）
