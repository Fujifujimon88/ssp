"""
パブリッシャー・広告スロットのデータモデル（Pydantic）
DB永続化はSQLAlchemy/asyncpgで別途実装可能
"""
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, HttpUrl


class AdFormat(str, Enum):
    BANNER = "banner"
    VIDEO = "video"
    NATIVE = "native"


class PublisherStatus(str, Enum):
    PENDING = "pending"      # 審査中
    ACTIVE = "active"        # 配信中
    SUSPENDED = "suspended"  # 停止


# ── パブリッシャー ─────────────────────────────────────────────

class PublisherCreate(BaseModel):
    name: str                         # サイト名
    domain: str                       # example.com
    contact_email: str
    site_category: list[str] = []     # IABカテゴリ ["IAB1", "IAB17"]
    floor_price: float = 0.5          # 最低CPM(USD)


class Publisher(PublisherCreate):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: PublisherStatus = PublisherStatus.PENDING
    api_key: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    monthly_revenue_usd: float = 0.0


# ── 広告スロット ───────────────────────────────────────────────

class AdSlotCreate(BaseModel):
    publisher_id: str
    name: str                          # "トップバナー" など
    format: AdFormat = AdFormat.BANNER
    width: Optional[int] = None
    height: Optional[int] = None
    sizes: list[list[int]] = []        # [[300,250],[728,90]] — 複数サイズ対応
    floor_price: Optional[float] = None  # Noneの場合はパブリッシャー設定を使用
    position: Optional[int] = None     # IAB広告位置コード


class AdSlot(AdSlotCreate):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tag_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    impressions_today: int = 0
    revenue_today_usd: float = 0.0

    def effective_sizes(self) -> list[list[int]]:
        """sizes が空の場合は width/height から1サイズを返す"""
        if self.sizes:
            return self.sizes
        w, h = self.width or 300, self.height or 250
        return [[w, h]]


# ── レポート ───────────────────────────────────────────────────

class DailyReport(BaseModel):
    publisher_id: str
    date: str                   # YYYY-MM-DD
    impressions: int
    fill_rate: float            # 広告が表示された割合
    revenue_usd: float
    ecpm: float                 # 実効CPM = revenue / impressions * 1000
    top_dsp: Optional[str] = None
