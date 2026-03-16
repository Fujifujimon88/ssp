"""SQLAlchemy ORMモデル（PostgreSQLテーブル定義）"""
import uuid
from datetime import datetime, timezone

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
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
    sizes: Mapped[str] = mapped_column(Text, default="")  # JSON: [[300,250],[728,90]]
    tag_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

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
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    slot: Mapped["AdSlotDB"] = relationship("AdSlotDB", back_populates="impressions")


# ── MDM テーブル ──────────────────────────────────────────────


class DealerDB(Base):
    """携帯代理店・店舗"""
    __tablename__ = "dealers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    store_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    address: Mapped[str] = mapped_column(String(500), nullable=True)
    api_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=_uuid)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    devices: Mapped[list["DeviceDB"]] = relationship("DeviceDB", back_populates="dealer")


class CampaignDB(Base):
    """エンロールキャンペーン（VPN設定・Webクリップ設定をまとめたもの）"""
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    dealer_id: Mapped[str] = mapped_column(String(36), ForeignKey("dealers.id"), index=True, nullable=True)
    vpn_config: Mapped[str] = mapped_column(Text, nullable=True)    # JSON: VPN設定
    webclips: Mapped[str] = mapped_column(Text, nullable=True)       # JSON: Webクリップ設定リスト
    eru_nage_scenario_id: Mapped[str] = mapped_column(String(100), nullable=True)  # エル投げシナリオID
    line_liff_url: Mapped[str] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class DeviceDB(Base):
    """エンロール済みデバイス"""
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    enrollment_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=_uuid)
    dealer_id: Mapped[str] = mapped_column(String(36), ForeignKey("dealers.id"), index=True, nullable=True)
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"), index=True, nullable=True)
    platform: Mapped[str] = mapped_column(String(10), default="unknown")  # ios / android / unknown
    device_model: Mapped[str] = mapped_column(String(200), nullable=True)
    os_version: Mapped[str] = mapped_column(String(50), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(500), nullable=True)
    line_user_id: Mapped[str] = mapped_column(String(100), index=True, nullable=True)
    age_group: Mapped[str] = mapped_column(String(10), nullable=True)   # 10s/20s/30s/40s
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    mobileconfig_downloaded: Mapped[bool] = mapped_column(Boolean, default=False)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/active/unenrolled

    dealer: Mapped["DealerDB"] = relationship("DealerDB", back_populates="devices")


# ── アフィリエイト テーブル ────────────────────────────────────


class AffiliateCampaignDB(Base):
    """アフィリエイト案件（VPN・アプリ・EC等）"""
    __tablename__ = "affiliate_campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50), default="app")  # app/vpn/ec/finance
    destination_url: Mapped[str] = mapped_column(String(500))          # 最終リンク先
    reward_type: Mapped[str] = mapped_column(String(10), default="cpi") # cpi/cps/cpl
    reward_amount: Mapped[float] = mapped_column(Float, default=0.0)    # 円
    # 計測ツール連携
    appsflyer_dev_key: Mapped[str] = mapped_column(String(200), nullable=True)
    adjust_app_token: Mapped[str] = mapped_column(String(200), nullable=True)
    gtm_container_id: Mapped[str] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    clicks: Mapped[list["AffiliateClickDB"]] = relationship("AffiliateClickDB", back_populates="campaign")
    creatives: Mapped[list["CreativeDB"]] = relationship("CreativeDB", back_populates="campaign")


class AffiliateClickDB(Base):
    """アフィリエイトクリックログ"""
    __tablename__ = "affiliate_clicks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("affiliate_campaigns.id"), index=True)
    enrollment_token: Mapped[str] = mapped_column(String(64), index=True, nullable=True)
    dealer_id: Mapped[str] = mapped_column(String(36), index=True, nullable=True)
    click_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=_uuid)
    platform: Mapped[str] = mapped_column(String(10), nullable=True)
    clicked_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    converted: Mapped[bool] = mapped_column(Boolean, default=False)

    campaign: Mapped["AffiliateCampaignDB"] = relationship("AffiliateCampaignDB", back_populates="clicks")


