"""portal/router.py — 代理店・店舗ポータル ログイン認証"""
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from auth import (
    PORTAL_COOKIE_NAME,
    create_portal_token,
    decode_portal_token,
    get_portal_session,
    verify_password,
)
from config import settings
from database import get_db
from db_models import AgencyDB, DealerDB, DeviceDB, MdmImpressionDB, DealerPushLogDB, CampaignDB

router = APIRouter(tags=["Portal"])
templates = Jinja2Templates(directory="dashboard/templates")

_COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7日


def _set_portal_cookie(response: RedirectResponse, token: str) -> RedirectResponse:
    response.set_cookie(
        PORTAL_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.app_env != "development",
        max_age=_COOKIE_MAX_AGE,
    )
    return response


# ── ログインページ ────────────────────────────────────────────────

@router.get("/portal/login", response_class=HTMLResponse, include_in_schema=False)
async def portal_login_page(request: Request):
    token = request.cookies.get(PORTAL_COOKIE_NAME)
    if token:
        payload = decode_portal_token(token)
        if payload:
            dest = "/agency-portal" if payload["type"] == "agency" else "/dealer-portal"
            return RedirectResponse(dest, status_code=303)
    return templates.TemplateResponse("portal_login.html", {"request": request, "error": None})


@router.post("/portal/login", include_in_schema=False)
async def portal_login(
    request: Request,
    login_id: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    # 代理店を検索
    agency = await db.scalar(select(AgencyDB).where(AgencyDB.login_id == login_id))
    if agency and agency.hashed_password and verify_password(password, agency.hashed_password):
        token = create_portal_token("agency", str(agency.id))
        return _set_portal_cookie(RedirectResponse("/agency-portal", status_code=303), token)

    # 店舗を検索
    dealer = await db.scalar(select(DealerDB).where(DealerDB.login_id == login_id))
    if dealer and dealer.hashed_password and verify_password(password, dealer.hashed_password):
        token = create_portal_token("dealer", dealer.id)
        return _set_portal_cookie(RedirectResponse("/dealer-portal", status_code=303), token)

    return templates.TemplateResponse(
        "portal_login.html",
        {"request": request, "error": "IDまたはパスワードが正しくありません"},
        status_code=200,
    )


@router.get("/portal/logout", include_in_schema=False)
async def portal_logout():
    response = RedirectResponse("/portal/login", status_code=303)
    response.delete_cookie(PORTAL_COOKIE_NAME)
    return response


# ── 代理店ポータル ────────────────────────────────────────────────

@router.get("/agency-portal", response_class=HTMLResponse, include_in_schema=False)
async def agency_portal_page(
    request: Request,
    session: dict = Depends(get_portal_session),
    db: AsyncSession = Depends(get_db),
):
    if session["type"] != "agency":
        return RedirectResponse("/dealer-portal", status_code=303)

    agency = await db.get(AgencyDB, int(session["sub"]))
    if not agency:
        response = RedirectResponse("/portal/login", status_code=303)
        response.delete_cookie(PORTAL_COOKIE_NAME)
        return response

    return templates.TemplateResponse(
        "agency_portal.html",
        {
            "request": request,
            "agency_name": agency.name,
            "api_key": agency.api_key,
        },
    )


# ── 店舗ポータル ──────────────────────────────────────────────────

async def _get_or_create_dealer_campaign(dealer_id: str, db: AsyncSession) -> CampaignDB:
    campaign = await db.scalar(
        select(CampaignDB).where(CampaignDB.dealer_id == dealer_id, CampaignDB.status == "active")
    )
    if campaign is None:
        campaign = CampaignDB(
            name=f"dealer_{dealer_id}",
            dealer_id=dealer_id,
            webclips="[]",
            status="active",
        )
        db.add(campaign)
        await db.flush()
    return campaign


@router.get("/dealer-portal", response_class=HTMLResponse, include_in_schema=False)
async def dealer_portal_page(
    request: Request,
    session: dict = Depends(get_portal_session),
    db: AsyncSession = Depends(get_db),
):
    if session["type"] != "dealer":
        return RedirectResponse("/agency-portal", status_code=303)

    dealer = await db.get(DealerDB, session["sub"])
    if not dealer or dealer.status != "active":
        response = RedirectResponse("/portal/login", status_code=303)
        response.delete_cookie(PORTAL_COOKIE_NAME)
        return response

    from mdm.affiliate.billing import get_dealer_monthly_report

    now = datetime.now(timezone.utc)

    # 月次レポート
    report = await get_dealer_monthly_report(db, dealer.id, now.year, now.month)

    # 本日統計
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_impressions = await db.scalar(
        select(func.count(MdmImpressionDB.id)).where(
            MdmImpressionDB.dealer_id == dealer.id,
            MdmImpressionDB.served_at >= today_start,
        )
    ) or 0
    today_clicks = await db.scalar(
        select(func.count(MdmImpressionDB.id)).where(
            MdmImpressionDB.dealer_id == dealer.id,
            MdmImpressionDB.clicked == True,  # noqa: E712
            MdmImpressionDB.served_at >= today_start,
        )
    ) or 0
    today_revenue = await db.scalar(
        select(func.sum(MdmImpressionDB.cpm_price)).where(
            MdmImpressionDB.dealer_id == dealer.id,
            MdmImpressionDB.served_at >= today_start,
        )
    ) or 0.0
    today_ctr = round(today_clicks / today_impressions * 100, 1) if today_impressions > 0 else 0.0

    # プッシュ通知残り回数
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    push_used = await db.scalar(
        select(func.count(DealerPushLogDB.id)).where(
            DealerPushLogDB.dealer_id == dealer.id,
            DealerPushLogDB.sent_at >= month_start,
        )
    ) or 0
    push_remaining = max(0, 3 - push_used)

    # WebClip一覧
    campaign = await _get_or_create_dealer_campaign(dealer.id, db)
    await db.commit()
    webclips = json.loads(campaign.webclips or "[]")

    base = str(request.base_url).rstrip("/")

    return templates.TemplateResponse(
        "dealer_portal.html",
        {
            "request": request,
            "dealer_name": dealer.name,
            "store_code": dealer.store_code,
            "api_key": dealer.api_key,
            "today_impressions": today_impressions,
            "today_clicks": today_clicks,
            "today_ctr": today_ctr,
            "today_revenue": today_revenue,
            "report": report,
            "qr_url": f"{base}/mdm/qr/{dealer.store_code}",
            "portal_url": f"{base}/mdm/portal?dealer={dealer.id}",
            "push_remaining": push_remaining,
            "webclips_json": json.dumps(webclips),
        },
    )
