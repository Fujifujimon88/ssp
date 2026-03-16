"""
OpenRTB 2.5 アウトバウンドDSP接続クライアント
空き枠インプレッションをリアルタイム入札にかける。
"""
import asyncio
import logging
import time
import uuid
from typing import Optional

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# JPY/USD 固定レート（後でAPI化）
_JPY_USD_RATE = 150.0

# タイムアウト上限（ms）
_BID_TIMEOUT_MS = 250

# DSP設定（DB管理 or 環境変数）
DSP_CONFIGS: list[dict] = [
    {
        "name": "i-mobile",
        "endpoint": "https://spd.i-mobile.co.jp/bidder/bid",  # 申請後に確定
        "timeout_ms": 200,
        "active": False,  # 申請完了後にTrueへ
    },
    # CyberAgent DSP, Google ADX は後続で追加
]


# ── Pydantic モデル（OpenRTB 2.5 サブセット） ─────────────────────────────


class _Banner(BaseModel):
    w: int
    h: int
    pos: int = 0


class _Imp(BaseModel):
    id: str
    banner: _Banner
    bidfloor: float  # USD
    bidfloorcur: str = "USD"


class _Publisher(BaseModel):
    id: str = "ssp_platform"
    name: str = "SSP Platform"


class _App(BaseModel):
    id: str = "ssp_mdm"
    name: str = "SSP MDM"
    publisher: _Publisher = _Publisher()


class _Geo(BaseModel):
    country: str = "JPN"


class _Device(BaseModel):
    ua: str = ""
    geo: _Geo = _Geo()
    os: str = ""
    osv: str = ""
    model: str = ""
    make: str = ""
    connectiontype: int = 0


class DspBidRequest(BaseModel):
    """OpenRTB 2.5 BidRequest（MDM配信用サブセット）"""

    id: str
    imp: list[_Imp]
    app: _App = _App()
    device: _Device = _Device()
    at: int = 2  # second price auction
    tmax: int = _BID_TIMEOUT_MS


class _Bid(BaseModel):
    id: str
    impid: str
    price: float  # USD CPM
    adid: str = ""
    adm: str = ""
    adomain: list[str] = []
    crid: str = ""
    w: int = 0
    h: int = 0
    nurl: str = ""


class _SeatBid(BaseModel):
    bid: list[_Bid]
    seat: str = ""


class DspBidResponse(BaseModel):
    """OpenRTB 2.5 BidResponse（DSPからのレスポンス）"""

    id: str
    seatbid: list[_SeatBid] = []
    bidid: str = ""
    cur: str = "USD"
    nbr: Optional[int] = None  # no-bid reason


# ── ヘルパー ──────────────────────────────────────────────────────────────


def build_bid_request(
    impression_id: str,
    floor_price_jpy: float,
    device_profile: dict,
    slot_type: str,
    w: int,
    h: int,
) -> dict:
    """
    OpenRTB 2.5 bid request dict を構築する。

    Args:
        impression_id:   SSP側のインプレッションID（OpenRTB imp.id に使用）
        floor_price_jpy: フロア価格（円）
        device_profile:  AndroidDeviceDB / DeviceProfileDB などのデバイス情報 dict
        slot_type:       "lockscreen" | "video" | "notification"
        w:               クリエイティブ幅 (px)
        h:               クリエイティブ高さ (px)

    Returns:
        OpenRTB 2.5 BidRequest dict
    """
    floor_usd = floor_price_jpy / _JPY_USD_RATE

    request = DspBidRequest(
        id=str(uuid.uuid4()),
        imp=[
            _Imp(
                id=impression_id,
                banner=_Banner(w=w, h=h),
                bidfloor=round(floor_usd, 6),
            )
        ],
        device=_Device(
            ua=device_profile.get("ua", ""),
            os=device_profile.get("os", "android"),
            osv=device_profile.get("os_version", ""),
            model=device_profile.get("model", ""),
            make=device_profile.get("manufacturer", ""),
        ),
        tmax=_BID_TIMEOUT_MS,
    )
    return request.model_dump()


