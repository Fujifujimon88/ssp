# 本番デプロイ手順書

> 対象: VPS（Ubuntu 22.04 / さくら or ConoHa）+ 独自ドメイン
> 目標: HTTPS + FastAPI + PostgreSQL + NanoMDM を1台で動かす

---

## ステップ 1 — VPS 初期設定

```bash
# 非rootユーザー作成
adduser deploy
usermod -aG sudo deploy
su - deploy

# 必要パッケージ
sudo apt update && sudo apt install -y \
  python3.11 python3.11-venv python3-pip \
  postgresql postgresql-contrib nginx certbot python3-certbot-nginx \
  git curl unzip
```

---

## ステップ 2 — PostgreSQL 設定 (#5)

```bash
sudo -u postgres psql <<EOF
CREATE USER ssp WITH PASSWORD 'strong-password-here';
CREATE DATABASE ssp_platform OWNER ssp;
GRANT ALL PRIVILEGES ON DATABASE ssp_platform TO ssp;
EOF
```

`.env` に設定:
```
DATABASE_URL=postgresql+asyncpg://ssp:strong-password-here@localhost:5432/ssp_platform
```

DBマイグレーション（初回のみ）:
```bash
cd /home/deploy/ssp_platform
python -c "
import asyncio
from db_models import Base
from sqlalchemy.ext.asyncio import create_async_engine
engine = create_async_engine('postgresql+asyncpg://ssp:strong-password-here@localhost:5432/ssp_platform')
asyncio.run(engine.run_sync(Base.metadata.create_all))
print('Tables created.')
"
```

---

## ステップ 3 — アプリデプロイ (#3)

```bash
cd /home/deploy
git clone https://github.com/yourorg/ssp_platform.git
cd ssp_platform
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# .env を作成（下記テンプレート参照）
cp .env.example .env
nano .env
```

### `.env` テンプレート
```env
DATABASE_URL=postgresql+asyncpg://ssp:strong-password-here@localhost:5432/ssp_platform
REDIS_URL=redis://localhost:6379
SECRET_KEY=<長いランダム文字列: openssl rand -hex 32>
ADMIN_API_KEY=<長いランダム文字列>
APP_ENV=production
SSP_ENDPOINT=https://mdm.example.com

# FCM v1 (Android Push)
FCM_PROJECT_ID=your-firebase-project-id
FCM_SERVICE_ACCOUNT_PATH=/home/deploy/secrets/firebase-service-account.json

# NanoMDM
NANOMDM_URL=http://localhost:9000
NANOMDM_API_KEY=<長いランダム文字列>

# APNs (iOS)
APNS_CERT_PATH=/home/deploy/secrets/mdm_push.pem
APNS_KEY_PATH=/home/deploy/secrets/mdm_push_key.pem
APNS_TOPIC=com.apple.mgmt.External.XXXXXXXX
APNS_PRODUCTION=true
MDM_SERVER_URL=https://mdm.example.com/nanomdm/mdm
```

### systemd サービス
```bash
sudo tee /etc/systemd/system/ssp.service > /dev/null <<'EOF'
[Unit]
Description=SSP Platform FastAPI
After=network.target postgresql.service

[Service]
User=deploy
WorkingDirectory=/home/deploy/ssp_platform
ExecStart=/home/deploy/ssp_platform/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
EnvironmentFile=/home/deploy/ssp_platform/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ssp
sudo systemctl start ssp
sudo systemctl status ssp
```

---

## ステップ 4 — HTTPS + Nginx (#2)

### ドメイン取得後（例: mdm.example.com）

```bash
# Nginx 設定
sudo tee /etc/nginx/sites-available/ssp <<'EOF'
server {
    listen 80;
    server_name mdm.example.com;
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    server_name mdm.example.com;

    ssl_certificate /etc/letsencrypt/live/mdm.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mdm.example.com/privkey.pem;

    # MDM .mobileconfig は Content-Type が重要
    location /mdm/enroll/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        add_header Content-Type "application/x-apple-aspen-config";
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/ssp /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# SSL証明書（Let's Encrypt）
sudo certbot --nginx -d mdm.example.com --non-interactive --agree-tos -m admin@example.com
```

---

## ステップ 5 — NanoMDM デプロイ (#9)

```bash
# Go インストール
curl -OL https://go.dev/dl/go1.22.0.linux-amd64.tar.gz
sudo tar -C /usr/local -xzf go1.22.0.linux-amd64.tar.gz
export PATH=$PATH:/usr/local/go/bin

# NanoMDM ビルド
git clone https://github.com/micromdm/nanomdm.git /home/deploy/nanomdm
cd /home/deploy/nanomdm
go build -o nanomdm ./cmd/nanomdm

# NanoMDM systemd
sudo tee /etc/systemd/system/nanomdm.service > /dev/null <<'EOF'
[Unit]
Description=NanoMDM Server
After=network.target

[Service]
User=deploy
WorkingDirectory=/home/deploy/nanomdm
ExecStart=/home/deploy/nanomdm/nanomdm \
  -storage file \
  -storage-path /home/deploy/nanomdm/db \
  -listen :9000 \
  -api-key NANOMDM_API_KEY_HERE \
  -cert /home/deploy/secrets/mdm_push.pem \
  -key /home/deploy/secrets/mdm_push_key.pem
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable nanomdm
sudo systemctl start nanomdm
```

---

## ステップ 6 — Android APK ビルド (#4)

```bash
# SERVER_URL を本番URLに指定してビルド
cd android-dpc
./gradlew assembleRelease -PSERVER_URL=https://mdm.example.com

# APK署名（keystore未作成の場合）
keytool -genkey -v -keystore dpc-release.keystore -alias dpc -keyalg RSA -keysize 2048 -validity 10000
# → prompts で情報入力

jarsigner -verbose -sigalg SHA256withRSA -digestalg SHA-256 \
  -keystore dpc-release.keystore \
  app/build/outputs/apk/release/app-release-unsigned.apk dpc

zipalign -v 4 app-release-unsigned.apk app-release-signed.apk
```

ビルドしたAPKを `ssp_platform/static/dpc-latest.apk` に配置して `/mdm/enroll/android` からダウンロードできるようにする。

---

## APNs MDM証明書取得 (#10)

1. Apple Developer Portal (https://developer.apple.com) にサインイン
2. Certificates > MDM CSR を作成
3. Apple Push Certificates Portal (https://identity.apple.com) でアップロード → .pem をダウンロード
4. `/home/deploy/secrets/mdm_push.pem` に配置

費用: $99/年（Apple Developer Program）

---

## チェックリスト

- [ ] VPS契約 + SSH鍵設定
- [ ] ドメイン取得 + Aレコード設定
- [ ] PostgreSQL 作成 + マイグレーション
- [ ] アプリデプロイ + systemd 起動
- [ ] HTTPS + Nginx設定 + certbot
- [ ] .env 本番値を設定
- [ ] NanoMDM デプロイ（iOS使う場合）
- [ ] APNs証明書取得（iOS使う場合）
- [ ] Android APK ビルド + 署名 + アップロード
- [ ] 動作確認: QRスキャン → エンロール → 広告表示
