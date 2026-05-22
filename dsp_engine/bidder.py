"""
dsp_engine コアビッダー。

SSP オークション（main.py の auction_engine）に LocalDspEngineDSP として参加し、
OpenRTB BidRequest を受けて入札する。HTTP ループバックを避けるため、
auction_engine からは同一プロセス内の直接 Python 呼び出しで使う
（Vercel workers:1 環境でのデッドロック回避）。

落札時は main.py が record_dsp_win() を呼び、DspSpendLogDB と予算消化を記録する。
"""
import hashlib
import hmac
import html
import logging
import urllib.parse
import uuid
from collections import namedtuple
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auction.openrtb import Bid, BidRequest, BidResponse, SeatBid
from cache import get_redis
from config import settings
from db_models import DspBidLogDB, DspCampaignDB, DspSpendLogDB
from dsp.base import BaseDSP
from dsp_engine.campaign_manager import (
    get_active_creatives_by_campaign,
    get_all_campaign_stats,
    list_active_campaigns,
)
from dsp_engine.currency import get_jpy_per_usd
from dsp_engine.nbr import (
    NBR_ALL_BUDGET_PACED,
    NBR_BELOW_FLOOR,
    NBR_HOLDOUT,
    NBR_NO_ACTIVE_CAMPAIGNS,
    NBR_NO_IMPRESSION,
    NBR_SHADED_BELOW_FLOOR,
    nbr_label,
)
from dsp_engine.pacing import BudgetPacer
from dsp_engine.reporting import extract_report_dims
from dsp_engine.scoring import compute_bid_cpm_jpy
from dsp_engine.segments import get_segment_multiplier, platform_of
from dsp_engine.shading import compute_shaded_bid, fetch_past_cleared_prices
from utils import utcnow

logger = logging.getLogger(__name__)

_pacer = BudgetPacer()

# ── no-bid 理由コード（nbr）集計カウンタ ────────────────────────────
# 入札ログ DspBidLogDB（全行）とは独立した軽量カウンタ。outcome/nbr 別の
# 当日件数を Redis（不在時はプロセス内 dict）で集計し、admin で即時に
# no-bid 内訳を可視化する。
_NBR_KEY_PREFIX = "dsp:nbr"
_NBR_TTL_SEC = 86400 * 2          # カウンタは2日で失効
_mem_nbr_counts: dict[str, int] = {}  # Redis 不在時のフォールバック {key: count}


def _nbr_code_str(outcome: str, nbr_code: Optional[int]) -> str:
    """outcome/nbr をカウンタ用のコード文字列にする（入札成立は "bid"）。"""
    if outcome == "bid":
        return "bid"
    return str(nbr_code) if nbr_code is not None else "unknown"


async def _incr_nbr_counter(outcome: str, nbr_code: Optional[int]) -> None:
    """outcome/nbr 別の当日カウンタを加算する。"""
    code = _nbr_code_str(outcome, nbr_code)
    day = utcnow().date().isoformat()
    key = f"{_NBR_KEY_PREFIX}:{day}:{code}"
    r = await get_redis()
    if r:
        await r.incr(key)
        await r.expire(key, _NBR_TTL_SEC)
    else:
        _mem_nbr_counts[key] = _mem_nbr_counts.get(key, 0) + 1


async def get_nbr_counts(day=None) -> dict[str, int]:
    """指定日（既定: 当日UTC）の outcome/nbr 別件数 {code: count} を返す。"""
    day_str = (day or utcnow().date()).isoformat()
    prefix = f"{_NBR_KEY_PREFIX}:{day_str}:"
    out: dict[str, int] = {}
    r = await get_redis()
    if r:
        async for key in r.scan_iter(match=f"{prefix}*"):
            val = await r.get(key)
            out[key[len(prefix):]] = int(val) if val else 0
    else:
        for key, val in _mem_nbr_counts.items():
            if key.startswith(prefix):
                out[key[len(prefix):]] = val
    return out


