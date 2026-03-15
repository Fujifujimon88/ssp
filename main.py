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
import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.collector import record_auction, get_daily_stats
from analytics.report import generate_daily_report
from auction.engine import AuctionEngine
from auction.openrtb import (
    Banner, BidRequest, Impression,
    Publisher as OrtbPublisher, Site,
)
from auth import get_current_publisher_id
from cache import close_redis, delete_win_token, get_win_token, is_redis_connected, set_win_token
from config import settings
from database import Base, engine, get_db
from db_models import AdSlotDB, ImpressionDB, PublisherDB
from dsp.mock_dsp import create_mock_dsps
from publisher.router import router as publisher_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

APP_VERSION = "0.2.0"

auction_engine = AuctionEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # DBテーブル作成（開発用。本番はAlembicマイグレーションを使う）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # DSP登録（開発: モックDSP / 本番: HttpDSPに差し替え）
    for dsp in create_mock_dsps():
        auction_engine.register_dsp(dsp.dsp_id, dsp)

    logger.info(f"SSP Platform started | env={settings.app_env} | dsps={auction_engine.registered_dsp_ids()}")
    yield

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
    bid_request = BidRequest(
        imp=[
            Impression(
                id=imp_id,
                banner=Banner(w=sizes[0][0], h=sizes[0][1]),
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
    actual_slot_id = slot.id if slot else slot_id
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
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ── 管理画面（全パブリッシャー一覧）──────────────────────────

@app.get("/admin", response_class=HTMLResponse, summary="管理画面")
async def admin(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PublisherDB).order_by(PublisherDB.created_at.desc()))
    publishers = result.scalars().all()
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "publishers": publishers}
    )


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
    import asyncio as _asyncio
    results = []
    tasks = [
        generate_daily_report(publisher_id, db=db, for_date=_date.today() - timedelta(days=i))
        for i in range(days - 1, -1, -1)
    ]
    reports = await _asyncio.gather(*tasks)
    return [r.model_dump() for r in reports]


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