async def _send_single_bid(
    dsp_config: dict,
    bid_request: dict,
) -> Optional[float]:
    """
    単一DSPへのbid request送信。タイムアウト処理付き。

    Returns:
        落札価格（USD CPM）、またはNone（no-bid / タイムアウト / エラー）
    """
    timeout_sec = dsp_config.get("timeout_ms", 200) / 1000.0
    endpoint = dsp_config["endpoint"]
    dsp_name = dsp_config["name"]

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(
                endpoint,
                json=bid_request,
                headers={"Content-Type": "application/json"},
            )

        # HTTP 204 = no content (no bid)
        if resp.status_code == 204 or not resp.content:
            logger.debug(f"DSP {dsp_name}: no bid (204)")
            return None

        if resp.status_code != 200:
            logger.warning(
                f"DSP {dsp_name}: unexpected status {resp.status_code}"
            )
            return None

        data = resp.json()
        bid_response = DspBidResponse.model_validate(data)

        if not bid_response.seatbid:
            return None

        # 最高入札価格を取得
        best_price: Optional[float] = None
        for seat in bid_response.seatbid:
            for bid in seat.bid:
                if best_price is None or bid.price > best_price:
                    best_price = bid.price

        return best_price

    except httpx.TimeoutException:
        logger.debug(f"DSP {dsp_name}: timeout after {timeout_sec}s")
        return None
    except Exception as exc:
        logger.warning(f"DSP {dsp_name}: bid error — {exc}")
        return None


async def request_bid(
    impression_id: str,
    floor_price_jpy: float,
    device_profile: dict,
    slot_type: str,
    creative_w: int,
    creative_h: int,
) -> Optional[dict]:
    """
    全設定済みDSPに並列でbid requestを送信し、フロア価格以上の最高入札を返す。

    Args:
        impression_id:   SSP側インプレッションID
        floor_price_jpy: フロア価格（円）
        device_profile:  デバイス情報 dict（manufacturer, model, os_version など）
        slot_type:       "lockscreen" | "video" | "notification"
        creative_w:      広告幅 (px)
        creative_h:      広告高さ (px)

    Returns:
        {"dsp_name": str, "bid_price_usd": float, "clearing_price_usd": float}
        またはNone（入札なし・全DSP非アクティブ・タイムアウト）
    """
    active_dsps = [d for d in DSP_CONFIGS if d.get("active", False)]
    if not active_dsps:
        return None

    bid_request = build_bid_request(
        impression_id=impression_id,
        floor_price_jpy=floor_price_jpy,
        device_profile=device_profile,
        slot_type=slot_type,
        w=creative_w,
        h=creative_h,
    )

    floor_usd = floor_price_jpy / _JPY_USD_RATE
    start = time.monotonic()

    # 並列ファンアウト：全DSPへ同時送信、上限 _BID_TIMEOUT_MS ms
    tasks = [
        asyncio.wait_for(
            _send_single_bid(dsp_config, bid_request),
            timeout=_BID_TIMEOUT_MS / 1000.0,
        )
        for dsp_config in active_dsps
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    elapsed_ms = (time.monotonic() - start) * 1000
    logger.debug(f"DSP fanout completed in {elapsed_ms:.1f}ms")

    # フロア以上の最高入札を選ぶ（second-price: clearing = bid price）
    best_price: Optional[float] = None
    best_dsp: Optional[str] = None

    for dsp_config, result in zip(active_dsps, results):
        if isinstance(result, Exception):
            logger.debug(
                f"DSP {dsp_config['name']}: exception in gather — {result}"
            )
            continue
        if result is not None and result >= floor_usd:
            if best_price is None or result > best_price:
                best_price = result
                best_dsp = dsp_config["name"]

    if best_price is None or best_dsp is None:
        return None

    return {
        "dsp_name": best_dsp,
        "bid_price_usd": best_price,
        "clearing_price_usd": best_price,  # second-price auction (DSP handles internally)
    }