async def _log_bid_decision(
    db: AsyncSession,
    *,
    bid_request: BidRequest,
    source: str,
    imp,
    outcome: str,
    nbr: Optional[int] = None,
    campaign_id: Optional[str] = None,
    bid_price_usd: Optional[float] = None,
    bid_cpm_jpy: Optional[float] = None,
    shaded: bool = False,
    candidate_count: int = 0,
    paced_out_count: int = 0,
) -> None:
    """入札判定を DspBidLogDB に1行記録し、nbr 別カウンタを加算する。

    ログ書き込みの失敗で入札処理を巻き込まないよう、例外は warning のみで握りつぶす
    （ただし沈黙させず必ずログには出す）。
    """
    try:
        db.add(DspBidLogDB(
            request_id=getattr(bid_request, "id", None),
            source=source,
            imp_id=(imp.id if imp is not None else None),
            bidfloor_usd=float(imp.bidfloor) if imp is not None else 0.0,
            outcome=outcome,
            nbr=nbr,
            campaign_id=campaign_id,
            bid_price_usd=bid_price_usd,
            bid_cpm_jpy=bid_cpm_jpy,
            shaded=shaded,
            candidate_count=candidate_count,
            paced_out_count=paced_out_count,
        ))
        await db.commit()
    except Exception as exc:  # ログ失敗で入札を止めない
        logger.warning(f"dsp-engine bid log persist failed: {exc}")
        try:
            await db.rollback()
        except Exception:
            pass
    # カウンタは DB ログとは独立（片方の失敗をもう片方に波及させない）
    try:
        await _incr_nbr_counter(outcome, nbr)
    except Exception as exc:
        logger.warning(f"dsp-engine nbr counter failed: {exc}")


async def get_bid_log_summary(db: AsyncSession, limit: int = 50, day=None) -> dict:
    """直近の入札判定ログと outcome/nbr 別件数を返す（admin 可視化用）。"""
    rows = (await db.scalars(
        select(DspBidLogDB)
        .order_by(DspBidLogDB.logged_at.desc(), DspBidLogDB.id.desc())
        .limit(limit)
    )).all()
    recent = [
        {
            "id": r.id,
            "request_id": r.request_id,
            "source": r.source,
            "outcome": r.outcome,
            "nbr": r.nbr,
            "nbr_label": nbr_label(r.nbr) if r.outcome != "bid" else "(bid)",
            "campaign_id": r.campaign_id,
            "bid_price_usd": r.bid_price_usd,
            "bid_cpm_jpy": r.bid_cpm_jpy,
            "shaded": r.shaded,
            "candidate_count": r.candidate_count,
            "paced_out_count": r.paced_out_count,
            "logged_at": r.logged_at.isoformat() if r.logged_at else None,
        }
        for r in rows
    ]
    breakdown_rows = (await db.execute(
        select(DspBidLogDB.outcome, DspBidLogDB.nbr, func.count())
        .group_by(DspBidLogDB.outcome, DspBidLogDB.nbr)
    )).all()
    breakdown: dict[str, int] = {}
    for outcome, nbr_code, count in breakdown_rows:
        key = _nbr_code_str(outcome, nbr_code)
        breakdown[key] = breakdown.get(key, 0) + int(count)
    return {
        "recent": recent,
        "nbr_breakdown": breakdown,           # 全期間の DB 集計
        "nbr_counters": await get_nbr_counts(day),  # 当日の Redis/メモリ集計
        "campaign_win_rates": await get_campaign_win_rates(db),
    }


async def get_campaign_win_rates(db: AsyncSession) -> dict[str, dict]:
    """campaign 別の win-rate（落札率）を返す。

    bids = DspBidLogDB の outcome="bid" 件数（実際に入札した回数）、
    wins = DspSpendLogDB 件数（落札した回数）。win_rate = wins / bids。
    可視化用であり、入札ロジックには反映しない。
    """
    bid_rows = (await db.execute(
        select(DspBidLogDB.campaign_id, func.count())
        .where(DspBidLogDB.outcome == "bid", DspBidLogDB.campaign_id.is_not(None))
        .group_by(DspBidLogDB.campaign_id)
    )).all()
    win_rows = (await db.execute(
        select(DspSpendLogDB.campaign_id, func.count())
        .group_by(DspSpendLogDB.campaign_id)
    )).all()
    bids_by = {cid: int(n) for cid, n in bid_rows}
    wins_by = {cid: int(n) for cid, n in win_rows}
    out: dict[str, dict] = {}
    for cid in set(bids_by) | set(wins_by):
        bids = bids_by.get(cid, 0)
        wins = wins_by.get(cid, 0)
        out[cid] = {
            "bids": bids,
            "wins": wins,
            "win_rate": (wins / bids) if bids > 0 else 0.0,
        }
    return out


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


