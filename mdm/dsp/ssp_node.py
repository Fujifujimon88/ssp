"""
ADT-03 — OpenRTB 2.5 インバウンド SSP ノード

外部DSPからの bid request を受け取り、
自社フロア価格と対決させて second-price オークションを実施する。
Win notice は GET /openrtb/win/{auction_id}?price={clearing_price} で受け取る。
"""
import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from db_models import DspWinLogDB, MdmAdSlotDB

logger = logging.getLogger(__name__)

# DSP API キーホワイトリスト（本番は DB 管理に移行）
# 環境変数 OPENRTB_API_KEYS（カンマ区切り）から読む
_api_keys_raw = os.getenv("OPENRTB_API_KEYS", "test-dsp-key-1,test-dsp-key-2")
ALLOWED_API_KEYS = set(k.strip() for k in _api_keys_raw.split(",") if k.strip())

JPY_PER_USD = 150.0  # 固定レート（本番はAPI取得）

router = APIRouter(prefix="/openrtb", tags=["OpenRTB Inbound"])


# ── Pydantic モデル（OpenRTB 2.5 サブセット）────────────────────────


class OrtbBanner(BaseModel):
    w: Optional[int] = None
    h: Optional[int] = None
    format: Optional[list] = None


class OrtbImpression(BaseModel):
    id: str
    banner: Optional[OrtbBanner] = None
    bidfloor: Optional[float] = 0.0
    bidfloorcur: Optional[str] = "JPY"
    tagid: Optional[str] = None


class OrtbApp(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    bundle: Optional[str] = None
    storeurl: Optional[str] = None


class OrtbDevice(BaseModel):
    ua: Optional[str] = None
    ip: Optional[str] = None
    os: Optional[str] = None
    model: Optional[str] = None
    carrier: Optional[str] = None
    language: Optional[str] = "ja"


class OrtbUserData(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    segment: Optional[list] = None


class OrtbUser(BaseModel):
    id: Optional[str] = None
    data: Optional[list[OrtbUserData]] = None


class OrtbBidRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    imp: list[OrtbImpression]
    app: Optional[OrtbApp] = None
    device: Optional[OrtbDevice] = None
    user: Optional[OrtbUser] = None
    at: Optional[int] = 2  # 2 = second price auction
    tmax: Optional[int] = 250


class OrtbBid(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    impid: str
    price: float
    adid: Optional[str] = None
    adm: Optional[str] = None
    adomain: Optional[list[str]] = None
    crid: Optional[str] = None
    w: Optional[int] = None
    h: Optional[int] = None
    nurl: Optional[str] = None  # Win notice URL


class OrtbSeatBid(BaseModel):
    bid: list[OrtbBid]
    seat: Optional[str] = None


class OrtbBidResponse(BaseModel):
    id: str
    seatbid: Optional[list[OrtbSeatBid]] = None
    cur: str = "JPY"
    nbr: Optional[int] = None  # No bid reason


def _verify_api_key(x_openrtb_apikey: str = Header(default="")) -> str:
    if x_openrtb_apikey not in ALLOWED_API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid DSP API key")
    return x_openrtb_apikey


@router.post("/bid", response_model=OrtbBidResponse, summary="OpenRTB 2.5 インバウンド入札受付")
async def receive_bid_request(
    bid_req: OrtbBidRequest,
    db: AsyncSession = Depends(get_db),
    dsp_key: str = Depends(_verify_api_key),
):
    """
    外部DSPからのBidRequestを受け取り、second-priceオークションを実施する。

    - フロア価格 < DSP入札価格 → 落札
    - フロア価格 >= DSP入札価格 → No bid (nbr=3)
    - Clearing price = max(floor, second_highest_bid) ← 今は単一DSPのため floor
    """
    auction_id = bid_req.id
    seatbids = []

    for imp in bid_req.imp:
        # フロア価格を取得（スロット or デフォルト ¥500 CPM）
        floor_jpy = imp.bidfloor or 500.0
        if (imp.bidfloorcur or "JPY").upper() == "USD":
            floor_jpy = imp.bidfloor * JPY_PER_USD

        # 自社クリエイティブの最高eCPMをフロアとして使用
        # MdmAdSlotDB のフロア価格フィールドは floor_price_cpm
        slot = await db.scalar(
            select(MdmAdSlotDB).where(
                MdmAdSlotDB.status == "active",
            ).order_by(MdmAdSlotDB.floor_price_cpm.desc()).limit(1)
        )
        internal_floor_jpy = float(slot.floor_price_cpm) if slot and slot.floor_price_cpm else floor_jpy
        effective_floor = max(floor_jpy, internal_floor_jpy)

        # Win notice URL を生成
        server_url = os.getenv("SSP_ENDPOINT", "https://mdm.example.com")
        nurl = f"{server_url}/openrtb/win/{auction_id}?price=${{AUCTION_PRICE}}&imp_id={imp.id}"

        # クリエイティブを選択してBidResponseを構築
        from mdm.creative.selector import select_creative
        creative = await select_creative(db, slot_type="lockscreen")
        if creative:
            bid = OrtbBid(
                impid=imp.id,
                price=effective_floor,  # 最低落札価格
                adid=str(creative.get("creative_id", "")),
                crid=str(creative.get("creative_id", "")),
                adm=_build_adm(creative, server_url),
                adomain=["platform.jp"],
                w=320, h=480,
                nurl=nurl,
            )
            seatbids.append(OrtbSeatBid(bid=[bid], seat="ssp-platform"))
            logger.info(f"Bid response: auction={auction_id} imp={imp.id} price={effective_floor:.2f}JPY")
        else:
            logger.info(f"No bid: auction={auction_id} imp={imp.id} (no creative)")
            return OrtbBidResponse(id=auction_id, nbr=7)  # nbr=7: No inventory

    if not seatbids:
        return OrtbBidResponse(id=auction_id, nbr=3)  # nbr=3: Technical error

    return OrtbBidResponse(id=auction_id, seatbid=seatbids, cur="JPY")


@router.get("/win/{auction_id}", summary="OpenRTB Win Notice 受信")
async def receive_win_notice(
    auction_id: str,
    price: float = 0.0,
    imp_id: str = "",
    db: AsyncSession = Depends(get_db),
):
    """
    DSPが落札した場合にWin noticeを受け取る。
    Clearing priceを記録してDspWinLogDBに保存する。
    """
    clearing_price_jpy = price
    take_rate = 0.175  # 17.5%（平均）
    platform_revenue_jpy = clearing_price_jpy * (1 - take_rate)

    win_log = DspWinLogDB(
        impression_id=imp_id or auction_id,
        dsp_name="inbound_dsp",
        bid_price_usd=clearing_price_jpy / JPY_PER_USD,
        clearing_price_usd=clearing_price_jpy / JPY_PER_USD,
        platform_revenue_jpy=platform_revenue_jpy,
    )
    db.add(win_log)
    await db.commit()

    logger.info(f"Win notice: auction={auction_id} price={clearing_price_jpy}JPY revenue={platform_revenue_jpy:.0f}JPY")
    return {"ok": True}


def _build_adm(creative: dict, server_url: str) -> str:
    """簡易ADM（Ad Markup）を生成する。実際はHTMLバナーを返す。"""
    title   = creative.get("title", "")
    img_url = creative.get("image_url", "")
    # selector.py が返すキーは click_url（cta_url ではない）
    cta_url = creative.get("click_url") or creative.get("cta_url", "#")
    return (
        f'<a href="{cta_url}" target="_blank">'
        f'<img src="{img_url}" alt="{title}" width="320" height="480" />'
        f'</a>'
    )
