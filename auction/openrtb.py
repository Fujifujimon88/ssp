"""
OpenRTB 2.6 相当 リクエスト/レスポンス データモデル
仕様: https://github.com/InteractiveAdvertisingBureau/openrtb2.x/blob/main/2.6.md

2.5 から 2.6 相当へ拡張: App / Source(schain) / Regs(GPP) / user.ext.eids /
Bid.burl・lurl / Imp.pmp・Deal / Video 詳細 / Device 拡張。
全追加フィールドは Optional のため 2.5 形式のリクエストと後方互換。
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field
import uuid


# ── 共有・被参照モデル（依存順に先行定義） ─────────────────────

class BannerFormat(BaseModel):
    w: int
    h: int


class Geo(BaseModel):
    lat: Optional[float] = None       # 緯度
    lon: Optional[float] = None       # 経度
    country: Optional[str] = None     # ISO 3166-1 alpha-3 (e.g. "JPN")
    region: Optional[str] = None      # ISO 3166-2 (e.g. "JP-13")
    city: Optional[str] = None
    zip: Optional[str] = None
    type: Optional[int] = None        # 1=GPS/LOC, 2=IP, 3=USER
    utcoffset: Optional[int] = None   # UTC分offset


class Deal(BaseModel):
    id: str                           # ディール識別子
    bidfloor: float = 0.0
    bidfloorcur: str = "USD"
    at: Optional[int] = None          # オークションタイプ上書き
    wseat: Optional[list[str]] = None # 許可バイヤーシートリスト
    wadomain: Optional[list[str]] = None


class Pmp(BaseModel):
    private_auction: int = 0          # 0=open, 1=deals only
    deals: Optional[list[Deal]] = None


# ── Bid Request ────────────────────────────────────────────────

class Banner(BaseModel):
    w: Optional[int] = None           # 幅(px) — primaryサイズ
    h: Optional[int] = None           # 高さ(px) — primaryサイズ
    format: Optional[list[BannerFormat]] = None  # マルチサイズリスト
    btype: Optional[list[int]] = None # ブロックするバナータイプ
    battr: Optional[list[int]] = None # ブロックする広告属性
    pos: Optional[int] = None         # 広告掲載位置
    api: Optional[list[int]] = None   # 対応APIフレームワーク


class Video(BaseModel):
    mimes: list[str]                          # 対応MIMEタイプ
    minduration: Optional[int] = None
    maxduration: Optional[int] = None
    protocols: Optional[list[int]] = None
    w: Optional[int] = None
    h: Optional[int] = None
    startdelay: Optional[int] = None          # -1=midroll generic, -2=postroll generic
    placement: Optional[int] = None           # 1=in-stream, 2=in-banner, 3=in-article, 4=in-feed, 5=interstitial
    plcmt: Optional[int] = None               # 2.6新区分 1=instream, 2=accompanying, 3=interstitial, 4=standalone
    linearity: Optional[int] = None           # 1=linear, 2=non-linear
    skip: Optional[int] = None                # 0=not skippable, 1=skippable
    skipmin: Optional[int] = None             # スキップ可能になるまでの動画長(秒)
    skipafter: Optional[int] = None           # スキップ可能になるまでの秒数
    sequence: Optional[int] = None
    api: Optional[list[int]] = None           # 1=VPAID1, 2=VPAID2, 3=MRAID1, 4=ORMMA, 5=MRAID2
    battr: Optional[list[int]] = None         # ブロックする広告属性
    maxextended: Optional[int] = None
    minbitrate: Optional[int] = None
    maxbitrate: Optional[int] = None
    playbackmethod: Optional[list[int]] = None  # 1=auto+sound, 2=auto+mute, 3=click+sound, 4=mouse over
    pos: Optional[int] = None                 # 広告掲載位置


class Impression(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    banner: Optional[Banner] = None
    video: Optional[Video] = None
    tagid: Optional[str] = None       # 広告スロットID
    bidfloor: float = 0.0             # フロアプライス(CPM, USD)
    bidfloorcur: str = "USD"
    secure: Optional[int] = None      # 1=HTTPS必須
    pmp: Optional[Pmp] = None         # Private Marketplace / Deal 情報


class Publisher(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    domain: Optional[str] = None


class App(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    bundle: Optional[str] = None      # iOS: "com.example.app" / Android: bundle ID
    domain: Optional[str] = None
    storeurl: Optional[str] = None    # アプリストアURL
    cat: Optional[list[str]] = None   # IABカテゴリ
    publisher: Optional[Publisher] = None
    ver: Optional[str] = None         # アプリバージョン
    paid: Optional[int] = None        # 0=free, 1=paid


class Site(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    domain: Optional[str] = None
    cat: Optional[list[str]] = None   # IABカテゴリ
    publisher: Optional[Publisher] = None
    page: Optional[str] = None        # ページURL
    ref: Optional[str] = None         # リファラー


class SupplyChainNode(BaseModel):
    asi: str                          # 広告システムドメイン (e.g. "exchange1.com")
    sid: str                          # 当該広告システムのアカウントID
    hp: int                           # 1=このノードを必ず通過, 0=not required
    rid: Optional[str] = None         # 当該ノードでのリクエストID
    name: Optional[str] = None
    domain: Optional[str] = None


class SupplyChain(BaseModel):
    complete: int                     # 0=不完全, 1=完全
    nodes: list[SupplyChainNode]
    ver: str = "1.0"


class SourceExt(BaseModel):
    schain: Optional[SupplyChain] = None


class Source(BaseModel):
    fd: Optional[int] = None          # 0=交換機決定, 1=上流決定
    tid: Optional[str] = None         # トランザクションID
    pchain: Optional[str] = None      # 支払いIDチェーン
    ext: Optional[SourceExt] = None   # schain を内包


class RegsExt(BaseModel):
    gdpr: Optional[int] = None        # 0=not in scope, 1=in scope


class Regs(BaseModel):
    coppa: Optional[int] = None       # 1=COPPA対象
    gpp: Optional[str] = None         # GPP文字列 (OpenRTB 2.6)
    gpp_sid: Optional[list[int]] = None  # 適用GPPセクションID一覧
    ext: Optional[RegsExt] = None


class Device(BaseModel):
    ua: Optional[str] = None          # User-Agent
    ip: Optional[str] = None
    language: Optional[str] = None
    devicetype: Optional[int] = None  # 1=PC, 2=スマホ, 3=タブレット
    geo: Optional[Geo] = None         # 地理情報
    ifa: Optional[str] = None         # 広告ID (IDFA / AAID)
    make: Optional[str] = None        # メーカー (e.g. "Apple")
    model: Optional[str] = None       # 機種名 (e.g. "iPhone")
    os: Optional[str] = None          # OS名 (e.g. "iOS")
    osv: Optional[str] = None         # OSバージョン (e.g. "17.4")
    lmt: Optional[int] = None         # 0=トラッキング許可, 1=制限
    connectiontype: Optional[int] = None  # 0=不明, 1=Ethernet, 2=WiFi, 4=CELL
    carrier: Optional[str] = None     # キャリア名


class UserIdEntry(BaseModel):
    id: Optional[str] = None
    atype: Optional[int] = None       # 1=device, 3=user
    ext: Optional[dict] = None


class ExtendedId(BaseModel):
    source: str                       # ID発行元 (e.g. "adserver.org", "liveramp.com")
    uids: list[UserIdEntry]
    ext: Optional[dict] = None


class UserExt(BaseModel):
    eids: Optional[list[ExtendedId]] = None  # Extended IDs (UID2.0 / RampID 等)
    consent: Optional[str] = None     # TCF consent string


class User(BaseModel):
    id: Optional[str] = None
    buyeruid: Optional[str] = None
    yob: Optional[int] = None         # 生年(4桁)
    gender: Optional[str] = None      # "M", "F", "O"
    ext: Optional[UserExt] = None     # eids を内包


class BidRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    imp: list[Impression]
    site: Optional[Site] = None
    app: Optional[App] = None         # Site の代替（モバイルアプリ枠）
    device: Optional[Device] = None
    user: Optional[User] = None
    source: Optional[Source] = None   # schain を含む供給元情報
    regs: Optional[Regs] = None       # gpp / gpp_sid を含むプライバシー規制
    at: int = 2                       # 2=セカンドプライスオークション
    tmax: int = 80                    # タイムアウト(ms)
    cur: list[str] = ["USD"]
    bcat: Optional[list[str]] = None  # ブロックするIABカテゴリ
    badv: Optional[list[str]] = None  # ブロックする広告主ドメイン


# ── Bid Response ───────────────────────────────────────────────

class Bid(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    impid: str                        # 対応するImpression.id
    price: float                      # 入札価格(CPM, USD)
    adid: Optional[str] = None
    adm: Optional[str] = None        # 広告マークアップ(HTML/VAST)
    nurl: Optional[str] = None       # 落札通知URL（OpenRTB win notice。${AUCTION_PRICE}マクロ対応）
    burl: Optional[str] = None       # 請求通知URL（課金確定時。OpenRTB 2.6）
    lurl: Optional[str] = None       # 損失通知URL（落札失敗時。OpenRTB 2.6）
    adomain: Optional[list[str]] = None
    iurl: Optional[str] = None        # バナー画像URL
    cid: Optional[str] = None        # キャンペーンID
    crid: Optional[str] = None       # クリエイティブID
    cat: Optional[list[str]] = None  # 広告のIABカテゴリ
    cattax: Optional[int] = None     # カテゴリ分類体系 (1=IAB1.0, 2=IAB2.0)
    w: Optional[int] = None
    h: Optional[int] = None


class SeatBid(BaseModel):
    bid: list[Bid]
    seat: Optional[str] = None        # DSP識別子


class BidResponse(BaseModel):
    id: str                           # BidRequest.idと対応
    seatbid: Optional[list[SeatBid]] = None
    bidid: Optional[str] = None
    cur: str = "USD"
    nbr: Optional[int] = None         # No-bid理由コード


# ── 落札通知 ───────────────────────────────────────────────────

class WinNotice(BaseModel):
    auction_id: str
    imp_id: str
    winning_price: float              # 実際の支払い価格（at に従う: at=1 first-price / at=2 second-price）
    dsp_id: str
    creative_id: Optional[str] = None
