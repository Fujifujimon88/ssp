"""SQLAlchemy ORMモデル（PostgreSQLテーブル定義）"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Integer, SmallInteger, String, Text, func
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
    slot_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("ad_slots.id"), nullable=True, index=True)
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
    # 代理店（AgencyDB）との紐付け
    agency_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agencies.id"), nullable=True, index=True)
    # 代理店内での店舗番号（1, 2, 3...）
    store_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    region: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    devices: Mapped[list["DeviceDB"]] = relationship("DeviceDB", back_populates="dealer")
    ad_assignments: Mapped[list["StoreAdAssignmentDB"]] = relationship("StoreAdAssignmentDB", back_populates="dealer")


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
    safari_config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: Safari設定
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


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


# ── 同意ログ ─────────────────────────────────────────────────


class ConsentLogDB(Base):
    """MDMエンロール時の同意内容詳細ログ"""
    __tablename__ = "consent_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enrollment_token: Mapped[str] = mapped_column(String(200), index=True)
    dealer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    consent_version: Mapped[str] = mapped_column(String(20), default="1.0")
    consent_items: Mapped[str] = mapped_column(Text, default="[]")  # JSON array of consented items
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    consented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


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
    appsflyer_dev_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    adjust_app_token: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    adjust_event_token: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    advertising_id_field: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    gtm_container_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    vta_window_hours: Mapped[int] = mapped_column(Integer, default=24)  # 24 | 72 | 168
    vta_cpi_rate: Mapped[float] = mapped_column(Float, default=0.5)     # fraction of full CPI rate
    status: Mapped[str] = mapped_column(String(20), default="active")
    # 担当代理店 ID（nullable: 直販キャンペーンは NULL）
    agency_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agencies.id"), nullable=True, index=True)
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
    gaid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)       # Google Advertising ID
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
    video_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    video_duration_sec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    skip_after_sec: Mapped[int] = mapped_column(Integer, default=5)
    creative_type: Mapped[str] = mapped_column(String(20), default="banner")  # banner | video | html5
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
    status: Mapped[str] = mapped_column(String(20), default="served")
    # served / prefetched / expired
    cpm_price: Mapped[float] = mapped_column(Float, default=0.0)
    clicked: Mapped[bool] = mapped_column(Boolean, default=False)
    clicked_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    video_event: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # start|q1|midpoint|q3|complete|skip
    dwell_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    screen_on_count_today: Mapped[Optional[int]] = mapped_column(SmallInteger(), nullable=True)
    dismiss_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    hour_of_day: Mapped[Optional[int]] = mapped_column(SmallInteger(), nullable=True)
    served_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    slot: Mapped["MdmAdSlotDB"] = relationship("MdmAdSlotDB", back_populates="impressions")
    creative: Mapped["CreativeDB"] = relationship("CreativeDB", back_populates="impressions")


class CreativeExperimentDB(Base):
    """A/Bテスト実験定義"""
    __tablename__ = "creative_experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200))
    slot_type: Mapped[str] = mapped_column(String(50))  # lockscreen / widget etc.
    control_creative_id: Mapped[str] = mapped_column(String(36), ForeignKey("creatives.id"))
    variant_creative_id: Mapped[str] = mapped_column(String(36), ForeignKey("creatives.id"))
    traffic_split: Mapped[float] = mapped_column(Float, default=0.5)  # 0.5 = 50/50
    status: Mapped[str] = mapped_column(String(20), default="active")  # active / paused / concluded
    winner: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "control" / "variant" / None
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── CPI課金・ポストバック テーブル ──────────────────────────────


class InstallEventDB(Base):
    """DPC APKが報告したインストール確認イベント（CPI課金起点）"""
    __tablename__ = "install_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id: Mapped[str] = mapped_column(String(255), index=True)
    package_name: Mapped[str] = mapped_column(String(255))
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("affiliate_campaigns.id"), index=True)
    install_ts: Mapped[int] = mapped_column(BigInteger)
    apk_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    billing_status: Mapped[str] = mapped_column(String(20), default="pending")   # pending | billable | paid
    postback_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | success | failed
    postback_attempts: Mapped[int] = mapped_column(Integer, default=0)
    cpi_amount: Mapped[float] = mapped_column(Float, default=0.0)
    attribution_type: Mapped[str] = mapped_column(String(20), default="click")  # click | view_through
    vta_impression_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PostbackLogDB(Base):
    """S2Sポストバック送信ログ"""
    __tablename__ = "postback_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    install_event_id: Mapped[str] = mapped_column(String(36), ForeignKey("install_events.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20))                              # appsflyer | adjust
    request_url: Mapped[str] = mapped_column(Text)
    response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── デバイスプロファイル ──────────────────────────────────────


class DeviceProfileDB(Base):
    """デバイスメタデータストア（BKD-07）"""
    __tablename__ = "device_profiles"

    device_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    os_version: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    carrier: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    mcc_mnc: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    screen_width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    screen_height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ram_gb: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    storage_free_mb: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cohort_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cohort_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ── タイムスロット価格乗数 ────────────────────────────────────


class TimeSlotMultiplierDB(Base):
    """時間帯別eCPM乗数定義（BKD-08）"""
    __tablename__ = "time_slot_multipliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hour_start: Mapped[int] = mapped_column(Integer)          # 0-23
    hour_end: Mapped[int] = mapped_column(Integer)            # 0-23 inclusive
    day_of_week: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 0=Mon, 6=Sun, None=all
    multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)    # e.g. "朝プレミアム"


# ── DSP接続設定・落札ログ（BKD-06） ──────────────────────────────


class DspConfigDB(Base):
    """
    アウトバウンドDSP接続設定（OpenRTB 2.5）

    active=False の間はbid requestを送信しない（申請完了後にTrueへ変更）。
    take_rate: プラットフォームの取り分（0.15 = 15%）
    """
    __tablename__ = "dsp_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    endpoint_url: Mapped[str] = mapped_column(String(500))
    timeout_ms: Mapped[int] = mapped_column(Integer, default=200)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    take_rate: Mapped[float] = mapped_column(Float, default=0.15)  # 15%
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DspWinLogDB(Base):
    """
    DSP落札ログ（収益記録・レポート用）

    platform_revenue_jpy: clearing_price_usd × (1 - take_rate) × 150 JPY/USD
    """
    __tablename__ = "dsp_win_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    impression_id: Mapped[str] = mapped_column(String(36), index=True)
    dsp_name: Mapped[str] = mapped_column(String(100), index=True)
    bid_price_usd: Mapped[float] = mapped_column(Float)
    clearing_price_usd: Mapped[float] = mapped_column(Float)
    platform_revenue_jpy: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


# ── ML 特徴量テーブル（ML-01） ──────────────────────────────────


class UserFeatureDB(Base):
    """
    デバイス単位のML特徴量スナップショット（ML-01）

    毎日02:00 JSTのバッチ処理で過去30日分のmdm_impressionsを集計してupsert。
    プライバシー: device_idは疑似匿名UUID。PII（氏名・電話・メール）は含まない。
    APPI準拠: consent_given=Trueのデバイスのみ対象。
    """
    __tablename__ = "user_features"

    device_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    # 過去30日のimpression集計
    impression_count_30d: Mapped[int] = mapped_column(Integer, default=0)
    click_count_30d: Mapped[int] = mapped_column(Integer, default=0)
    ctr_30d: Mapped[float] = mapped_column(Float, default=0.0)
    avg_dwell_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # CTRが最も高い時間帯（0-23）
    preferred_hour: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 最頻出のdismissタイプ
    dominant_dismiss_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # デバイスプロファイルスナップショット
    carrier: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    feature_version: Mapped[int] = mapped_column(Integer, default=1)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


# ── ML モデルバージョン管理（ML-02） ──────────────────────────────────
class MlModelVersionDB(Base):
    """Two-Towerモデルのバージョン管理テーブル"""
    __tablename__ = "ml_model_versions"
    id               = Column(Integer, primary_key=True)
    version          = Column(String(32), nullable=False, unique=True)
    model_type       = Column(String(32), nullable=False, default="two_tower")
    train_auc        = Column(Float)
    val_auc          = Column(Float)
    offline_ctr_lift = Column(Float)
    tflite_size_mb   = Column(Float)
    tflite_path      = Column(Text)
    is_active        = Column(Boolean, default=False)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())


# ── 代理店ポータル（BKD-11） ──────────────────────────────────────────
class AgencyDB(Base):
    """代理店テーブル"""
    __tablename__ = "agencies"
    id            = Column(Integer, primary_key=True)
    name          = Column(String(128), nullable=False)
    api_key       = Column(String(64), nullable=False, unique=True)
    contact_email = Column(String(256))
    # 代理店ごとのテイクレート（0.0〜1.0）。精算時に使用。
    take_rate     = Column(Float, nullable=False, default=0.175)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


# ── 収益精算（BKD-12） ─────────────────────────────────────────────────
class InvoiceDB(Base):
    """月次請求書テーブル"""
    __tablename__ = "invoices"
    id                  = Column(Integer, primary_key=True)
    period_month        = Column(String(7), nullable=False)   # "2026-03"
    campaign_id         = Column(String(36), ForeignKey("affiliate_campaigns.id"))
    agency_id           = Column(Integer, ForeignKey("agencies.id"), nullable=True)
    gross_revenue_jpy   = Column(Integer, nullable=False, default=0)
    take_rate           = Column(Float,   nullable=False, default=0.175)
    platform_fee_jpy    = Column(Integer, nullable=False, default=0)
    net_payable_jpy     = Column(Integer, nullable=False, default=0)
    cpi_count           = Column(Integer, nullable=False, default=0)
    impression_count    = Column(Integer, nullable=False, default=0)
    video_complete_count= Column(Integer, nullable=False, default=0)
    status              = Column(String(16), nullable=False, default="draft")  # draft/sent/paid
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    sent_at             = Column(DateTime(timezone=True), nullable=True)


# ── 店舗プッシュ通知ログ ─────────────────────────────────────────────


class DealerPushLogDB(Base):
    """店舗からAndroidデバイスへのプッシュ通知送信ログ（月3回制限）"""
    __tablename__ = "dealer_push_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dealer_id: Mapped[str] = mapped_column(String(36), ForeignKey("dealers.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    android_sent: Mapped[int] = mapped_column(Integer, default=0)
    ios_sent: Mapped[int] = mapped_column(Integer, default=0)
    total_devices: Mapped[int] = mapped_column(Integer, default=0)


# ── 店舗別広告配信設定 ──────────────────────────────────────────────


class StoreAdAssignmentDB(Base):
    """
    店舗（DealerDB）ごとの広告配信設定。
    どのアフィリエイトキャンペーン（静止画クリエイティブ含む）を配信するかを定義する。

    priority: 小さい値が優先（1が最高優先）
    status: active / paused
    """
    __tablename__ = "store_ad_assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dealer_id: Mapped[str] = mapped_column(String(36), ForeignKey("dealers.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("affiliate_campaigns.id"), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active / paused
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    dealer: Mapped["DealerDB"] = relationship("DealerDB", back_populates="ad_assignments")
    campaign: Mapped["AffiliateCampaignDB"] = relationship("AffiliateCampaignDB")
