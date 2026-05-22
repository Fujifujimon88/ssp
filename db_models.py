"""SQLAlchemy ORMモデル（PostgreSQLテーブル定義）"""
import uuid
from datetime import date as date_type, datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, SmallInteger, String, Text, func
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

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
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    # 代理店（AgencyDB）との紐付け
    agency_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agencies.id"), nullable=True, index=True)
    # 代理店内での店舗番号（1, 2, 3...）
    store_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    region: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # CV計測方法デフォルト（NULL=キャンペーン設定に従う / "install" / "app_open"）
    default_cv_trigger: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    login_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True, index=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
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
    enrolled_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/active/unenrolled/opted_out
    re_enroll_count: Mapped[int] = mapped_column(Integer, default=0)
    token_revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    # CV計測方法: "install"=Method1（プリインストール完了でCV）/ "app_open"=Method2（プッシュ通知タップでCV）
    cv_trigger: Mapped[str] = mapped_column(String(20), default="install")
    # 直接ASPポストバックURLテンプレート（A8.net/smaad等）
    # 変数: {device_id} {enrollment_token} {dealer_id} {store_id} {amount} {install_ts} {package_name} {event_type}
    postback_url_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # JANet連携: クリックURL = https://click.j-a-net.jp/{janet_media_id}/{janet_original_id}/{device_id}
    janet_media_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    janet_original_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # smaad / A8.net 等クリックURLテンプレート（{device_id} を置換）
    # 例: https://tr.smaad.net/redirect?zo=745468462&ad=198337123&uid={device_id}
    # 例: https://px.a8.net/a8fly/earnings?a8mat=XXX&uid={device_id}
    click_url_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # ポイント付与設定（デフォルト: 付与しない）
    enable_points: Mapped[bool] = mapped_column(Boolean, default=False)
    point_rate: Mapped[float] = mapped_column(Float, default=1.0)  # 1円=何ポイント
    # 報酬分配設定
    dealer_revenue_rate: Mapped[float] = mapped_column(Float, default=0.0)   # 代理店獲得金額率 (%)
    user_point_rate: Mapped[float] = mapped_column(Float, default=0.0)       # ユーザー獲得ポイント率 (%)
    # トラッキングURL（マクロ: {CLICK_URL} {SESSIONID} {HIMSITE} {NWCLKID} {NWSITEID} {USERID} {CAMPAIGNID}）
    tracking_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # パートナーID フィルタ（カンマ区切り）
    blacklist_partner_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    whitelist_partner_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    clicks: Mapped[list["AffiliateClickDB"]] = relationship("AffiliateClickDB", back_populates="campaign")
    creatives: Mapped[list["CreativeDB"]] = relationship("CreativeDB", back_populates="campaign")


class AffiliateClickDB(Base):
    """アフィリエイトクリックログ"""
    __tablename__ = "affiliate_clicks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("affiliate_campaigns.id"), index=True)
    enrollment_token: Mapped[str] = mapped_column(String(64), index=True, nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)  # Android ID（JANet UserID）
    dealer_id: Mapped[str] = mapped_column(String(36), index=True, nullable=True)
    click_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=_uuid)
    platform: Mapped[str] = mapped_column(String(10), nullable=True)
    clicked_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    converted: Mapped[bool] = mapped_column(Boolean, default=False)

    campaign: Mapped["AffiliateCampaignDB"] = relationship("AffiliateCampaignDB", back_populates="clicks")


class AffiliateConversionDB(Base):
    """アフィリエイトCV（AppsFlyer/Adjustからのポストバック）"""
    __tablename__ = "affiliate_conversions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    click_token: Mapped[str] = mapped_column(String(64), index=True, nullable=True)
    campaign_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("affiliate_campaigns.id"), index=True, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # appsflyer/adjust/manual/janet/skyflag/smaad/a8
    event_type: Mapped[str] = mapped_column(String(50), default="install")
    revenue_jpy: Mapped[float] = mapped_column(Float, default=0.0)
    raw_payload: Mapped[str] = mapped_column(Text, nullable=True)       # JSONポストバック保存
    converted_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    # ASP共通: 2段階通知ステータス（pending/approved/rejected）
    attestation_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    # ASP固有CV ID（冪等性キー: action_id=JANet, cv_id=SKYFLAG）
    asp_action_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    # user_token（ポストバックで受け取った ASP側ユーザーID）
    user_token: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)

    points: Mapped[list["UserPointDB"]] = relationship("UserPointDB", back_populates="conversion")


