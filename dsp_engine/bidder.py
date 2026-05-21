"""
dsp_engine コアビッダー。

SSP オークション（main.py の auction_engine）に LocalDspEngineDSP として参加し、
OpenRTB BidRequest を受けて入札する。HTTP ループバックを避けるため、
auction_engine からは同一プロセス内の直接 Python 呼び出しで使う
（Vercel workers:1 環境でのデッドロック回避）。

落札時は main.py が record_dsp_win() を呼び、DspSpendLogDB と予算消化を記録する。
"""
import html
import logging
import urllib.parse
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from auction.openrtb import Bid, BidRequest, BidResponse, SeatBid
from config import settings
from db_models import DspSpendLogDB
from dsp.base import BaseDSP
from dsp_engine.campaign_manager import get_campaign_stats, list_active_campaigns
from dsp_engine.currency import get_jpy_per_usd
from dsp_engine.pacing import BudgetPacer
from dsp_engine.scoring import compute_bid_cpm_jpy

logger = logging.getLogger(__name__)

_pacer = BudgetPacer()


class LocalDspEngineDSP(BaseDSP):
    """auction_engine に登録する DSP アダプター（同一プロセス直接呼び出し）。"""

    DSP_ID = "dsp-engine"

    def __init__(self):
        super().__init__(dsp_id=self.DSP_ID, name="DSP Engine", endpoint="local://dsp-engine")

    async def send_bid_request(self, bid_request: BidRequest) -> Optional[BidResponse]:
        # バックグラウンド／オークション文脈から呼ばれるため自前でセッションを張る
        from database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            try:
                return await handle_bid_request(bid_request, db)
            except Exception as exc:  # オークションを巻き込まないよう握りつぶしてノービッド
                logger.error(f"dsp-engine bid failed: {exc}")
                return None


def _domain_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc or "advertiser.example.com"
    except Exception:
        return "advertiser.example.com"


def click_through_url(campaign, click_token: str) -> str:
    """クリック先 URL に dsp_ct（click_token）を付与する。

    広告主は LP 着地後の購入計測（AppsFlyer 等）でこの dsp_ct を
    /dsp-engine/conversion へ送り返すことで ROAS アトリビューションが成立する。
    """
    base = campaign.creative_click_url or "https://advertiser.example.com/lp"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}dsp_ct={urllib.parse.quote(click_token, safe='')}"


def render_adm(campaign, imp, click_token: str) -> str:
    """OpenRTB ad markup（クリック可能なバナー HTML）を生成する。"""
    w = (imp.banner.w if imp.banner else None) or campaign.creative_width or 300
    h = (imp.banner.h if imp.banner else None) or campaign.creative_height or 250
    url = html.escape(click_through_url(campaign, click_token), quote=True)
    title = html.escape(campaign.creative_title or "")

    if campaign.creative_image_url:
        img = html.escape(campaign.creative_image_url, quote=True)
        inner = (
            f'<img src="{img}" alt="{title}" '
            f'style="width:{w}px;height:{h}px;object-fit:cover;display:block;">'
        )
    else:
        body = html.escape(campaign.creative_body or "")
        inner = (
            f'<div style="width:{w}px;height:{h}px;background:#0b5cff;color:#fff;'
            f'display:flex;flex-direction:column;align-items:center;justify-content:center;'
            f'font-family:sans-serif;text-align:center;padding:8px;box-sizing:border-box;">'
            f'<strong style="font-size:15px;">{title}</strong>'
            f'<span style="font-size:12px;margin-top:4px;">{body}</span></div>'
        )
    return (
        f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
        f'style="text-decoration:none;display:inline-block;">{inner}</a>'
    )


def win_notice_url(campaign_id: str, click_token: str, source: str, bid_price_usd: float) -> str:
    """OpenRTB 落札通知 URL（nurl）。外部エクスチェンジが落札時に呼ぶ。

    ${AUCTION_PRICE} はエクスチェンジが実落札価格(USD CPM)に置換するマクロ。
    """
    base = settings.ssp_endpoint.rstrip("/")
    qs = urllib.parse.urlencode({
        "ct": click_token,
        "cid": campaign_id,
        "src": source,
        "bid": round(bid_price_usd, 6),
    })
    return f"{base}/dsp-engine/win?{qs}&price=${{AUCTION_PRICE}}"


