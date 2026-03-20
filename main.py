"""
SSP Platform - FastAPI メインアプリケーション（DB + Redis 対応版）

エンドポイント:
  POST /auth/register   ← パブリッシャー新規登録
  POST /auth/token      ← ログイン（JWT取得）
  POST /v1/bid          ← Prebid.jsヘッダービディング
  GET  /v1/win          ← 落札通知
  GET  /v1/ad/{token}   ← 広告クリエイティブ配信
  POST /api/slots       ← 広告スロット作成
  GET  /api/slots       ← スロット一覧
  GET  /api/slots/{id}/tag ← Prebid.jsタグ取得
  GET  /api/reports/daily  ← 日次レポート
  GET  /dashboard       ← 管理ダッシュボード
  GET  /health          ← ヘルスチェック
"""
import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.collector import record_auction, get_daily_stats
from analytics.report import generate_daily_report
from auction.engine import AuctionEngine
from auction.openrtb import (
    Banner, BannerFormat, BidRequest, Impression,
    Publisher as OrtbPublisher, Site,
)
from auth import get_current_publisher_id
from cache import close_redis, delete_win_token, get_win_token, is_redis_connected, set_win_token
from config import settings
from database import Base, engine, get_db
from db_models import AdSlotDB, ImpressionDB, PublisherDB
from dsp.mock_dsp import create_mock_dsps
from mdm.dsp.ssp_node import router as openrtb_router
from mdm.router import router as mdm_router
from publisher.router import router as publisher_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

APP_VERSION = "0.2.4"

auction_engine = AuctionEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from database import AsyncSessionLocal
    from mdm.tasks.health_check import run_health_check

    async def _schedule_health_check():
        while True:
            await asyncio.sleep(3600)  # 1時間ごと
            async with AsyncSessionLocal() as db:
                try:
                    await run_health_check(db)
                except Exception as e:
                    logger.error(f"HealthCheck task failed: {e}")

    # DSP登録（開発: モックDSP / 本番: HttpDSPに差し替え）
    for dsp in create_mock_dsps():
        auction_engine.register_dsp(dsp.dsp_id, dsp)

    hc_task = asyncio.create_task(_schedule_health_check())
    logger.info(f"SSP Platform started | env={settings.app_env} | dsps={auction_engine.registered_dsp_ids()}")
    yield

    hc_task.cancel()

    for dsp in auction_engine._dsps.values():
        if hasattr(dsp, "close"):
            await dsp.close()
    await close_redis()
    logger.info("SSP Platform shutdown")


app = FastAPI(
    title="SSP Platform",
    description="日本ニッチメディア向けSupply-Side Platform",
    version=APP_VERSION,
    lifespan=lifespan,
)

templates = Jinja2Templates(directory="dashboard/templates")
app.include_router(publisher_router)
app.include_router(mdm_router)
app.include_router(openrtb_router)

# Vercel の X-Forwarded-Proto ヘッダーを信頼し request.base_url が https:// を返すようにする
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# ── Basic認証（ダッシュボード保護）─────────────────────────────
_http_basic = HTTPBasic()

def _require_basic_auth(credentials: HTTPBasicCredentials = Depends(_http_basic)) -> HTTPBasicCredentials:
    ok_user = secrets.compare_digest(credentials.username, settings.basic_auth_user)
    ok_pass = secrets.compare_digest(credentials.password, settings.basic_auth_password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="認証が必要です",
            headers={"WWW-Authenticate": 'Basic realm="SSP Admin"'},
        )
    return credentials


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/admin")


# ── ヘッダービディングエンドポイント ───────────────────────────