class UserPointDB(Base):
    """ユーザーへのポイント付与履歴"""
    __tablename__ = "user_points"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_token: Mapped[str] = mapped_column(String(20), index=True)
    conversion_id: Mapped[str] = mapped_column(String(36), ForeignKey("affiliate_conversions.id"), unique=True, index=True)
    points: Mapped[float] = mapped_column(Float, default=0.0)  # 付与ポイント数
    awarded_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    conversion: Mapped["AffiliateConversionDB"] = relationship("AffiliateConversionDB", back_populates="points")


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
    dealer_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)  # 所属代理店
    store_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)   # 所属店舗
    status: Mapped[str] = mapped_column(String(20), default="active")             # active/unenrolled/migrated
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    previous_device_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    migrated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    device_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    migration_suspicious: Mapped[bool] = mapped_column(Boolean, default=False)
    # ASPに渡す不透明ユーザーID（device_idを外部に渡さないためのプロキシ）
    # フォーマット: "UT" + 10桁英数字
    user_token: Mapped[Optional[str]] = mapped_column(String(20), unique=True, nullable=True, index=True)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    acked_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    # アトリビューション追跡: サーバー側でキャンペーン・店舗を保存
    campaign_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    store_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

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
    enrolled_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    last_checkin_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    profile_status: Mapped[str] = mapped_column(String(20), default="unknown")  # unknown/present/missing/re_installing
    last_profile_check_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
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
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True
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
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
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
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True
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
    # アトリビューション強化フィールド
    cv_method: Mapped[str] = mapped_column(String(20), default="install")         # install | app_open | pending_app_open
    app_open_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    dealer_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    store_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)


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
    # ── SSP連携画面（dsp_engine）拡張カラム ──
    # platform_mapping: 外部サービスID → "android"/"ios" の対応（JSON文字列）
    platform_mapping: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # app_mapping: 外部アプリ/スロットID → 内部 dsp_campaigns.id の対応（JSON文字列）
    app_mapping: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    qps_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0=無制限
    last_win_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # 外部エクスチェンジ認証用の共有シークレット（X-DSP-Secret ヘッダーで照合。NULL=認証不要）
    api_secret: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # ── サプライチェーン検証（schain / sellers.json）拡張カラム ──
    # schain_required: True=schain 検証失敗で入札拒否, False=警告のみ, NULL=無検証
    schain_required: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # allowed_asi_domains: 許可する schain asi ドメインのリスト（JSON配列文字列。NULL/空=無制限）
    allowed_asi_domains: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # sellers_json_url: このエクスチェンジの sellers.json 取得URL（突合用）
    sellers_json_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # sellers_json_cache: 取得済み sellers.json のキャッシュ（JSON文字列）
    sellers_json_cache: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # sellers_json_cached_at: sellers.json キャッシュの取得時刻（TTL判定用）
    sellers_json_cached_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    login_id       = Column(String(64), nullable=True, unique=True, index=True)
    hashed_password = Column(String(255), nullable=True)


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