# ── クリエイティブ選択 / holdout（#7。入札パス内・純粋関数） ──────────
# 入札パスに外部 I/O を入れない原則（教訓6）に従い、選択・holdout 判定は
# DB 取得済みのクリエイティブリストに対する純粋関数として実装する。

CreativeView = namedtuple(
    "CreativeView", "id title body image_url click_url width height"
)


def _view_from_creative(creative) -> CreativeView:
    """DspCreativeDB を ad markup 生成用のビューに変換する。"""
    return CreativeView(
        id=creative.id, title=creative.title, body=creative.body,
        image_url=creative.image_url, click_url=creative.click_url,
        width=creative.width, height=creative.height,
    )


def _view_from_campaign(campaign) -> CreativeView:
    """DspCreativeDB を持たないキャンペーンのフォールバックビュー（後方互換）。"""
    return CreativeView(
        id=campaign.creative_id, title=campaign.creative_title,
        body=campaign.creative_body, image_url=campaign.creative_image_url,
        click_url=campaign.creative_click_url, width=campaign.creative_width,
        height=campaign.creative_height,
    )


def select_creative(creatives: list, seed: str):
    """active クリエイティブから weight 比例で1つを決定的に選ぶ（純粋関数）。

    seed のハッシュを weight 合計でマッピングするため、同一 seed では常に
    同じクリエイティブを返す（A/B 振り分けの再現性・テスト容易性）。
    選択可能なもの（status=active かつ weight>0）が無ければ None。
    """
    active = sorted(
        (c for c in creatives if c.status == "active" and (c.weight or 0) > 0),
        key=lambda c: c.id,
    )
    if not active:
        return None
    total = sum(c.weight for c in active)
    point = int(hashlib.sha256(f"creative:{seed}".encode()).hexdigest(), 16) % total
    cumulative = 0
    for creative in active:
        cumulative += creative.weight
        if point < cumulative:
            return creative
    return active[-1]


def is_holdout(holdout_rate: float, seed: str) -> bool:
    """holdout バケット判定（純粋関数・決定的）。

    holdout_rate（0.0-1.0）の割合で True を返す。0.0=常に False、
    1.0=常に True。seed のハッシュを 0-1 に正規化して閾値と比較するため、
    同一 seed では判定が安定する。
    """
    if holdout_rate <= 0.0:
        return False
    if holdout_rate >= 1.0:
        return True
    bucket = int(hashlib.sha256(f"holdout:{seed}".encode()).hexdigest(), 16) % 10_000
    return (bucket / 10_000.0) < holdout_rate


def resolve_creative(campaign, creatives: list, seed: str) -> CreativeView:
    """入札に使うクリエイティブを決める。

    DspCreativeDB があれば weight 比例で選択し、無ければキャンペーンの
    インライン素材にフォールバックする（#7 移行前データ・後方互換）。
    """
    selected = select_creative(creatives, seed)
    if selected is not None:
        return _view_from_creative(selected)
    return _view_from_campaign(campaign)


def click_through_url(creative, click_token: str) -> str:
    """最終クリック先 URL（広告主 LP）に dsp_ct（click_token）を付与する。

    クリックトラッカー /dsp-engine/click がクリック記録後にこの URL へ
    リダイレクトする。広告主は LP 着地後の購入計測（AppsFlyer 等）でこの
    dsp_ct を /dsp-engine/conversion へ送り返すことで ROAS が成立する。

    creative は CreativeView（#7）。クリエイティブ単位で LP を切り替えられる。
    """
    base = creative.click_url or "https://advertiser.example.com/lp"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}dsp_ct={urllib.parse.quote(click_token, safe='')}"


def click_destination_url(campaign, creative, click_token: str) -> str:
    """クリックトラッカーのリダイレクト先を解決する（#7）。

    クリックされたクリエイティブ（DspCreativeDB）があればその click_url を、
    無ければキャンペーンのインライン素材にフォールバックして LP を決める。
    """
    view = _view_from_creative(creative) if creative is not None \
        else _view_from_campaign(campaign)
    return click_through_url(view, click_token)


def click_tracker_url(click_token: str) -> str:
    """広告マークアップのクリックリンク先（クリック計測トラッカー）。

    /dsp-engine/click がクリックを記録してから広告主 LP へリダイレクトする。
    """
    base = settings.ssp_endpoint.rstrip("/")
    return f"{base}/dsp-engine/click?ct={urllib.parse.quote(click_token, safe='')}"


