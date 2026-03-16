"""
OpenRTB 2.5 リクエスト/レスポンス データモデル
仕様: https://github.com/InteractiveAdvertisingBureau/openrtb2.x/blob/main/2.5.md
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field
import uuid


# ── Bid Request ────────────────────────────────────────────────

class BannerFormat(BaseModel):
    w: int
    h: int


class Banner(BaseModel):
    w: Optional[int] = None           # 幅(px) — primaryサイズ
    h: Optional[int] = None           # 高さ(px) — primaryサイズ
    format: Optional[list[BannerFormat]] = None  # マルチサイズリスト
    btype: Optional[list[int]] = None # ブロックするバナータイプ
    battr: Optional[list[int]] = None # ブロックする広告属性
    pos: Optional[int] = None         # 広告掲載位置


class Video(BaseModel):
    mimes: list[str]                          # 対応MIMEタイプ
    minduration: Optional[int] = None
    maxduration: Optional[int] = None
    protocols: Optional[list[int]] = None
    w: Optional[int] = None
    h: Optional[int] = None


class Impression(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    banner: Optional[Banner] = None
    video: Optional[Video] = None
    tagid: Optional[str] = None       # 広告スロットID
    bidfloor: float = 0.0             # フロアプライス(CPM, USD)
    bidfloorcur: str = "USD"
    secure: Optional[int] = None      # 1=HTTPS必須


class Publisher(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    domain: Optional[str] = None


class Site(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    domain: Optional[str] = None
    cat: Optional[list[str]] = None   # IABカテゴリ
    publisher: Optional[Publisher] = None
    page: Optional[str] = None        # ページURL
    ref: Optional[str] = None         # リファラー


class Device(BaseModel):
    ua: Optional[str] = None          # User-Agent
    ip: Optional[str] = None
    language: Optional[str] = None
    devicetype: Optional[int] = None  # 1=PC, 2=スマホ, 3=タブレット


class User(BaseModel):
    id: Optional[str] = None
    buyeruid: Optional[str] = None


class BidRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    imp: list[Impression]
    site: Optional[Site] = None
    device: Optional[Device] = None
    user: Optional[User] = None
    at: int = 2                       # 2=セカンドプライスオークション
    tmax: int = 80                    # タイムアウト(ms)
    cur: list[str] = ["USD"]
    bcat: Optional[list[str]] = None  # ブロックするIABカテゴリ


# ── Bid Response ───────────────────────────────────────────────

class Bid(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    impid: str                        # 対応するImpression.id
    price: float                      # 入札価格(CPM, USD)
    adid: Optional[str] = None
    adm: Optional[str] = None        # 広告マークアップ(HTML/VAST)
    adomain: Optional[list[str]] = None
    iurl: Optional[str] = None        # バナー画像URL
    cid: Optional[str] = None        # キャンペーンID
    crid: Optional[str] = None       # クリエイティブID
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
    winning_price: float              # 実際の支払い価格(セカンドプライス)
    dsp_id: str
    creative_id: Optional[str] = None