class AffiliateConversionDB(Base):
    """アフィリエイトCV（AppsFlyer/Adjustからのポストバック）"""
    __tablename__ = "affiliate_conversions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    click_token: Mapped[str] = mapped_column(String(64), index=True, nullable=True)
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("affiliate_campaigns.id"), index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # appsflyer/adjust/manual
    event_type: Mapped[str] = mapped_column(String(50), default="install")
    revenue_jpy: Mapped[float] = mapped_column(Float, default=0.0)
    raw_payload: Mapped[str] = mapped_column(Text, nullable=True)       # JSONポストバック保存
    converted_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


# ── Android MDM テーブル ───────────────────────────────────────


class AndroidDeviceDB(Base):
    """Androidデバイス（DPC APKがエンロール後に登録）"""
    __tablename__ = "android_devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)   # Android ID
    enrollment_token: Mapped[str] = mapped_column(String(64), index=True, nullable=True)
    fcm_token: Mapped[str] = mapped_column(String(500), nullable=True)             # FCMプッシュトークン
    manufacturer: Mapped[str] = mapped_column(String(100), nullable=True)
    model: Mapped[str] = mapped_column(String(100), nullable=True)
    android_version: Mapped[str] = mapped_column(String(20), nullable=True)
    sdk_int: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")             # active/unenrolled
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    commands: Mapped[list["AndroidCommandDB"]] = relationship("AndroidCommandDB", back_populates="device")


class AndroidCommandDB(Base):
    """Androidデバイスへのコマンドキュー（DPCがポーリングで取得）"""
    __tablename__ = "android_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    device_id: Mapped[str] = mapped_column(String(64), ForeignKey("android_devices.device_id"), index=True)
    command_type: Mapped[str] = mapped_column(String(50))
    # install_apk / add_webclip / show_notification / update_lockscreen / remove_app
    payload: Mapped[str] = mapped_column(Text, nullable=True)     # JSON
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending / sent / acknowledged / failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    acked_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    device: Mapped["AndroidDeviceDB"] = relationship("AndroidDeviceDB", back_populates="commands")


# ── iOS MDM テーブル ──────────────────────────────────────────


class iOSDeviceDB(Base):
    """NanoMDM経由でエンロール済みのiOSデバイス"""
    __tablename__ = "ios_devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    udid: Mapped[str] = mapped_column(String(64), unique=True, index=True)     # iOS UDID
    enrollment_token: Mapped[str] = mapped_column(String(64), index=True, nullable=True)
    push_magic: Mapped[str] = mapped_column(String(256), nullable=True)        # APNs PushMagic
    push_token: Mapped[str] = mapped_column(String(256), nullable=True)        # APNs Device Token
    topic: Mapped[str] = mapped_column(String(256), nullable=True)             # APNs MDM Topic
    device_name: Mapped[str] = mapped_column(String(200), nullable=True)
    device_model: Mapped[str] = mapped_column(String(100), nullable=True)      # iPhone15,2 etc.
    product_name: Mapped[str] = mapped_column(String(100), nullable=True)      # iPhone 15 Pro etc.
    os_version: Mapped[str] = mapped_column(String(20), nullable=True)
    serial_number: Mapped[str] = mapped_column(String(64), index=True, nullable=True)
    enrolled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")         # pending/active/unenrolled
    enrolled_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_checkin_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    commands: Mapped[list["MDMCommandDB"]] = relationship("MDMCommandDB", back_populates="device")