@app.post("/v1/bid", summary="Prebid.jsからの入札リクエスト")
async def header_bidding(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()

    publisher_id = body.get("publisherId")
    slot_id = body.get("slotId")
    floor_price = float(body.get("floorPrice", settings.floor_price_default))
    sizes = body.get("sizes", [[300, 250]])

    if not publisher_id or not slot_id:
        raise HTTPException(status_code=400, detail="publisherId and slotId required")

    # スロット存在確認 + フロアプライス解決
    slot = await db.scalar(
        select(AdSlotDB).where(AdSlotDB.tag_id == slot_id, AdSlotDB.active == True)
    )
    if slot and slot.floor_price is not None:
        floor_price = slot.floor_price

    imp_id = str(uuid.uuid4())
    banner_formats = [BannerFormat(w=s[0], h=s[1]) for s in sizes if len(s) == 2]
    bid_request = BidRequest(
        imp=[
            Impression(
                id=imp_id,
                banner=Banner(
                    w=sizes[0][0], h=sizes[0][1],
                    format=banner_formats if len(banner_formats) > 1 else None,
                ),
                tagid=slot_id,
                bidfloor=floor_price,
                secure=1,
            )
        ],
        site=Site(
            id=publisher_id,
            publisher=OrtbPublisher(id=publisher_id),
            page=body.get("pageUrl"),
            ref=body.get("referer"),
        ),
        tmax=settings.auction_timeout_ms,
    )

    results = await auction_engine.run_auction(bid_request)
    result = results[0] if results else None

    if not result or not result.winner:
        return JSONResponse({"bids": []})

    # 落札トークン → Redisに保存
    win_token = uuid.uuid4().hex
    await set_win_token(win_token, {
        "adm": result.winner.bid.adm,
        "cpm": result.clearing_price,
        "w": result.winner.bid.w,
        "h": result.winner.bid.h,
        "dsp_id": result.winner.dsp_id,
        "publisher_id": publisher_id,
        "slot_id": slot_id,
    })

    # DBに記録
    actual_slot_id = slot.id if slot else None
    await record_auction(result, slot_id=actual_slot_id, publisher_id=publisher_id, db=db)

    return JSONResponse({
        "bids": [{
            "bidderCode": "ssp_adapter",
            "cpm": result.clearing_price,
            "width": result.winner.bid.w or sizes[0][0],
            "height": result.winner.bid.h or sizes[0][1],
            "ad": result.winner.bid.adm,
            "winToken": win_token,
            "ttl": 30,
            "netRevenue": True,
            "currency": "USD",
            "meta": {"latencyMs": round(result.duration_ms, 1)},
        }]
    })


# ── 落札通知 ───────────────────────────────────────────────────

@app.get("/v1/win", summary="落札通知（win notice）")
async def win_notice(token: str = Query(...), price: Optional[float] = Query(None)):
    data = await get_win_token(token)
    if not data:
        raise HTTPException(status_code=404, detail="Invalid or expired win token")
    logger.info(f"Win | token={token[:8]}... | cpm={data['cpm']:.3f} | dsp={data['dsp_id']}")
    await delete_win_token(token)
    return {"status": "ok"}


# ── 広告クリエイティブ配信 ─────────────────────────────────────

@app.get("/v1/ad/{token}", response_class=HTMLResponse, summary="広告クリエイティブ配信")
async def serve_ad(token: str):
    data = await get_win_token(token)
    if not data:
        raise HTTPException(status_code=404, detail="Invalid ad token")
    adm = data.get("adm", "")
    return HTMLResponse(content=adm)


# ── レポートAPI ────────────────────────────────────────────────

@app.get("/api/reports/daily", summary="日次レポート")
async def daily_report(
    date_str: Optional[str] = Query(None),
    publisher_id: str = Depends(get_current_publisher_id),
    db: AsyncSession = Depends(get_db),
):
    target_date = date.fromisoformat(date_str) if date_str else date.today()
    return await generate_daily_report(publisher_id, db=db, for_date=target_date)


# ── ログインページ ─────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse, summary="パブリッシャーログイン")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


# ── パブリッシャーポータル（要JWT）─────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse, summary="パブリッシャーポータル")
async def dashboard(request: Request, _: HTTPBasicCredentials = Depends(_require_basic_auth)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "version": APP_VERSION})


# ── 管理画面（全パブリッシャー一覧）──────────────────────────

@app.get("/admin", response_class=HTMLResponse, summary="管理画面")
async def admin(request: Request):
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "version": APP_VERSION, "initial_section": ""}
    )


