"""共通ユーティリティ"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """現在のUTC時刻をnaive datetimeで返す（SQLAlchemy naive DateTime カラム用）"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
