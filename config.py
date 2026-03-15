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


settings = Settings()