def _admin_section(section: str):
    """管理画面の各セクション用レスポンスを生成するヘルパー"""
    async def handler(request: Request):
        return templates.TemplateResponse(
            "admin.html",
            {"request": request, "version": APP_VERSION, "initial_section": section}
        )
    return handler

# MDM管理セクション個別ルート
app.add_api_route("/mdm-dashboard",        _admin_section("mdm-dashboard"),        response_class=HTMLResponse)
app.add_api_route("/lockscreen-analytics", _admin_section("lockscreen-analytics"),  response_class=HTMLResponse)
app.add_api_route("/store-ad-delivery",    _admin_section("store-ad-delivery"),     response_class=HTMLResponse, dependencies=[Depends(_require_basic_auth)])
app.add_api_route("/dealers",              _admin_section("dealers"),               response_class=HTMLResponse)
app.add_api_route("/campaigns",            _admin_section("campaigns"),             response_class=HTMLResponse)
app.add_api_route("/affiliate-campaigns",  _admin_section("affiliate-campaigns"),   response_class=HTMLResponse)
app.add_api_route("/asp-cv-report",        _admin_section("asp-cv-report"),         response_class=HTMLResponse)
app.add_api_route("/affiliate-points",     _admin_section("affiliate-points"),      response_class=HTMLResponse)
app.add_api_route("/creatives",            _admin_section("creatives"),             response_class=HTMLResponse)
app.add_api_route("/devices",              _admin_section("devices"),               response_class=HTMLResponse)
app.add_api_route("/billing",              _admin_section("billing"),               response_class=HTMLResponse)
app.add_api_route("/wifi-triggers",        _admin_section("wifi-triggers"),         response_class=HTMLResponse)
app.add_api_route("/time-slots",           _admin_section("time-slots"),            response_class=HTMLResponse)
app.add_api_route("/experiments",          _admin_section("experiments"),           response_class=HTMLResponse)
app.add_api_route("/ad-slots",             _admin_section("ad-slots"),              response_class=HTMLResponse)
app.add_api_route("/analytics",            _admin_section("analytics"),             response_class=HTMLResponse)
app.add_api_route("/consent-logs",         _admin_section("consent-logs"),          response_class=HTMLResponse)
app.add_api_route("/invoices",             _admin_section("invoices"),              response_class=HTMLResponse)
app.add_api_route("/ml-pipeline",          _admin_section("ml-pipeline"),           response_class=HTMLResponse)
app.add_api_route("/dsp-configs",          _admin_section("dsp-configs"),           response_class=HTMLResponse)
app.add_api_route("/ios-widget",           _admin_section("ios-widget"),            response_class=HTMLResponse)
app.add_api_route("/agencies",             _admin_section("agencies"),              response_class=HTMLResponse)
# SSP管理セクション
app.add_api_route("/overview",             _admin_section("overview"),              response_class=HTMLResponse)
app.add_api_route("/publishers",           _admin_section("publishers"),            response_class=HTMLResponse)
app.add_api_route("/api-guide",            _admin_section("api-guide"),             response_class=HTMLResponse)


# ── DSP別統計API ───────────────────────────────────────────────

@app.get("/api/dsp/stats", summary="DSP別落札統計（本日）")
async def dsp_stats(
    publisher_id: str = Depends(get_current_publisher_id),
    db: AsyncSession = Depends(get_db),
):
    from datetime import date, datetime
    today = date.today()
    start = datetime.combine(today, datetime.min.time())
    end   = datetime.combine(today, datetime.max.time())

    rows = await db.execute(
        select(
            ImpressionDB.winning_dsp,
            func.count(ImpressionDB.id).label("wins"),
            func.avg(ImpressionDB.clearing_price).label("avg_cpm"),
            func.sum(ImpressionDB.clearing_price).label("total_cpm"),
        )
        .where(
            ImpressionDB.publisher_id == publisher_id,
            ImpressionDB.filled == True,
            ImpressionDB.timestamp >= start,
            ImpressionDB.timestamp <= end,
        )
        .group_by(ImpressionDB.winning_dsp)
        .order_by(func.count(ImpressionDB.id).desc())
    )
    return [
        {
            "dsp_id":  row.winning_dsp,
            "wins":    row.wins,
            "avg_cpm": round(float(row.avg_cpm or 0), 4),
            "revenue": round(float(row.total_cpm or 0) / 1000, 6),
        }
        for row in rows.all()
    ]