class WifiTriggerRuleDB(Base):
    """
    Wi-Fi SSID 来店トリガールール。

    SSIDに接続したデバイスに対して実行するアクションを定義する。
    action_type: "push" | "line" | "point"
    action_config（例）:
      push  → {"title": "ご来店！", "body": "今日のお得情報", "url": "https://..."}
      line  → {"message": "来店ポイント+100pt獲得！"}
      point → {"points": 100, "reason": "来店ボーナス"}
    """
    __tablename__ = "wifi_trigger_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ssid: Mapped[str] = mapped_column(String(64), index=True)
    dealer_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("dealers.id"), nullable=True, index=True)
    action_type: Mapped[str] = mapped_column(String(32))   # push | line | point
    action_config: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=60)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WifiCheckinLogDB(Base):
    """Wi-Fi SSID 来店ログ（デバイスがSSIDに接続するたびに記録）"""
    __tablename__ = "wifi_checkin_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    ssid: Mapped[str] = mapped_column(String(64))
    dealer_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    actions_fired: Mapped[str] = mapped_column(Text, default="[]")  # JSON: 実行したaction_typeのリスト
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


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


# ── DSP エンジン（広告主向けパフォーマンス DSP / dsp_engine モジュール） ──────────


class DspCampaignDB(Base):
    """
    広告主向け DSP キャンペーン（ROAS 最適化）。

    入札価格 = pCTR × pCVR × avg_purchase_value_jpy × (1 - margin_rate) × 1000（CPM, JPY）
    をフロア/キャップでクランプして算出する。

    クリエイティブは MVP では1キャンペーン1素材としてインライン保持する。
    target_roas は表示用の目標値（入札ロジックには margin_rate のみを使う）。
    """
    __tablename__ = "dsp_campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    advertiser_name: Mapped[str] = mapped_column(String(200))
    campaign_name: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(String(20), default="roas")  # roas（MVP）
    status: Mapped[str] = mapped_column(String(20), default="active")
    # active / paused / budget_exhausted

    # 予算（円）
    daily_budget_jpy: Mapped[float] = mapped_column(Float, default=0.0)   # 0=無制限
    total_budget_jpy: Mapped[float] = mapped_column(Float, default=0.0)   # 0=無制限

    # 入札パラメータ
    target_roas: Mapped[float] = mapped_column(Float, default=300.0)      # 目標ROAS(%)（表示用）
    margin_rate: Mapped[float] = mapped_column(Float, default=0.20)       # プラットフォーム取り分
    bid_floor_jpy: Mapped[float] = mapped_column(Float, default=100.0)    # 最低入札CPM(円)
    bid_cap_jpy: Mapped[float] = mapped_column(Float, default=5000.0)     # 最高入札CPM(円)
    avg_purchase_value_jpy: Mapped[float] = mapped_column(Float, default=3000.0)
    base_ctr: Mapped[float] = mapped_column(Float, default=0.01)          # コールドスタートpCTR
    target_cvr: Mapped[float] = mapped_column(Float, default=0.02)        # コールドスタートpCVR

    # クリエイティブ（MVP: インライン1素材）
    creative_title: Mapped[str] = mapped_column(String(200), default="")
    creative_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    creative_image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    creative_click_url: Mapped[str] = mapped_column(String(500), default="")
    creative_width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    creative_height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    start_date: Mapped[Optional[date_type]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date_type]] = mapped_column(Date, nullable=True)

    # 広告主ログイン（ポータルcookie認証）
    login_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True, index=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DspSpendLogDB(Base):
    """
    DSP エンジンの落札（消化金額）ログ。

    SSP オークションで dsp-engine が落札するたびに1行を記録する。
    click_token は広告マークアップに埋め込まれ、購入CVのアトリビューションに使う。
    ROAS の分母（spend）かつ、購入CV → impression の橋渡し。
    """
    __tablename__ = "dsp_spend_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dsp_campaigns.id"), index=True
    )
    impression_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    click_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=_uuid)
    platform: Mapped[str] = mapped_column(String(10), default="unknown")  # android/ios/unknown
    source: Mapped[str] = mapped_column(String(40), default="ssp-node")   # 入札元(SSP/エクスチェンジ名)
    bid_price_jpy: Mapped[float] = mapped_column(Float, default=0.0)      # 入札CPM(円)
    cleared_price_jpy: Mapped[float] = mapped_column(Float, default=0.0)  # 落札価格CPM(円)
    spend_jpy: Mapped[float] = mapped_column(Float, default=0.0)          # 実消化額(=cleared/1000)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    # クリックは別テーブル DspClickEventDB に記録する（実クリック数・クリック日基準集計のため）