def render_adm(creative, imp, click_token: str) -> str:
    """OpenRTB ad markup（クリック可能なバナー HTML）を生成する。

    creative は CreativeView（#7）。入札時に weight 比例で選択された素材。
    """
    w = (imp.banner.w if imp.banner else None) or creative.width or 300
    h = (imp.banner.h if imp.banner else None) or creative.height or 250
    # クリックは計測トラッカー経由（記録 → LP へリダイレクト）
    url = html.escape(click_tracker_url(click_token), quote=True)
    title = html.escape(creative.title or "")

    if creative.image_url:
        img = html.escape(creative.image_url, quote=True)
        inner = (
            f'<img src="{img}" alt="{title}" '
            f'style="width:{w}px;height:{h}px;object-fit:cover;display:block;">'
        )
    else:
        body = html.escape(creative.body or "")
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


def _win_notice_message(ct: str, cid: str, src: str, bid: float) -> str:
    """win notice 署名対象の正規化文字列（bid は 6 桁固定で URL 往復差を吸収）。"""
    return f"{ct}|{cid}|{src}|{float(bid):.6f}"


def sign_win_notice(ct: str, cid: str, src: str, bid: float) -> str:
    """win notice（nurl）の改竄防止署名を生成する（HMAC-SHA256 / settings.secret_key）。

    ${AUCTION_PRICE} マクロで置換される price は署名対象に含められないため、
    署名できるのは ct / cid / src / bid の 4 項目。price 改竄は呼び出し側で
    bid 上限クランプにより別途緩和する。
    """
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        _win_notice_message(ct, cid, src, bid).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_win_notice(sig: Optional[str], ct: str, cid: str, src: str, bid: float) -> bool:
    """win notice の署名を検証する（タイミング安全比較）。sig 欠落は False。"""
    if not sig:
        return False
    expected = sign_win_notice(ct, cid, src, bid)
    return hmac.compare_digest(sig, expected)


