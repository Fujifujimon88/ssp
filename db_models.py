"""SQLAlchemy ORMモデル（PostgreSQLテーブル定義）"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class PublisherDB(Base):
    __tablename__ = "publishers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    contact_email: Mapped[str] = mapped_column(String(255))
    site_category: Mapped[str] = mapped_column(String(500), default="")  # JSON文字列
    floor_price: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    api_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    monthly_revenue_usd: Mapped[float] = mapped_column(Float, default=0.0)

    slots: Mapped[list["AdSlotDB"]] = relationship("AdSlotDB", back_populates="publisher")


class AdSlotDB(Base):
    __tablename__ = "ad_slots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    publisher_id: Mapped[str] = mapped_column(String(36), ForeignKey("publishers.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    format: Mapped[str] = mapped_column(String(20), default="banner")
    width: Mapped[int] = mapped_column(Integer, nullable=True)
    height: Mapped[int] = mapped_column(Integer, nullable=True)
    floor_price: Mapped[float] = mapped_column(Float, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=True)
    tag_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    publisher: Mapped["PublisherDB"] = relationship("PublisherDB", back_populates="slots")
    impressions: Mapped[list["ImpressionDB"]] = relationship("ImpressionDB", back_populates="slot")


class ImpressionDB(Base):
    __tablename__ = "impressions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    auction_id: Mapped[str] = mapped_column(String(36), index=True)
    imp_id: Mapped[str] = mapped_column(String(36))
    slot_id: Mapped[str] = mapped_column(String(36), ForeignKey("ad_slots.id"), index=True)
    publisher_id: Mapped[str] = mapped_column(String(36), ForeignKey("publishers.id"), index=True)
    winning_dsp: Mapped[str] = mapped_column(String(100), nullable=True)
    clearing_price: Mapped[float] = mapped_column(Float, default=0.0)
    bid_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    filled: Mapped[bool] = mapped_column(Boolean, default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    slot: Mapped["AdSlotDB"] = relationship("AdSlotDB", back_populates="impressions")