class DspConversionEventDB(Base):
    """
    DSP エンジンの購入CV（ROAS の分子）。

    広告主の AppsFlyer/Adjust の purchase ポストバック先を /dsp-engine/conversion に
    設定してもらい、click_token でアトリビューションする。
    dedup_key（appsflyer_event_id 等）で冪等化する。
    """
    __tablename__ = "dsp_conversion_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dsp_campaigns.id"), index=True
    )
    impression_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    click_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(10), default="unknown")
    source: Mapped[str] = mapped_column(String(40), default="direct")
    # direct / s2s_appsflyer / s2s_adjust
    event_type: Mapped[str] = mapped_column(String(50), default="purchase")
    revenue_jpy: Mapped[float] = mapped_column(Float, default=0.0)
    # 冪等性キー（重複ポストバック排除）
    dedup_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, unique=True, index=True)
    raw_payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attributed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class DspClickEventDB(Base):
    """
    DSP エンジンのクリックイベント（クリック計測 / CTR の分子）。

    クリックトラッカー /dsp-engine/click が呼ばれるたびに1行を記録する。
    同一 click_token（= 同一インプレッション）でも毎回記録するため、
    clicks は「実クリック数」になる。日別レポートは clicked_at 基準で集計する。
    """
    __tablename__ = "dsp_click_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dsp_campaigns.id"), index=True
    )
    click_token: Mapped[str] = mapped_column(String(64), index=True)  # unique でない（複数クリック可）
    impression_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(10), default="unknown")
    source: Mapped[str] = mapped_column(String(40), default="ssp-node")
    clicked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class DspBidLogDB(Base):
    """
    DSP エンジンの入札判定ログ（bid request 1 件につき 1 行）。

    handle_bid_request の全分岐（入札成立 / 各 no-bid 理由）を記録し、
    no-bid 時は理由コード nbr（dsp_engine/nbr.py）を持つ。
    落札ログ DspSpendLogDB が「勝った入札」だけを記録するのに対し、
    本テーブルは「入札しなかった分も含む全 bid request」を記録する。
    """
    __tablename__ = "dsp_bid_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(40), default="ssp-node", index=True)
    imp_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    bidfloor_usd: Mapped[float] = mapped_column(Float, default=0.0)  # imp.bidfloor(USD CPM)
    outcome: Mapped[str] = mapped_column(String(10), default="no_bid")  # bid / no_bid
    # no-bid 理由コード（outcome=no_bid のときのみ。bid 成立時は NULL）
    nbr: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    # 入札成立時の落札候補キャンペーン
    campaign_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    bid_price_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 入札CPM(USD)
    bid_cpm_jpy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)    # 入札CPM(円)
    shaded: Mapped[bool] = mapped_column(Boolean, default=False)  # bid shading 適用有無
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)   # 配信中キャンペーン数
    paced_out_count: Mapped[int] = mapped_column(Integer, default=0)   # 予算で除外された数
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class DspSegmentPerfDB(Base):
    """
    DSP エンジンの device セグメント別パフォーマンス（入札 ML ベースライン）。

    定期バッチ（dsp_engine/segments.py）が DspSpendLogDB（imp）と
    DspClickEventDB（click）から platform 別 CTR を集計し、全体 CTR に対する
    乗数（multiplier）を算出して upsert する。入札時はこの乗数を L1 キャッシュ
    経由で参照し pCTR を補正する（入札パスに DB I/O を入れない）。
    """
    __tablename__ = "dsp_segment_perf"

    segment: Mapped[str] = mapped_column(String(20), primary_key=True)  # android/ios/web/unknown
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    # 全体 CTR に対する乗数。[SEG_MULT_MIN, SEG_MULT_MAX] でクランプ。低サンプル時は 1.0。
    multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
