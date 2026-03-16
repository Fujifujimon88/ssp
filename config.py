"""アプリケーション設定（環境変数 / .env）"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # DB - ローカル開発は SQLite、本番は PostgreSQL（.env で上書き）
    database_url: str = "sqlite+aiosqlite:///./ssp.db"

    # Redis - ローカル開発はインメモリフォールバックを使用
    redis_url: str = "redis://localhost:6379"

    # JWT
    secret_key: str = "change-me-in-production-use-long-random-string"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7日

    # オークション
    auction_timeout_ms: int = 80
    floor_price_default: float = 0.5

    # 管理者認証（.env で上書き必須）
    admin_api_key: str = "change-me-admin-key"

    # アプリ
    app_env: str = "development"
    ssp_endpoint: str = "http://localhost:8000"
    revenue_share_rate: float = 0.70

    # LINE Messaging API（.env で設定）
    line_channel_access_token: str = ""
    line_channel_secret: str = ""
    line_official_account_id: str = ""  # @xxxxx 形式

    # エル投げ API（.env で設定）
    eru_nage_api_url: str = "https://api.eru-nage.example.com"
    eru_nage_api_key: str = ""

    # FCM（Firebase Cloud Messaging）Android Push通知 - HTTP v1 API
    fcm_project_id: str = ""           # Firebase プロジェクトID（例: my-project-12345）
    fcm_service_account_path: str = "" # サービスアカウントJSONファイルのパス

    # NanoMDM サーバー設定
    nanomdm_url: str = "http://localhost:9000"        # NanoMDMのURL（FastAPIと別プロセス）
    nanomdm_api_key: str = "change-me-nanomdm-key"  # NanoMDM APIキー

    # APNs（Apple Push Notification Service）iOS MDM通知
    apns_cert_path: str = ""    # MDMプッシュ証明書 .pem ファイルパス
    apns_key_path: str = ""     # MDMプッシュ秘密鍵 .pem ファイルパス
    apns_topic: str = ""        # com.apple.mgmt.External.XXXXXXXX（証明書から取得）
    apns_production: bool = False  # True=本番, False=サンドボックス

    # iOS MDM設定
    mdm_server_url: str = ""    # https://mdm.example.com/nanomdm/mdm（NanoMDMのMDMエンドポイント）
    mdm_push_magic: str = ""    # （デバイスチェックイン後に取得）
    app_bundle_id: str = "com.example.ssp"    # App Clips用バンドルID


settings = Settings()