# ── レポート履歴API ────────────────────────────────────────────

@app.get("/api/reports/range", summary="期間レポート（複数日）")
async def reports_range(
    days: int = Query(default=7, ge=1, le=90),
    publisher_id: str = Depends(get_current_publisher_id),
    db: AsyncSession = Depends(get_db),
):
    from datetime import date as _date, timedelta
    # 同一セッションを並列使用するとエラーになるため順次実行
    reports = []
    for i in range(days - 1, -1, -1):
        r = await generate_daily_report(publisher_id, db=db, for_date=_date.today() - timedelta(days=i))
        reports.append(r)
    return [r.model_dump() for r in reports]


# ── Ads.txt / sellers.json ─────────────────────────────────────

@app.get("/sellers.json", summary="sellers.json（IAB Tech Lab標準）")
async def sellers_json(db: AsyncSession = Depends(get_db)):
    """DSPが本SSPのインベントリ正当性を確認するための sellers.json"""
    result = await db.execute(
        select(PublisherDB).where(PublisherDB.status == "active")
    )
    publishers = result.scalars().all()
    sellers = [
        {
            "seller_id": pub.id,
            "name": pub.name,
            "domain": pub.domain,
            "seller_type": "PUBLISHER",
            "is_confidential": 0,
        }
        for pub in publishers
    ]
    return {
        "contact_email": "adops@ssp-platform.example.com",
        "version": "1.0",
        "sellers": sellers,
    }


@app.get("/api/publishers/me/ads-txt", response_class=PlainTextResponse, summary="自分のads.txtライン取得")
async def get_my_ads_txt(
    request: Request,
    publisher_id: str = Depends(get_current_publisher_id),
    db: AsyncSession = Depends(get_db),
):
    """パブリッシャーがサイトに設置する ads.txt の1行を返す"""
    pub = await db.get(PublisherDB, publisher_id)
    if not pub:
        raise HTTPException(status_code=404, detail="Publisher not found")
    host = str(request.base_url).replace("https://", "").replace("http://", "").rstrip("/")
    line = f"{host}, {pub.id}, DIRECT, ssp-platform"
    comment = (
        f"# {pub.domain} の ads.txt に以下の1行を追加してください\n"
        f"# ファイルパス: https://{pub.domain}/ads.txt\n\n"
        f"{line}\n"
    )
    return PlainTextResponse(comment)


# ── Apple App Site Association（App Clips用）──────────────────

@app.get("/.well-known/apple-app-site-association", summary="AASA（App Clips対応）")
async def apple_app_site_association():
    """
    App ClipsがNFC/QR起動する際にAppleが検証するファイル。
    iOS-03: App Clips (NFC/QR Launch) 対応。
    app_bundle_id を .env で設定してから App Clipsを公開する。
    Bundle ID形式: TEAMID.jp.platform.ssp
    参照: https://developer.apple.com/documentation/xcode/supporting-associated-domains
    """
    bundle_id = settings.app_bundle_id or "TEAMID.jp.platform.ssp"
    clip_bundle_id = f"{bundle_id}.Clip"
    return JSONResponse(
        content={
            "applinks": {
                "details": [
                    {
                        "appIDs": [bundle_id],
                        "components": [
                            {"/": "/mdm/*"},
                            {"/": "/enroll/*"},
                        ],
                    }
                ]
            },
            "appclips": {
                "apps": [clip_bundle_id]
            },
            "webcredentials": {
                "apps": [bundle_id]
            },
        },
        headers={
            "Content-Type": "application/json",
            "Cache-Control": "max-age=3600",
        },
    )


# ── ヘルスチェック ─────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "dsps": auction_engine.registered_dsp_ids(),
        "redis": is_redis_connected(),
        "env": settings.app_env,
    }