class MDMCommandDB(Base):
    """iOS MDM コマンドキュー（NanoMDM API経由で送信）"""
    __tablename__ = "mdm_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    udid: Mapped[str] = mapped_column(String(64), ForeignKey("ios_devices.udid"), index=True)
    request_type: Mapped[str] = mapped_column(String(100))
    # AddWebClip / InstallConfiguration / RemoveProfile / DeviceLock / ProfileList
    command_uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=_uuid)
    payload: Mapped[str] = mapped_column(Text, nullable=True)   # JSON
    status: Mapped[str] = mapped_column(String(20), default="queued")
    # queued / sent / acknowledged / error / not_now
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    result: Mapped[str] = mapped_column(Text, nullable=True)    # デバイスからの返答JSON

    device: Mapped["iOSDeviceDB"] = relationship("iOSDeviceDB", back_populates="commands")


# ── クリエイティブ管理 ────────────────────────────────────────


class CreativeDB(Base):
    """
    広告クリエイティブ（アフィリエイト案件に紐付く広告素材）

    type:
      text   - タイトル + 説明文のみ（DPCの通知広告）
      image  - 画像URL + タイトル（ロック画面・ウィジェット）
      html5  - HTML5コンテンツ（WebClip LP埋め込み）
      video  - 動画URL + サムネイル（将来対応）
    """
    __tablename__ = "creatives"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("affiliate_campaigns.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(20), default="text")
    # text / image / html5 / video
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=True)
    image_url: Mapped[str] = mapped_column(String(500), nullable=True)
    html_content: Mapped[str] = mapped_column(Text, nullable=True)  # HTML5広告本文
    click_url: Mapped[str] = mapped_column(String(500))             # クリック先URL
    width: Mapped[int] = mapped_column(Integer, nullable=True)       # px
    height: Mapped[int] = mapped_column(Integer, nullable=True)      # px
    status: Mapped[str] = mapped_column(String(20), default="active")
    # active / paused / rejected
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    campaign: Mapped["AffiliateCampaignDB"] = relationship(
        "AffiliateCampaignDB", back_populates="creatives"
    )
    impressions: Mapped[list["MdmImpressionDB"]] = relationship(
        "MdmImpressionDB", back_populates="creative"
    )


class MdmAdSlotDB(Base):
    """
    MDM端末上の広告枠定義（SSP既存のAdSlotDBとは別にMDM専用）

    slot_type:
      lockscreen   - Android ロック画面（CPM ¥500-2000/千回）
      widget       - Android ホーム画面ウィジェット（CPM ¥300-1000/千回）
      notification - FCMプッシュ通知（CPC）
      webclip_ios  - iOS WebClipホーム画面（CPC）

    targeting_json: セグメント条件 例: {"age_group": ["20s","30s"], "platform": "android"}
    """
    __tablename__ = "mdm_ad_slots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    slot_type: Mapped[str] = mapped_column(String(30), index=True)
    floor_price_cpm: Mapped[float] = mapped_column(Float, default=500.0)  # ¥/千回
    targeting_json: Mapped[str] = mapped_column(Text, nullable=True)      # JSON
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    impressions: Mapped[list["MdmImpressionDB"]] = relationship(
        "MdmImpressionDB", back_populates="slot"
    )


class MdmImpressionDB(Base):
    """MDM広告配信インプレッションログ（課金・計測の基点）"""
    __tablename__ = "mdm_impressions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mdm_ad_slots.id"), index=True, nullable=True
    )
    creative_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("creatives.id"), index=True, nullable=True
    )
    device_id: Mapped[str] = mapped_column(String(64), index=True, nullable=True)
    enrollment_token: Mapped[str] = mapped_column(String(64), index=True, nullable=True)
    dealer_id: Mapped[str] = mapped_column(String(36), index=True, nullable=True)
    platform: Mapped[str] = mapped_column(String(10), nullable=True)
    age_group: Mapped[str] = mapped_column(String(10), nullable=True)
    cpm_price: Mapped[float] = mapped_column(Float, default=0.0)
    clicked: Mapped[bool] = mapped_column(Boolean, default=False)
    clicked_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    served_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    slot: Mapped["MdmAdSlotDB"] = relationship("MdmAdSlotDB", back_populates="impressions")
    creative: Mapped["CreativeDB"] = relationship("CreativeDB", back_populates="impressions")