async def handle_bid_request(
    bid_request: BidRequest, db: AsyncSession, source: str = "ssp-node"
) -> Optional[BidResponse]:
    """BidRequest を評価し、最も入札価格の高いキャンペーンで BidResponse を返す。

    1. status="active" のキャンペーンを取得
    2. 各キャンペーンの入札 CPM(円) を算出（scoring）
    3. 予算ペース内（pacing）のキャンペーンに絞り、最高値を選ぶ
    4. USD CPM に換算し、フロアプライス未達ならノービッド
    5. click_token 付き ad markup と落札通知 URL(nurl) を持つ Bid を返す

    source: 入札元（"ssp-node"=自社SSPオークション / 外部エクスチェンジ名）。
    """
    if not bid_request.imp:
        return None
    imp = bid_request.imp[0]

    campaigns = await list_active_campaigns(db)
    if not campaigns:
        return None

    best_campaign = None
    best_bid_cpm_jpy = 0.0
    for campaign in campaigns:
        stats = await get_campaign_stats(db, campaign.id)
        bid_cpm_jpy = compute_bid_cpm_jpy(campaign, stats)
        if not await _pacer.can_bid(campaign):
            continue  # 予算ペース超過 → このキャンペーンはスキップ
        if best_campaign is None or bid_cpm_jpy > best_bid_cpm_jpy:
            best_campaign, best_bid_cpm_jpy = campaign, bid_cpm_jpy

    if best_campaign is None:
        return None

    bid_price_usd = best_bid_cpm_jpy / get_jpy_per_usd()
    if bid_price_usd < imp.bidfloor:
        return None  # フロアプライス（USD CPM）未達

    click_token = uuid.uuid4().hex
    bid = Bid(
        impid=imp.id,
        price=round(bid_price_usd, 6),
        adm=render_adm(best_campaign, imp, click_token),
        nurl=win_notice_url(best_campaign.id, click_token, source, bid_price_usd),
        cid=best_campaign.id,      # 落札処理で campaign を特定するため
        crid=click_token,          # 落札処理で click_token を引き継ぐため
        adomain=[_domain_of(best_campaign.creative_click_url)],
        w=(imp.banner.w if imp.banner else None) or best_campaign.creative_width,
        h=(imp.banner.h if imp.banner else None) or best_campaign.creative_height,
    )
    return BidResponse(
        id=bid_request.id,
        seatbid=[SeatBid(bid=[bid], seat=LocalDspEngineDSP.DSP_ID)],
        cur="USD",
    )


async def record_dsp_win(
    db: AsyncSession,
    *,
    campaign_id: str,
    click_token: str,
    impression_id: Optional[str],
    cleared_price_usd: float,
    bid_price_usd: float,
    platform: str = "unknown",
    source: str = "ssp-node",
) -> DspSpendLogDB:
    """SSP オークションで dsp-engine が落札したときに main.py から呼ぶ。

    落札価格（USD CPM）を円換算し、DspSpendLogDB を記録して予算消化に反映する。
    1インプレッションの実消化額 = 落札 CPM(円) / 1000。
    """
    rate = get_jpy_per_usd()
    cleared_cpm_jpy = cleared_price_usd * rate
    bid_cpm_jpy = bid_price_usd * rate
    spend_jpy = cleared_cpm_jpy / 1000.0

    log = DspSpendLogDB(
        campaign_id=campaign_id,
        impression_id=impression_id,
        click_token=click_token,
        platform=platform,
        source=source,
        bid_price_jpy=bid_cpm_jpy,
        cleared_price_jpy=cleared_cpm_jpy,
        spend_jpy=spend_jpy,
    )
    db.add(log)
    await db.commit()
    await _pacer.record_spend(campaign_id, spend_jpy)
    logger.info(
        f"dsp-engine win | campaign={campaign_id} | cleared=¥{cleared_cpm_jpy:.1f}cpm "
        f"| spend=¥{spend_jpy:.3f}"
    )
    return log