def win_notice_url(
    campaign_id: str,
    click_token: str,
    source: str,
    bid_price_usd: float,
    creative_id: Optional[str] = None,
) -> str:
    """OpenRTB 落札通知 URL（nurl）。外部エクスチェンジが落札時に呼ぶ。

    ${AUCTION_PRICE} はエクスチェンジが実落札価格(USD CPM)に置換するマクロ。
    第三者による spend 偽装を防ぐため HMAC 署名(sig)を付与する。

    crid は #7 のレポート用ヒント（落札クリエイティブ）。spend には影響しない
    （改竄されても creative 軸の集計が乱れるだけ）ため署名対象には含めない。
    """
    base = settings.ssp_endpoint.rstrip("/")
    bid = round(bid_price_usd, 6)
    params = {
        "ct": click_token,
        "cid": campaign_id,
        "src": source,
        "bid": bid,
        "sig": sign_win_notice(click_token, campaign_id, source, bid),
    }
    if creative_id:
        params["crid"] = creative_id
    qs = urllib.parse.urlencode(params)
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
        await _log_bid_decision(
            db, bid_request=bid_request, source=source, imp=None,
            outcome="no_bid", nbr=NBR_NO_IMPRESSION,
        )
        return None
    imp = bid_request.imp[0]

    campaigns = await list_active_campaigns(db)
    if not campaigns:
        await _log_bid_decision(
            db, bid_request=bid_request, source=source, imp=imp,
            outcome="no_bid", nbr=NBR_NO_ACTIVE_CAMPAIGNS,
        )
        return None

    # 全キャンペーンの実績・クリエイティブを一括取得（入札パスの N+1 クエリ回避）
    campaign_ids = [c.id for c in campaigns]
    all_stats = await get_all_campaign_stats(db, campaign_ids)
    all_creatives = await get_active_creatives_by_campaign(db, campaign_ids)

    # device セグメント乗数（L1 キャッシュ参照のみ・DB I/O なし）。pCTR を補正する。
    ctr_multiplier = get_segment_multiplier(platform_of(bid_request.device))

    best_campaign = None
    best_bid_cpm_jpy = 0.0
    paced_out_count = 0
    for campaign in campaigns:
        stats = all_stats[campaign.id]
        bid_cpm_jpy = compute_bid_cpm_jpy(campaign, stats, ctr_multiplier=ctr_multiplier)
        # 日予算ペース + 総予算（lifetime spend）の両方をチェック
        if not await _pacer.can_bid(campaign, lifetime_spend_jpy=stats["spend_jpy"]):
            paced_out_count += 1
            continue
        if best_campaign is None or bid_cpm_jpy > best_bid_cpm_jpy:
            best_campaign, best_bid_cpm_jpy = campaign, bid_cpm_jpy

    candidate_count = len(campaigns)

    if best_campaign is None:
        await _log_bid_decision(
            db, bid_request=bid_request, source=source, imp=imp,
            outcome="no_bid", nbr=NBR_ALL_BUDGET_PACED,
            candidate_count=candidate_count, paced_out_count=paced_out_count,
        )
        return None

    # A/B テスト holdout（#7）: 落札候補が確定した後、このキャンペーンの
    # holdout_rate の割合を意図的にノービッドする（incrementality 計測の対照群）。
    # request id をシードに使い、同一リクエストでは判定が安定する。
    request_seed = getattr(bid_request, "id", "") or imp.id or ""
    if is_holdout(best_campaign.holdout_rate or 0.0, f"{best_campaign.id}:{request_seed}"):
        await _log_bid_decision(
            db, bid_request=bid_request, source=source, imp=imp,
            outcome="no_bid", nbr=NBR_HOLDOUT, campaign_id=best_campaign.id,
            candidate_count=candidate_count, paced_out_count=paced_out_count,
        )
        return None

    bid_price_usd = best_bid_cpm_jpy / get_jpy_per_usd()
    if bid_price_usd < imp.bidfloor:
        await _log_bid_decision(
            db, bid_request=bid_request, source=source, imp=imp,
            outcome="no_bid", nbr=NBR_BELOW_FLOOR, campaign_id=best_campaign.id,
            bid_price_usd=bid_price_usd, bid_cpm_jpy=best_bid_cpm_jpy,
            candidate_count=candidate_count, paced_out_count=paced_out_count,
        )
        return None  # フロアプライス（USD CPM）未達

    # first-price(at=1) は入札額がそのまま決済額になるため bid shading で過払いを防ぐ。
    # second-price(at=2) は 2位価格決済のため shading 不要（フルプライス入札）。
    shaded = False
    if bid_request.at == 1:
        rate = get_jpy_per_usd()
        past_cleared_jpy = await fetch_past_cleared_prices(db, best_campaign.id)
        best_bid_cpm_jpy = compute_shaded_bid(
            best_bid_cpm_jpy, past_cleared_jpy, imp.bidfloor * rate
        )
        shaded = True
        bid_price_usd = best_bid_cpm_jpy / rate
        if bid_price_usd < imp.bidfloor:
            await _log_bid_decision(
                db, bid_request=bid_request, source=source, imp=imp,
                outcome="no_bid", nbr=NBR_SHADED_BELOW_FLOOR,
                campaign_id=best_campaign.id, bid_price_usd=bid_price_usd,
                bid_cpm_jpy=best_bid_cpm_jpy, shaded=True,
                candidate_count=candidate_count, paced_out_count=paced_out_count,
            )
            return None  # shading 後にフロア未達ならノービッド

    # 落札キャンペーンの active クリエイティブから weight 比例で1つを選ぶ（#7）。
    # DspCreativeDB を持たないキャンペーンはインライン素材にフォールバック。
    creative = resolve_creative(
        best_campaign, all_creatives.get(best_campaign.id, []), request_seed
    )

    click_token = uuid.uuid4().hex
    bid = Bid(
        impid=imp.id,
        price=round(bid_price_usd, 6),
        adm=render_adm(creative, imp, click_token),
        nurl=win_notice_url(
            best_campaign.id, click_token, source, bid_price_usd,
            creative_id=creative.id,
        ),
        cid=best_campaign.id,                    # 落札処理で campaign を特定
        crid=creative.id,                        # 実クリエイティブID（#7 是正）
        ext={"dsp_click_token": click_token},    # click_token は ext で運ぶ（#7 是正）
        adomain=[_domain_of(creative.click_url)],
        w=(imp.banner.w if imp.banner else None) or creative.width,
        h=(imp.banner.h if imp.banner else None) or creative.height,
    )
    await _log_bid_decision(
        db, bid_request=bid_request, source=source, imp=imp, outcome="bid",
        campaign_id=best_campaign.id, bid_price_usd=round(bid_price_usd, 6),
        bid_cpm_jpy=best_bid_cpm_jpy, shaded=shaded,
        candidate_count=candidate_count, paced_out_count=paced_out_count,
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
    bid_request: Optional[BidRequest] = None,
    creative_id: Optional[str] = None,
) -> DspSpendLogDB:
    """SSP オークションで dsp-engine が落札したときに main.py から呼ぶ。

    落札価格（USD CPM）を円換算し、DspSpendLogDB を記録して予算消化に反映する。
    1インプレッションの実消化額 = 落札 CPM(円) / 1000。

    bid_request を渡すとレポート多次元軸（#6: publisher/app/placement/geo/deal_id）
    を spend log に非正規化記録する。BidRequest を持たない経路（外部エクスチェンジ
    win_notice 等）では None でよく、その場合 publisher 等は null 記録。

    creative_id（#7）は入札時に選択されたクリエイティブ。指定が無い場合は
    キャンペーンの主クリエイティブ（campaign.creative_id）にフォールバックする。

    冪等性: 同一 click_token の落札が既にあれば再記録・再消化しない
    （外部エクスチェンジの nurl 再送による二重計上を防ぐ）。
    """
    existing = await db.scalar(
        select(DspSpendLogDB).where(DspSpendLogDB.click_token == click_token)
    )
    if existing is not None:
        logger.info(f"dsp-engine win skipped (duplicate click_token={click_token})")
        return existing

    rate = get_jpy_per_usd()
    cleared_cpm_jpy = cleared_price_usd * rate
    bid_cpm_jpy = bid_price_usd * rate
    spend_jpy = cleared_cpm_jpy / 1000.0

    # レポート多次元軸: creative_id は入札時の選択を優先（#7）、無ければ campaign の
    # 主クリエイティブにフォールバック。publisher 等は BidRequest から解決（#6）。
    dims = extract_report_dims(bid_request)
    campaign = await db.get(DspCampaignDB, campaign_id)
    resolved_creative_id = creative_id or (
        campaign.creative_id if campaign is not None else None
    )

    log = DspSpendLogDB(
        campaign_id=campaign_id,
        impression_id=impression_id,
        click_token=click_token,
        platform=platform,
        source=source,
        bid_price_jpy=bid_cpm_jpy,
        cleared_price_jpy=cleared_cpm_jpy,
        spend_jpy=spend_jpy,
        creative_id=resolved_creative_id,
        publisher_id=dims["publisher_id"],
        app_id=dims["app_id"],
        placement=dims["placement"],
        geo=dims["geo"],
        deal_id=dims["deal_id"],
    )
    db.add(log)
    try:
        await db.commit()
    except IntegrityError:
        # click_token unique 制約のレース → 既存を返す（消化は加算しない）
        await db.rollback()
        existing = await db.scalar(
            select(DspSpendLogDB).where(DspSpendLogDB.click_token == click_token)
        )
        if existing is not None:
            return existing
        raise
    await _pacer.record_spend(campaign_id, spend_jpy)
    logger.info(
        f"dsp-engine win | campaign={campaign_id} | cleared=¥{cleared_cpm_jpy:.1f}cpm "
        f"| spend=¥{spend_jpy:.3f}"
    )
    # TOCTOU 抑止: 落札確定（消化加算）の直後に総予算超過を判定し、
    # 超過していればキャンペーンを止めて以降の入札を防ぐ。
    await _maybe_exhaust_budget(db, campaign_id)
    return log


async def _maybe_exhaust_budget(db: AsyncSession, campaign_id: str) -> None:
    """総予算（total_budget_jpy）を超過したキャンペーンを budget_exhausted に切り替える。

    can_bid のペーシングは「ある時点のチェック」に過ぎず、bid→win の間に
    同時入札が積み上がると総予算をオーバーランしうる。落札記録の直後に
    実消化（DspSpendLogDB の合計）で再判定し、超過時は status を確定的に止める。
    """
    campaign = await db.get(DspCampaignDB, campaign_id)
    if campaign is None or campaign.status != "active":
        return
    total_budget = float(campaign.total_budget_jpy or 0.0)
    if total_budget <= 0:
        return  # 0以下は無制限
    lifetime_spend = await db.scalar(
        select(func.coalesce(func.sum(DspSpendLogDB.spend_jpy), 0.0))
        .where(DspSpendLogDB.campaign_id == campaign_id)
    ) or 0.0
    if float(lifetime_spend) >= total_budget:
        campaign.status = "budget_exhausted"
        await db.commit()
        logger.warning(
            f"dsp-engine campaign budget exhausted | campaign={campaign_id} "
            f"| lifetime=¥{float(lifetime_spend):.1f} >= total=¥{total_budget:.1f}"
        )
